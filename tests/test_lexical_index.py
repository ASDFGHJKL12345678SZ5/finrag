"""FR-4 词法倒排索引：入库维护 / BM25 查询 / df 正确性 / 重建。

为什么单独测：关键词路的性能修复必须同时保证**语义不退化**——df 算错、
重复入库翻倍、删块不扣减，都会让 BM25 排序慢慢失真，而召回率测试未必抓得到。
"""
from __future__ import annotations

import asyncio
import os

import numpy as np

os.environ["EMBEDDING_MODE"] = "mock"

from app.rag import store
from app.rag.chunker import Chunk

SOURCE = "新能源-000-宁德润能-2025"
SOURCE2 = "半导体-001-方衡半导体-2025"


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


def _vec(n: int) -> np.ndarray:
    arr = np.ones((n, 512), dtype=np.float32)
    return arr / np.linalg.norm(arr, axis=1, keepdims=True)


async def _seed_source(source: str = SOURCE, texts: list[str] | None = None) -> None:
    texts = texts or [
        "2025年公司营业收入518.75亿元，同比增长-13.1%。",
        "归母净利润99.61亿元，毛利率18.5%。",
        "公司是国内新能源行业龙头。",
    ]
    await store.upsert_document(
        source=source,
        title="宁德润能2025年年度研究报告",
        industry="新能源",
        report_date="2025-12-31",
        meta={"abbr": "宁德润", "code": "600001"},
    )
    chunks = [
        Chunk(text=t, source=source, section_path="二、财务分析", chunk_index=i)
        for i, t in enumerate(texts)
    ]
    await store.upsert_chunks(chunks, _vec(len(chunks)))


def test_入库tf写真实词频():
    """去重必须发生在 Counter 之后：重复词要写真实 tf，而不是恒为 1。"""

    async def body():
        await _seed_source(texts=["营业收入 营业收入 营业收入。"])
        pool = store.get_pool()
        async with pool.acquire() as conn:
            chunk_id = await conn.fetchval(
                "SELECT id FROM rag.chunks WHERE source = $1 AND chunk_index = 0",
                SOURCE,
            )
            tf = await conn.fetchval(
                "SELECT tf FROM rag.chunk_terms WHERE chunk_id = $1 AND term = $2",
                chunk_id, "营业收入",
            )
            tf_bigram = await conn.fetchval(
                "SELECT tf FROM rag.chunk_terms WHERE chunk_id = $1 AND term = $2",
                chunk_id, "营业",
            )
        assert tf == 3, f"重复 3 次应写 tf=3，实际 {tf}"
        assert tf_bigram == 3, f"二元组重复 3 次应写 tf=3，实际 {tf_bigram}"

    _run(body)


def test_查询侧词表仍去重():
    """查询侧不参与本次修复：重复 query 词仍只保留一个。"""
    terms = store._extract_terms("营业收入 营业收入", limit=12)
    assert len(terms) == len(set(terms))


def test_入库维护倒排索引与统计():
    async def body():
        await _seed_source()
        pool = store.get_pool()
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM rag.chunk_terms") > 0
            assert await conn.fetchval(
                "SELECT df FROM rag.term_df WHERE term = '营业'"
            ) == 1
            n_chunks, avg_len = await store.corpus_stats()
            assert n_chunks == 3 and avg_len > 0

    _run(body)


def test_bm25_命中正确文档且支持行业过滤():
    async def body():
        await _seed_source()
        await _seed_source(
            SOURCE2,
            ["2025年公司营业收入44.79亿元。", "归母净利润6.87亿元。"],
        )
        await store.upsert_document(
            source=SOURCE2, title="方衡半导体2025年年度研究报告",
            industry="半导体", report_date="2025-12-31", meta={},
        )
        rows = await store.keyword_search("宁德润能2025年的营业收入是多少", top_k=5)
        assert rows and rows[0]["source"] == SOURCE
        rows2 = await store.keyword_search(
            "宁德润能2025年的营业收入是多少", top_k=5, industry="半导体"
        )
        assert all(r["source"] == SOURCE2 for r in rows2)

    _run(body)


def test_重复入库df不翻倍():
    async def body():
        await _seed_source()
        pool = store.get_pool()

        async def df() -> int:
            async with pool.acquire() as conn:
                return await conn.fetchval(
                    "SELECT df FROM rag.term_df WHERE term = '营业'"
                )

        before = await df()
        await _seed_source()  # 同一 source 重灌
        assert await df() == before, "重灌同一篇不能让 df 累加"

    _run(body)


def test_删除旧块会扣减df():
    async def body():
        await _seed_source()
        pool = store.get_pool()

        async def df(term: str) -> int:
            async with pool.acquire() as conn:
                return await conn.fetchval(
                    "SELECT coalesce(df,0) FROM rag.term_df WHERE term = $1", term
                ) or 0

        assert await df("营业") == 1
        # 用完全不含"营业"的新内容重灌同一 source
        await _seed_source(texts=["公司总部位于上海。", "员工人数五千人。"])
        assert await df("营业") == 0
        assert await df("员工") == 1

    _run(body)


def test_company_names包含简称与缩写():
    async def body():
        await _seed_source()
        names = await store.company_names()
        assert "宁德润能" in names
        assert "宁德润" in names  # 3 字缩写来自 documents.meta.abbr

    _run(body)


def test_指标同义词扩词命中财务块():
    """问"营收"（不是"营业收入"）时，扩词必须把营收块排到现金流块前面。

    FR-1 的 synonym 档就是靠这一条：不扩词时 BM25 只匹配公司名/年份，
    短小的现金流体靠长度归一化反超，答案"有据可依"但答错指标。
    """

    async def body():
        await _seed_source(texts=[
            "2025年公司营业收入518.75亿元，同比增长13.1%。",
            "2025年经营性现金流净额106.82亿元，与净利润规模基本匹配。",
            "公司总部位于上海。",
        ])
        rows = await store.keyword_search("宁德润能2025年的营收是多少", top_k=3)
        assert rows
        assert "营业收入" in rows[0]["text"], (
            f"扩词后营收块应排第一，实际: {rows[0]['text'][:60]}"
        )

    _run(body)


def test_reset_lexical_index清空统计():
    async def body():
        await _seed_source()
        await store.reset_lexical_index()
        pool = store.get_pool()
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM rag.chunk_terms") == 0
            assert await conn.fetchval("SELECT count(*) FROM rag.term_df") == 0
        assert await store.corpus_stats() == (0, 0.0)

    _run(body)
