"""FR-8 API 安全与可观测：鉴权 / 限流 / CORS / 错误脱敏 / Langfuse 降级。"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

os.environ["EMBEDDING_MODE"] = "mock"
os.environ["LLM_MODE"] = "mock"

import pytest
from fastapi.testclient import TestClient

from app.api import main as api_main
from app.core.winloop import ensure_selector_loop
from app.rag import store
from app.rag.corpus import build_corpus
from app.rag.ingest import ingest_report

ensure_selector_loop()

KEY = {"X-API-Key": "dev-key-001"}


@pytest.fixture(autouse=True)
def allow_rate_limit(monkeypatch):
    """除限流用例之外，避免 Redis 桶状态跨测试累积（鉴权与限流要分开测）。"""

    async def _allow(*_args, **_kwargs):
        return True

    monkeypatch.setattr(api_main, "token_bucket", _allow)


def _seed() -> None:
    async def body():
        await store.init_store()
        try:
            pool = store.get_pool()
            async with pool.acquire() as conn:
                await conn.execute("TRUNCATE rag.documents CASCADE")
        await store.reset_lexical_index()
            for report in build_corpus(n_reports=5, seed=42):
                await ingest_report(report)
        finally:
            await store.close_store()

    asyncio.run(body())


def test_缺少_api_key_返回401():
    with TestClient(api_main.app) as client:
        r = client.post("/ask/sync", json={"question": "宁德润能2025年的营业收入是多少？"})
    assert r.status_code == 401
    assert "invalid api key" in r.json()["detail"]


def test_错误_api_key_返回401():
    with TestClient(api_main.app) as client:
        r = client.post(
            "/ask/sync",
            json={"question": "宁德润能2025年的营业收入是多少？"},
            headers={"X-API-Key": "wrong-key"},
        )
    assert r.status_code == 401


def test_有效_api_key_正常问答():
    _seed()
    with TestClient(api_main.app) as client:
        r = client.post(
            "/ask/sync",
            json={"question": "宁德润能2025年的营业收入是多少？", "session_id": "sec-ok"},
            headers=KEY,
        )
    assert r.status_code == 200
    assert r.json()["status"] in ("done", "insufficient")


def test_限流拒绝返回429(monkeypatch):
    async def _deny(*_args, **_kwargs):
        return False

    monkeypatch.setattr(api_main, "token_bucket", _deny)
    with TestClient(api_main.app) as client:
        r = client.post(
            "/ask/sync",
            json={"question": "宁德润能2025年的营业收入是多少？"},
            headers=KEY,
        )
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "60"


def test_cors_只允许配置的前端源():
    with TestClient(api_main.app) as client:
        allowed = client.get("/health", headers={"Origin": "http://localhost:5173"})
        denied = client.get("/health", headers={"Origin": "http://evil.example.com"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in denied.headers


def test_同步接口错误详情不泄漏(monkeypatch):
    async def _boom(*_args, **_kwargs):
        raise RuntimeError("postgresql://postgres:secret@db/finrag 连接失败")

    monkeypatch.setattr(api_main, "ask", _boom)
    with TestClient(api_main.app) as client:
        r = client.post(
            "/ask/sync",
            json={"question": "宁德润能2025年的营业收入是多少？"},
            headers=KEY,
        )
    assert r.status_code == 500
    assert "secret" not in r.text
    assert r.json()["detail"] == "服务内部错误，请稍后重试"


def test_sse错误详情不泄漏(monkeypatch):
    class BoomGraph:
        async def astream(self, *_args, **_kwargs):
            raise RuntimeError("api_key=sk-secret-123 无效")
            yield  # pragma: no cover - 让函数成为 async generator

    monkeypatch.setattr(api_main, "get_rag_graph", lambda: BoomGraph())
    with TestClient(api_main.app) as client, client.stream(
        "POST",
        "/ask",
        json={"question": "任意问题", "session_id": "sec-sse"},
        headers=KEY,
    ) as resp:
        events = [
            json.loads(line[6:])
            for line in resp.iter_lines()
            if line.startswith("data: ")
        ]
    errors = [e for e in events if e.get("event") == "error"]
    assert errors
    assert "sk-secret" not in errors[0]["detail"]
    assert "错误编号" in errors[0]["detail"]


def test_token_bucket_原子限流():
    from app.infra.cache import token_bucket

    key = f"test-{uuid.uuid4().hex}"

    async def body():
        assert await token_bucket(key, capacity=2, refill_per_min=2) is True
        assert await token_bucket(key, capacity=2, refill_per_min=2) is True
        assert await token_bucket(key, capacity=2, refill_per_min=2) is False

    asyncio.run(body())


def test_langfuse_未配置时零成本降级():
    from app.infra import observability as obs

    # .env 里可能配了真实 key；这里只验证"返回 None 时所有调用安全"
    assert obs.start_trace("q", "s") is None or obs.get_langfuse() is not None
    obs.record_span(None, "retrieve")
    obs.end_trace(None, {"x": 1}, "done")


def test_langfuse_span记录与异常降级():
    from app.infra import observability as obs

    class FakeSpan:
        def __init__(self):
            self.ended = False

        def end(self):
            self.ended = True

    class FakeTrace:
        def __init__(self):
            self.spans: list[tuple[str, dict]] = []
            self.updated = None

        def span(self, name, metadata=None, output=None):
            self.spans.append((name, metadata or {}))
            return FakeSpan()

        def update(self, **kwargs):
            self.updated = kwargs

    fake = FakeTrace()
    obs.record_span(fake, "retrieve", {"latency_ms": 12})
    obs.record_span(fake, "check_sufficiency", {"passed": True})
    obs.end_trace(fake, {"status": "done"}, "done")
    assert [s[0] for s in fake.spans] == ["retrieve", "check_sufficiency"]
    assert fake.spans[0][1]["latency_ms"] == 12
    assert fake.updated["metadata"]["status"] == "done"

    class Broken:
        def span(self, **_kwargs):
            raise RuntimeError("langfuse down")

        def update(self, **_kwargs):
            raise RuntimeError("langfuse down")

    obs.record_span(Broken(), "x")  # 不抛
    obs.end_trace(Broken(), {}, "failed")  # 不抛
