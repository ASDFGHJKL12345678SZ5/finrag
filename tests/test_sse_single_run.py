"""SSE 单跑回归测试（P1-1 修复的守卫，需要本地 postgres）。

事故记录：/ask 的 SSE handler 原本 astream(updates) 之后又 await ask()
把完整图重跑一遍拿终态——一次 SSE 请求触发两轮 LLM 调用（成本延迟双倍），
且 /ask 与 /ask/sync 并发时 checkpointer 两路写可能把"未初始化"的旧状态读回来。
修复：stream_mode=["updates","values"] 一次跑完拿两种数据。

本测试的阵法：把旧的第二跑函数 ask 换成"一被调用就炸"的探针——
只要有人双跑，/ask 立刻以 error 事件暴露；单跑则正常完成。
"""
import json
import os

os.environ["EMBEDDING_MODE"] = "mock"
os.environ["LLM_MODE"] = "mock"

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.winloop import ensure_selector_loop
from app.rag import store
from app.rag.corpus import build_corpus
from app.rag.ingest import ingest_report

ensure_selector_loop()

pytestmark = pytest.mark.skipif(
    os.environ.get("FINRAG_SKIP_DB") == "1",
    reason="FINRAG_SKIP_DB=1：跳过需要数据库的集成测试",
)


def _reset_and_seed(n: int = 10, seed: int = 42):
    async def body():
        await store.init_store()
        try:
            pool = store.get_pool()
            async with pool.acquire() as conn:
                await conn.execute("TRUNCATE rag.documents CASCADE")
            reports = build_corpus(n_reports=n, seed=seed)
            for r in reports:
                await ingest_report(r)
            return reports
        finally:
            await store.close_store()

    return asyncio.run(body())


def test_sse_does_not_run_graph_twice():
    reports = _reset_and_seed()
    fact = reports[0].facts[0]

    from app.api import main as api_main

    def _tripwire(*a, **k):  # 旧的第二跑入口：被调用即失败
        raise AssertionError("SSE handler 又调用了 ask()——双跑事故复发！")

    original = api_main.ask
    api_main.ask = _tripwire
    try:
        with TestClient(api_main.app) as client, client.stream(
            "POST", "/ask",
            json={"question": fact.question, "session_id": "sse-single-run"},
        ) as resp:
            assert resp.status_code == 200
            events = []
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
    finally:
        api_main.ask = original

    # 没有 error 事件（探针没炸）+ 有终态（answer 或 refuse）
    assert not [e for e in events if e.get("event") == "error"], events
    assert [e for e in events if e.get("event") in ("answer", "refuse")], events
    # 事件流里有全量引用字段（values 模式的产物）
    terminal = [e for e in events if e.get("event") in ("answer", "refuse")][-1]
    if terminal["event"] == "answer":
        assert "citations" in terminal
