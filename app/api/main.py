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

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.winloop import ensure_selector_loop
from app.infra.cache import close_redis, token_bucket
from app.infra.observability import end_trace, record_span, start_trace
from app.infra.observability import shutdown as obs_shutdown
from app.rag import store
from app.rag.graph import ask, get_rag_graph

log = get_logger("api")

ensure_selector_loop()

# SSE 整体超时（FR-5）：单次问答最多跑这么久，超时给前端一个 error 终态，
# 而不是让连接永远挂着（客户端断开/代理超时后再写会静默失败）。
SSE_TIMEOUT_S = 120.0

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
    obs_shutdown()
    await close_redis()
    await store.close_store()


app = FastAPI(
    title="FinRAG",
    version="0.5.0",
    description="金融研报知识库问答",
    lifespan=lifespan,
)
# CORS 收紧到前端源（FR-8）：`*` 让任意站点都能在用户浏览器里发起调用；
# 允许的源从配置来，dev 默认 Vite 5173，生产用 CORS_ORIGINS 覆盖。
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
    allow_credentials=False,
)


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    session_id: str = "default"


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> str:
    """API Key 鉴权 + 每 key 令牌桶限流（FR-8）。

    顺序很重要：先鉴权再限流——未授权的请求不应该消耗任何 key 的桶。
    Redis 故障时 token_bucket fail-open（限流器挂了不拒全部请求），
    由日志与 /health 暴露降级状态。
    """
    settings = get_settings()
    if settings.api_key_set and (not x_api_key or x_api_key not in settings.api_key_set):
        raise HTTPException(status_code=401, detail="invalid api key")
    key = x_api_key or "anonymous"
    allowed = await token_bucket(
        key, capacity=settings.rate_limit_per_min, refill_per_min=settings.rate_limit_per_min
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": "60"},
        )
    return key


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "finrag"}


@app.post("/ask/sync")
async def ask_sync(req: AskRequest, _key: str = Depends(require_api_key)) -> dict:
    """同步问答：跑完整状态机，返回终态（评测脚本用这个）。"""
    started = time.perf_counter()
    trace = start_trace(req.question, req.session_id)
    try:
        final = await ask(req.question, session_id=req.session_id)
    except Exception:
        # 错误详情只进日志，不出接口（FR-8）：异常原文可能带 DSN / 路径 / key
        log.exception("api.ask_sync_failed")
        end_trace(trace, {"status": "failed"}, "failed")
        raise HTTPException(status_code=500, detail="服务内部错误，请稍后重试") from None
    total_ms = round((time.perf_counter() - started) * 1000, 1)

    for step in final.get("trace") or []:
        record_span(
            trace,
            name=str(step.get("node", "node")),
            metadata={"latency_ms": step.get("latency_ms")},
            output=step.get("detail") or {},
        )
    end_trace(
        trace,
        output={
            "status": final.get("status"),
            "answer": final.get("answer", ""),
            "refusal_reason": final.get("refusal_reason", ""),
        },
        status=final.get("status"),
    )
    return {
        "question": req.question,
        "answer": final.get("answer", ""),
        "status": final.get("status", "failed"),
        "refusal_reason": final.get("refusal_reason", ""),
        "citations": _citations(final),
        "verdicts": final.get("verdicts") or [],
        "retry_count": final.get("retry_count", 0),
        "trace": final.get("trace") or [],
        "total_ms": total_ms,
    }


@app.post("/ask")
async def ask_stream(req: AskRequest, _key: str = Depends(require_api_key)) -> StreamingResponse:
    """SSE 流式问答：节点级事件 + 终态（answer 或 refuse）。"""
    request_id = uuid.uuid4().hex[:8]

    async def gen() -> AsyncIterator[str]:
        started = time.perf_counter()
        final: dict = {}
        trace = start_trace(req.question, req.session_id)
        try:
            graph = get_rag_graph()
            # stream_mode=["updates","values"]：一次跑完同时拿两种数据——
            #   updates：每节点增量（node/status/answer 事件从这里推）
            #   values ：每节点后的全量快照（最后一个就是终态，citations 要全量）
            # 曾经 astream(updates) 之后又 await ask() 重跑一遍完整图：
            # 一次 SSE 请求触发两轮 LLM 调用（成本翻倍、延迟翻倍）。
            # 修复：一次 astream 同时拿 updates（节点事件）和 values（终态），
            # 由 test_sse_single_run 的"一调用就炸"探针守护回归（README D7）。
            # 本图未挂 checkpointer（无会话持久化诉求），状态只活在单次请求里。
            async with asyncio.timeout(SSE_TIMEOUT_S):
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
                            # Langfuse span（FR-8）：检索/守卫/生成/校验逐节点
                            record_span(
                                trace,
                                name=str(node),
                                metadata={"latency_ms": step.get("latency_ms")},
                                output=step.get("detail") or {},
                            )
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
            end_trace(
                trace,
                output={
                    "status": final.get("status"),
                    "answer": final.get("answer", ""),
                    "refusal_reason": final.get("refusal_reason", ""),
                },
                status=final.get("status"),
            )
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
                    # reason 保持前端既有契约（拒答横幅直接展示）；
                    # 有确定性归因时给出具体原因（D13 充分性守卫的三档），
                    # 前端零改动——只是文案从"一律证据不足"变成可解释的拒答
                    "reason": _refusal_text(final.get("refusal_reason", "")),
                    "refusal_reason": final.get("refusal_reason", ""),
                    "verdicts": final.get("verdicts") or [],
                    "answer": final.get("answer", ""),
                    "total_ms": total_ms,
                })
        except TimeoutError:
            log.warning(
                "api.ask_timeout",
                extra={"context": {"timeout_s": SSE_TIMEOUT_S, "request_id": request_id}},
            )
            end_trace(trace, {"status": "timeout"}, "timeout")
            yield _sse({
                "event": "error",
                "detail": f"请求超时（>{int(SSE_TIMEOUT_S)}s），请缩小问题范围后重试",
            })
        except Exception:
            # FR-8：异常原文只进日志（可能带 DSN/路径/key），接口只给编号
            log.exception("api.ask_failed", extra={"context": {"request_id": request_id}})
            end_trace(trace, {"status": "failed"}, "failed")
            yield _sse({
                "event": "error",
                "detail": f"服务内部错误，请稍后重试（错误编号 {request_id}）",
            })

    return StreamingResponse(gen(), media_type="text/event-stream")


# 拒答归因码 -> 用户可读文案（与 app/rag/sufficiency.py、graph.py 的
# refusal_reason 一一对应）。没有归因码时回退通用话术——契约不变。
_REFUSAL_TEXTS = {
    "retrieval_empty": "未检索到与问题相关的研报内容",
    "year_not_in_evidence": "问题所问年份在知识库中没有数据",
    "metric_not_in_evidence": "知识库不覆盖该指标，无法回答",
    "future_not_in_evidence": "问题涉及未来/预测数据，知识库只有历史数据",
}


def _refusal_text(reason: str) -> str:
    return _REFUSAL_TEXTS.get(reason, "证据不足或引用校验未通过")


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
