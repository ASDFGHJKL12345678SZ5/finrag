"""FR-2：评测隔离（独立库）+ per_fact 逐题明细不变量。

最重要的一条是 `test_评测不碰开发库`：在开发库放一条哨兵文档，跑一遍评测，
哨兵必须还在——旧版 runner 的 `TRUNCATE rag.documents CASCADE` 会把它删掉。
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
    """哨兵文档在评测前后都必须存在——评测只能清自己的库。"""
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
        await run_eval(n_reports=2, seed=42)
        still = await dev.fetchval(
            "SELECT count(*) FROM rag.documents WHERE source = '__sentinel__'"
        )
        assert still == 1, "评测把开发库的文档清掉了（FR-2 隔离失效）"
    finally:
        await dev.execute("DELETE FROM rag.documents WHERE source = '__sentinel__'")
        await dev.close()


async def test_per_fact_逐题明细落库可下钻():
    settings = get_settings()
    metrics = await run_eval(n_reports=3, seed=42)
    assert metrics["eval_database"].endswith("_eval")

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
    assert per_fact["tier_sizes"][0] > 0
    assert items, "per_fact 不能只有 tier_sizes 没有明细"

    fact_items = [i for i in items if i["kind"] == "fact"]
    assert fact_items
    sample = fact_items[0]
    for key in ("fact_id", "source", "tier", "question", "retrieval_hit",
                "top_sources", "guard_refusal_reason"):
        assert key in sample, f"逐事实明细缺字段 {key}"
    assert sample["tier"] in ("exact", "paraphrase")
    # 原题档必须有端到端答案/状态
    exact = next(i for i in fact_items if i["tier"] == "exact")
    assert "answer" in exact and "status" in exact

    # 拒答档与污染档也要有逐题记录
    assert any(i["kind"] == "refusal" for i in items)
    assert any(i["kind"] == "contamination" for i in items)
    refusal = next(i for i in items if i["kind"] == "refusal")
    assert "refusal_reason" in refusal and "htype" in refusal

    # 误拒率/漏拒率两个新指标必须落进 metrics（FR-2 验收）
    metrics_db = row["metrics"]
    if isinstance(metrics_db, str):
        metrics_db = json.loads(metrics_db)
    assert "guard_false_reject" in metrics_db
    assert "refusal_miss_rate" in metrics_db
