"""FR-2 评测隔离 + FR-1 逐题明细：评测只动 finrag_eval，不碰开发库。

最重要的一条是 `test_评测不碰开发库`：开发库放哨兵文档，跑一遍评测，
哨兵必须还在——旧 runner 的 `TRUNCATE rag.documents CASCADE` 会删掉它。
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

import asyncpg
import pytest

from app.core.config import get_settings
from app.eval.db import ensure_eval_database
from app.eval.runner import run_eval


async def _connect(dsn: str) -> asyncpg.Connection:
    return await asyncpg.connect(dsn)


def test_评测库_DSN_由开发库派生():
    settings = get_settings()
    dev = urlsplit(settings.pg_dsn)
    ev = urlsplit(settings.resolved_eval_dsn)
    assert ev.hostname == dev.hostname and ev.port == dev.port
    assert ev.path.rstrip("/").endswith("_eval")
    assert ev.path != dev.path


async def test_ensure_eval_database_幂等():
    settings = get_settings()
    db1 = await ensure_eval_database(settings.pg_dsn, settings.resolved_eval_dsn)
    db2 = await ensure_eval_database(settings.pg_dsn, settings.resolved_eval_dsn)
    assert db1 == db2
    conn = await _connect(settings.resolved_eval_dsn)
    try:
        assert await conn.fetchval("SELECT current_database()") == db1
    finally:
        await conn.close()


async def test_评测不碰开发库():
    settings = get_settings()
    dev = await _connect(settings.pg_dsn)
    await dev.execute("CREATE SCHEMA IF NOT EXISTS rag")
    await dev.execute(
        """
        CREATE TABLE IF NOT EXISTS rag.documents (
            source TEXT PRIMARY KEY, title TEXT NOT NULL, industry TEXT,
            report_date DATE, meta JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    await dev.execute(
        "INSERT INTO rag.documents (source, title) VALUES ('__sentinel__', '开发库哨兵') "
        "ON CONFLICT (source) DO NOTHING"
    )
    try:
        await run_eval(
            n_companies=2, years=(2024, 2025), seed=42,
            n_exact=6, n_abbr=6, n_calc=4, n_refusal=4,
            strategies=("hybrid",), end2end_limit=2,
        )
        still = await dev.fetchval(
            "SELECT count(*) FROM rag.documents WHERE source = '__sentinel__'"
        )
        assert still == 1, "评测把开发库的文档清掉了（FR-2 隔离失效）"
    finally:
        await dev.execute("DELETE FROM rag.documents WHERE source = '__sentinel__'")
        await dev.close()


async def test_per_fact_逐题明细落库可下钻(monkeypatch):
    # 哨兵库测试会关掉 store 连接；这里重新跑一遍小评测，拿到最新 run
    metrics = await run_eval(
        n_companies=2, years=(2024, 2025), seed=42,
        n_exact=6, n_abbr=6, n_calc=4, n_refusal=4,
        strategies=("hybrid",), end2end_limit=2,
    )
    assert metrics["eval_database"].endswith("_eval")

    settings = get_settings()
    conn = await _connect(settings.resolved_eval_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT metrics, per_fact FROM eval.runs ORDER BY run_id DESC LIMIT 1"
        )
    finally:
        await conn.close()

    per_fact = row["per_fact"]
    if isinstance(per_fact, str):
        per_fact = json.loads(per_fact)
    items = per_fact["items"]
    assert per_fact["n_reports"] == 4
    assert items, "per_fact 不能没有逐题明细"

    answer_items = [i for i in items if i["category"] in ("exact", "abbr", "calculation")]
    assert answer_items
    sample = answer_items[0]
    for key in ("qid", "category", "subtype", "question", "source",
                "expected_answer", "retrieval", "expected"):
        assert key in sample, f"逐题明细缺字段 {key}"
    assert "hybrid" in sample["retrieval"]
    assert sample["retrieval"]["hybrid"]["rank"] is not None

    # 端到端抽样必须横跨多个题档（分层抽样，不是只抽 exact）
    answered = [i for i in answer_items if i.get("status") not in (None, "skipped")]
    assert answered, "应有端到端答案"
    assert len({i["category"] for i in answered}) >= 2

    # 拒答题也要有逐题记录
    refusals = [i for i in items if i["category"] == "refusal"]
    assert refusals
    assert any("refused" in i for i in refusals)
    assert metrics["refusal_accuracy"]
    assert metrics["refusal_miss_rate"] >= 0.0

    metrics_db = row["metrics"]
    if isinstance(metrics_db, str):
        metrics_db = json.loads(metrics_db)
    assert "guard_false_reject" in metrics_db
    assert "refusal_miss_rate" in metrics_db
    assert "retrieval" in metrics_db
    assert metrics_db["n_chunks"] > 0


async def test_评测库残留不污染下一次(monkeypatch):
    """同一评测库连续跑两次：第二次必须从头重建（块数不翻倍）。"""
    args = dict(
        n_companies=2, years=(2025,), seed=42,
        n_exact=6, n_abbr=6, n_calc=4, n_refusal=4,
        strategies=("keyword_only",), end2end_limit=-1,
    )
    first = await run_eval(**args)
    second = await run_eval(**args)
    assert second["n_chunks"] == first["n_chunks"], "重建后块数翻倍=旧块没清干净"
