"""pgvector 检索库：schema 初始化 / 写入 / 向量召回 / 关键词召回。

设计决策（ADR-01）：为什么向量库用 pgvector 而不是 Chroma/Milvus：
- 少一个服务：FinRAG 已经有 postgres（评测结果、文档元数据都存它），
  向量放同一实例不同 schema，运维面不扩大
- 元数据过滤（行业/报告日期/公司）用 SQL 表达最直接，混合检索
  （向量 + 关键词并集）一条 SQL 搞定，不用在应用层缝两个系统
- 万级-十万级文档 HNSW 完全够；真到千万级再迁 Milvus 不迟
  （检索接口是协议，换实现不动上层）

端口约定：默认 5433，与 DataCrew 的 5432 物理隔离——两个项目
数据库零共享，FinRAG 挂掉不影响 DataCrew 开发。
"""
from __future__ import annotations

import json
import re
from datetime import date

import asyncpg
import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger
from app.rag.chunker import Chunk

log = get_logger("store")

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- 关键词召回的 trigram 索引

CREATE TABLE IF NOT EXISTS rag.documents (
    source      TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    industry    TEXT,
    report_date DATE,
    meta        JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag.chunks (
    id            BIGSERIAL PRIMARY KEY,
    source        TEXT NOT NULL REFERENCES rag.documents(source) ON DELETE CASCADE,
    section_path  TEXT NOT NULL DEFAULT '',
    chunk_index   INTEGER NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'text',
    text          TEXT NOT NULL,
    embedding     vector({dim}),
    char_len      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (source, chunk_index)
);

-- HNSW 向量索引（内积距离；向量已归一化，内积 == 余弦）
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON rag.chunks USING hnsw (embedding vector_ip_ops);
-- trigram 索引：关键词召回走 ILIKE 路径（中文内置分词不可用，见 keyword_search）
CREATE INDEX IF NOT EXISTS chunks_text_trgm
    ON rag.chunks USING gin (text gin_trgm_ops);
"""

_pool: asyncpg.Pool | None = None


async def init_store() -> asyncpg.Pool:
    """建 schema（幂等）并返回连接池。"""
    global _pool
    settings = get_settings()
    _pool = await asyncpg.create_pool(
        settings.pg_dsn, min_size=settings.pg_pool_min, max_size=settings.pg_pool_max
    )
    async with _pool.acquire() as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS rag")
        # 用字面替换而不是 .format()：SQL 里有 JSONB 默认值的花括号，
        # format 会把它们当占位符直接炸（IndexError）
        await conn.execute(SCHEMA_SQL.replace("{dim}", str(settings.embedding_dim)))
    log.info("store.ready", extra={"context": {"dim": settings.embedding_dim}})
    return _pool


async def close_store() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("store 未初始化：先 await init_store()")
    return _pool


def _vec_literal(vec: np.ndarray) -> str:
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


async def upsert_document(
    source: str, title: str, industry: str | None, report_date: str | None, meta: dict
) -> None:
    # asyncpg 不会把 str 自动转成 DATE（报 DataError），显式解析
    parsed_date = date.fromisoformat(report_date) if report_date else None
    meta_json = json.dumps(meta, ensure_ascii=False)  # asyncpg 不自动序列化 JSONB
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO rag.documents (source, title, industry, report_date, meta)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (source) DO UPDATE SET
                title = EXCLUDED.title,
                industry = EXCLUDED.industry,
                report_date = EXCLUDED.report_date,
                meta = EXCLUDED.meta
            """,
            source, title, industry, parsed_date, meta_json,
        )


async def upsert_chunks(chunks: list[Chunk], vectors: np.ndarray) -> int:
    """批量写入块。先删旧块再插（文档重建时不留孤儿）。"""
    if len(chunks) != len(vectors):
        raise ValueError("chunks 与 vectors 数量不一致")
    if not chunks:
        return 0
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM rag.chunks WHERE source = $1", chunks[0].source)
        await conn.executemany(
            """
                INSERT INTO rag.chunks
                    (source, section_path, chunk_index, kind, text, embedding, char_len)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
            [
                (
                    c.source, c.section_path, c.chunk_index, c.kind, c.text,
                    _vec_literal(vectors[i]), len(c.text),
                )
                for i, c in enumerate(chunks)
            ],
        )
    log.info("chunks.upserted", extra={"context": {"source": chunks[0].source, "n": len(chunks)}})
    return len(chunks)


async def vector_search(
    query_vec: np.ndarray, top_k: int, industry: str | None = None
) -> list[dict]:
    """向量召回。得分 = 1 - 余弦距离 == 余弦（向量已归一化）。"""
    pool = get_pool()
    sql = """
        SELECT source, section_path, chunk_index, kind, text,
               1 - (embedding <=> $1::vector) AS score
        FROM rag.chunks
        WHERE ($2::text IS NULL OR source IN (
            SELECT source FROM rag.documents WHERE industry = $2))
        ORDER BY embedding <=> $1::vector
        LIMIT $3
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, _vec_literal(query_vec), industry, top_k)
    return [dict(r) for r in rows]


async def keyword_search(query: str, top_k: int, industry: str | None = None) -> list[dict]:
    """关键词召回：查询里的实义片段做 trigram 模糊匹配，命中越多分越高。

    为什么不用内置中文分词：pg 内置 simple 配置对中文按整句切，recall 差。
    trigram 对连续子串匹配有效，代价是索引体积，万级块可接受。
    """
    terms = _extract_terms(query)[:5]
    if not terms:
        return []
    n = len(terms)
    # 按词长加权：命中"宁德润能"（4 字）比命中"年的"（2 字）更能说明
    # 文档相关——不用真 IDF（要全表统计），词长是零成本的近似
    hit_clause = " + ".join(
        "(text ILIKE '%'||$" + str(i + 2) + "||'%')::int * length($" + str(i + 2) + ")"
        for i in range(n)
    )
    where_clause = " OR ".join(
        "text ILIKE '%'||$" + str(i + 2) + "||'%'" for i in range(n)
    )
    industry_param = n + 2
    sql = (
        "SELECT source, section_path, chunk_index, kind, text, ("
        + hit_clause
        + ")::float / " + str(n) + " AS score FROM rag.chunks WHERE ("
        + where_clause
        + ") AND ($" + str(industry_param) + "::text IS NULL OR source IN ("
        + " SELECT source FROM rag.documents WHERE industry = $" + str(industry_param)
        + ")) ORDER BY score DESC LIMIT $1"
    )
    params = [top_k, *terms, industry]
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [dict(r) for r in rows]


def _extract_terms(query: str) -> list[str]:
    """从查询里提取检索用词。

    中文没有空格，整句是一个连续串——直接拿去做 ILIKE 永远匹配不上。
    策略：连续中文串按二元组切（"宁德润能" -> 宁德/德润/润能），
    英文数字串原样保留（ROE、300750 是完整实体，切开就丢了）。
    二元组与 pg_trgm 索引配合，召回靠部分命中计数打分。
    """
    terms: list[str] = []
    for run in re.findall(r"[一-龥]+|[A-Za-z0-9]+", query):
        if re.fullmatch(r"[A-Za-z0-9]+", run):
            if len(run) >= 2:
                terms.append(run)
            continue
        if len(run) <= 8:
            # 整串 + 二元组：完整实体（公司名/指标名）区分度最高，
            # 二元组保证部分命中仍有召回
            terms.append(run)
            if len(run) >= 2:
                terms.extend(run[i : i + 2] for i in range(len(run) - 1))
        else:
            terms.extend(run[i : i + 2] for i in range(len(run) - 1))
    seen: dict[str, None] = {}
    for t in terms:
        seen.setdefault(t, None)
    return list(seen)[:12]
