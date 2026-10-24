"""FR-10 跨文档聚合：识别 / SQL argmax / 图路径零 LLM / 无数据拒答。"""
from __future__ import annotations

import asyncio
import os

os.environ["EMBEDDING_MODE"] = "mock"
os.environ["LLM_MODE"] = "mock"

import pytest

from app.core.winloop import ensure_selector_loop
from app.rag import store
from app.rag.aggregate import detect_aggregate
from app.rag.corpus import build_corpus
from app.rag.ingest import ingest_report

ensure_selector_loop()


def test_识别行业极值问题():
    spec = detect_aggregate("食品饮料行业2024年营业收入最高的公司是哪家？")
    assert spec is not None
    assert (spec.industry, spec.year, spec.metric) == ("食品饮料", 2024, "营业收入")
    spec2 = detect_aggregate("医药生物行业2025年归母净利润最高的公司是哪家？")
    assert spec2 is not None and spec2.metric == "归母净利润"


@pytest.mark.parametrize(
    "question",
    [
        "宁德润能2024年的营业收入是多少？",      # 没行业/极值词
        "新能源行业2024年的营业收入是多少？",    # 极值词缺失
        "新能源行业营业收入最高的公司是哪家？",  # 年份缺失
        "新能源行业2024年营收最高的公司是哪家？",  # 行业词不在问题里（是"新能源"）
    ],
)
def test_非聚合问题不误触发(question):
    # 第 4 条含"新能源"+"行业"，会被识别——它是合法聚合问法；
    # 这里只断言前三条为 None，最后一条单独放行
    if question.startswith("新能源行业2024年营收"):
        assert detect_aggregate(question) is not None
    else:
        assert detect_aggregate(question) is None


def _run(body):
    async def wrapper():
        await store.init_store()
        try:
            pool = store.get_pool()
            async with pool.acquire() as conn:
                await conn.execute("TRUNCATE rag.documents CASCADE")
            await store.reset_lexical_index()
            return await body()
        finally:
            await store.close_store()

    return asyncio.run(wrapper())


def test_sql聚合取最高营收():
    async def body():
        reports = build_corpus(n_companies=8, years=(2024,), seed=42)
        for report in reports:
            await ingest_report(report)
        row = await store.aggregate_facts("营业收入", 2024, "新能源")
        assert row is not None
        expected = max(report.facts[0].answer for report in reports)
        assert row["answer"] == expected
        assert row["source"].split("-")[2] == "华曜新源"  # seed=42 的赢家

    _run(body)


def test_图路径零LLM调用(monkeypatch):
    async def body():
        reports = build_corpus(n_companies=8, years=(2024,), seed=42)
        for report in reports:
            await ingest_report(report)
        from app.rag import graph as graph_mod

        def _boom(*_args, **_kwargs):
            raise AssertionError("聚合路径不应调用 LLM")

        monkeypatch.setattr(graph_mod, "get_llm", _boom)
        final = await graph_mod.ask("新能源行业2024年营业收入最高的公司是哪家？", session_id="agg")
        assert final["status"] == "done"
        assert "华曜新源" in final["answer"]
        nodes = [t["node"] for t in final.get("trace") or []]
        assert "aggregate" in nodes and "generate" not in nodes
        assert final["retrieved"][0]["citation_id"] == "c1"

    _run(body)


def test_无数据时拒答而不是拿别家凑():
    async def body():
        reports = build_corpus(n_companies=8, years=(2024,), seed=42)
        for report in reports:
            await ingest_report(report)
        from app.rag import graph as graph_mod

        final = await graph_mod.ask("银行行业2019年营业收入最高的公司是哪家？", session_id="agg-empty")
        assert final["status"] == "insufficient"
        assert final["refusal_reason"] == "aggregate_empty"

    _run(body)
