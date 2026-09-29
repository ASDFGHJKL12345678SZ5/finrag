"""检索库集成测试（需要本地 postgres，CI 用 services 提供）。

形态说明：同步测试 + asyncio.run()，每个测试自建自销事件循环与连接池。
不用 pytest-asyncio 的 async fixture——跨循环复用 asyncpg 池会报
"attached to a different loop"，而这里的测试本来就该互相隔离：
每个测试自己灌自己的数据，互不依赖执行顺序。
"""
import asyncio
import os

import pytest

from app.core.winloop import ensure_selector_loop
from app.rag import store
from app.rag.corpus import all_facts, build_corpus
from app.rag.ingest import embed_query, ingest_report

ensure_selector_loop()

pytestmark = pytest.mark.skipif(
    os.environ.get("FINRAG_SKIP_DB") == "1",
    reason="FINRAG_SKIP_DB=1：跳过需要数据库的集成测试",
)


def _run_with_store(body):
    """建库 -> 跑测试体 -> 关库，全在同一个事件循环里。"""

    async def wrapper():
        await store.init_store()
        try:
            return await body()
        finally:
            await store.close_store()

    return asyncio.run(wrapper())


async def _reset_and_seed(n: int, seed: int):
    """先清库再灌数据——测试确定性不依赖库里历史状态。"""
    os.environ["EMBEDDING_MODE"] = "mock"
    pool = store.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE rag.documents CASCADE")
    reports = build_corpus(n_reports=n, seed=seed)
    for r in reports:
        await ingest_report(r)
    return reports


def test_关键词召回能找回对应文档():
    async def body():
        reports = await _reset_and_seed(8, 99)
        facts = all_facts(reports)
        hit = 0
        for f in facts[:10]:
            rows = await store.keyword_search(f.evidence_terms[0], top_k=5)
            if any(f.source == r["source"] for r in rows):
                hit += 1
        assert hit >= 8, f"关键词召回命中率过低: {hit}/10"

    _run_with_store(body)


def test_向量检索返回归一化得分():
    async def body():
        await _reset_and_seed(8, 99)
        qv = embed_query("营业收入是多少")
        rows = await store.vector_search(qv, top_k=5)
        assert len(rows) == 5
        for r in rows:
            assert -1.01 <= r["score"] <= 1.01
            assert r["text"]

    _run_with_store(body)


def test_按行业过滤只返回该行业文档():
    async def body():
        reports = await _reset_and_seed(4, 100)
        industry = reports[0].industry
        qv = embed_query("营收")
        rows = await store.vector_search(qv, top_k=10, industry=industry)
        assert rows, "行业过滤后应有结果"
        expect = {r.source for r in reports if r.industry == industry}
        got = {r["source"] for r in rows}
        assert got, "过滤结果不应为空"
        assert got <= expect, "过滤结果必须全部来自该行业的文档"

    _run_with_store(body)
