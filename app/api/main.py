"""FinRAG API：健康检查 + 问答（SSE 流式 / 同步两种形态）。

SSE 事件契约（扁平 dict，无 data 包装——与 DataCrew 保持一致）：
    {"event": "node",    "node": "retrieve", "latency_ms": 12.3, "detail": {...}}
    {"event": "status",  "status": "done", "retry_count": 1}
    {"event": "answer",  "answer": "..."}                      # 生成节点的流式中继
    {"event": "answer",  "answer": "...", "citations": [...], "total_ms": 90.2}  # 终态（带全字段）
    {"event": "refuse",  "reason": "...", "verdicts": [...]}     # 证据不足拒答（终态）
    {"event": "error",   "detail": "..."}

为什么流式：问答链路 3-6 个节点、每节点几十到几百毫秒，流式让用户
第一时间看到"在检索/在生成"，而不是对着转圈等 2 秒。评测脚本用同步
接口（/ask/sync），前端/DEMO 用 SSE。
"""
from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.winloop import ensure_selector_loop
from app.rag import store
from app.rag.graph import ask, build_rag_graph

log = get_logger("api")

ensure_selector_loop()

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动/关闭钩子（最终审查修复：@app.on_event 在 FastAPI 0.10x 已废弃，
    lifespan 是官方替代；本地 embedding 模式在启动时预热，首问不付下载/初始化）"""
    await store.init_store()
    if get_settings().embedding_mode == "local":
        # 本地 ONNX 模型首次加载约数秒——放启动处，首问不背这个延迟
        from app.rag.embeddings import get_embedder

        get_embedder()
        log.info("api.embedder_warm")
    log.info("api.startup", extra={"context": {"store": "ready"}})
    yield
    await store.close_store()


app = FastAPI(
    title="FinRAG",
    version="0.5.0",
    description="金融研报知识库问答",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    session_id: str = "default"


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "finrag"}


@app.post("/ask/sync")
async def ask_sync(req: AskRequest) -> dict:
    """同步问答：跑完整状态机，返回终态（评测脚本用这个）。"""
    started = time.perf_counter()
    final = await ask(req.question, session_id=req.session_id)
    total_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        "question": req.question,
        "answer": final.get("answer", ""),
        "status": final.get("status", "failed"),
        "citations": _citations(final),
        "verdicts": final.get("verdicts") or [],
        "retry_count": final.get("retry_count", 0),
        "trace": final.get("trace") or [],
        "total_ms": total_ms,
    }


@app.post("/ask")
async def ask_stream(req: AskRequest) -> StreamingResponse:
    """SSE 流式问答：节点级事件 + 终态（answer 或 refuse）。"""

    async def gen() -> AsyncIterator[str]:
        started = time.perf_counter()
        final: dict = {}
        try:
            graph = build_rag_graph()
            # stream_mode=["updates","values"]：一次跑完同时拿两种数据——
            #   updates：每节点增量（node/status/answer 事件从这里推）
            #   values ：每节点后的全量快照（最后一个就是终态，citations 要全量）
            # 曾经 astream(updates) 之后又 await ask() 重跑一遍完整图：
            # 一次 SSE 请求触发两轮 LLM 调用（成本翻倍、延迟翻倍）。
            # 修复：一次 astream 同时拿 updates（节点事件）和 values（终态），
            # 由 test_sse_single_run 的"一调用就炸"探针守护回归（README D7）。
            # 本图未挂 checkpointer（无会话持久化诉求），状态只活在单次请求里。
            async for mode, chunk in graph.astream(
                {
                    "question": req.question,
                    "session_id": req.session_id,
                    "original_question": req.question,
                    "status": "running",
                    "retry_count": 0,
                },
                stream_mode=["updates", "values"],
            ):
                if mode == "values":
                    final = chunk
                    continue
                for node, delta in chunk.items():
                    for step in delta.get("trace") or []:
                        yield _sse({"event": "node", "node": node, **step})
                    if "status" in delta:
                        yield _sse({
                            "event": "status",
                            "status": delta["status"],
                            "retry_count": delta.get("retry_count", 0),
                        })
                    if "answer" in delta:
                        yield _sse({"event": "answer", "answer": delta["answer"]})
            total_ms = round((time.perf_counter() - started) * 1000, 1)
            if final.get("status") == "done":
                yield _sse({
                    "event": "answer",
                    "answer": final.get("answer", ""),
                    "citations": _citations(final),
                    "total_ms": total_ms,
                })
            else:
                yield _sse({
                    "event": "refuse",
                    "reason": "证据不足或引用校验未通过",
                    "verdicts": final.get("verdicts") or [],
                    "answer": final.get("answer", ""),
                    "total_ms": total_ms,
                })
        except Exception as exc:
            log.exception("api.ask_failed")
            yield _sse({"event": "error", "detail": str(exc)})

    return StreamingResponse(gen(), media_type="text/event-stream")


def _citations(final: dict) -> list[dict]:
    return [
        {
            "citation_id": c["citation_id"],
            "source": c["source"],
            "section_path": c.get("section_path", ""),
            "text": c["text"],
            "score": round(c["score"], 4),
        }
        for c in (final.get("retrieved") or [])
    ]


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}" + chr(10) + chr(10)
