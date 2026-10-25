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


def test_行业二字只是可选加强():
    """R1：有/没有“行业”二字，识别结果必须一致。"""
    with_word = detect_aggregate("食品饮料行业2024年营业收入最高的公司是哪家？")
    without_word = detect_aggregate("食品饮料2024年营业收入最高的公司是哪家？")
    assert with_word == without_word
    assert without_word is not None


@pytest.mark.parametrize(
    ("question", "industry", "year", "metric"),
    [
        # R1 验收：不带“行业”二字、带行业词 + 居首/最高/第一的 6 种问法
        ("银行2022年营收居首的公司是哪家？", "银行", 2022, "营业收入"),
        ("半导体2024年营业收入最高的公司是哪家？", "半导体", 2024, "营业收入"),
        ("医药生物2021年归母净利润第一的是哪家？", "医药生物", 2021, "归母净利润"),
        ("食品饮料2025年毛利率最高的是哪家公司？", "食品饮料", 2025, "毛利率"),
        ("新能源2023年研发费用率榜首的公司", "新能源", 2023, "研发费用率"),
        ("银行2025年经营现金流最大的是哪家？", "银行", 2025, "经营性现金流"),
    ],
)
def test_不带行业二字也能识别(question, industry, year, metric):
    spec = detect_aggregate(question)
    assert spec is not None, f"未识别：{question}"
    assert (spec.industry, spec.year, spec.metric) == (industry, year, metric)


@pytest.mark.parametrize(
    "question",
    [
        "宁德润能2024年的营业收入是多少？",      # 无行业词、无极值词
        "新能源2024年的营业收入是多少？",        # 有行业词+年份+指标，无极值语义
        "银行业2024年营收是多少？",              # R1 反向测试：不得误触发聚合
        "半导体行业2024年毛利率是多少？",        # 同上（带“行业”也不触发）
        "新能源营业收入最高的公司是哪家？",      # 缺年份
        "2024年营业收入最高的公司是哪家？",      # 缺行业词
    ],
)
def test_非聚合问题不误触发(question):
    assert detect_aggregate(question) is None


def test_行业词表取自语料元信息(monkeypatch):
    """R1：词表由 corpus.INDUSTRIES（Report.industry 取值集合）派生，不手写。

    把语料元信息临时扩成含“白酒/乳业”的行业，识别立即生效——如果词表是
    本模块手写的常量，这两个词不可能被识别。反向：元信息里没有的“教育”
    不能凭空识别。
    """
    from app.rag import corpus

    monkeypatch.setattr(corpus, "INDUSTRIES", [*corpus.INDUSTRIES, "白酒", "乳业"])
    for word in ("白酒", "乳业"):
        spec = detect_aggregate(f"{word}2022年营收居首的公司是哪家？")
        assert spec is not None and spec.industry == word
    assert detect_aggregate("教育2022年营收居首的公司是哪家？") is None


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
