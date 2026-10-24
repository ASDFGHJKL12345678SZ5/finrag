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

-- HNSW 向量索引不在 SCHEMA_SQL 里建：算子类必须和查询运算符一致（查询用 <=>
-- 余弦距离，索引就得是 vector_cosine_ops（ADR-08）。曾经用 vector_ip_ops，
-- 计划器直接用不上索引，万行 chunks 全表扫描（EXPLAIN 可见 Seq Scan）。向量已
-- L2 归一化，余弦/内积数值等价，但 pgvector 要求算子类与运算符一一对应。
-- 索引由 init_store() 里的 _ensure_hnsw_opclass() 建：只在缺失或算子类不
-- 对（旧库升级路径）时才 DDL——早期版每次 init_store 都 DROP+CREATE，万行
-- 级 chunks 的 HNSW 重建是分钟级阻塞，API/测试每起一次就付一次。
-- （另：DO $$  dollar-quoting 曾在本文件被写成单 $，直接 PostgresSyntaxError，
--   条件逻辑放 Python 后这类问题也顺带消除。）
-- trigram 索引：关键词召回走 ILIKE 路径（中文内置分词不可用，见 keyword_search）
CREATE INDEX IF NOT EXISTS chunks_text_trgm
    ON rag.chunks USING gin (text gin_trgm_ops);

-- FR-4 词法倒排索引：入库时维护 term -> (chunk, tf)，查询时不再全表 ILIKE、
-- 不再 count(*) 算 df。2 字中文二元组直接做词条（pg 内置中文分词不可用）。
CREATE TABLE IF NOT EXISTS rag.chunk_terms (
    chunk_id BIGINT NOT NULL REFERENCES rag.chunks(id) ON DELETE CASCADE,
    term     TEXT NOT NULL,
    tf       INT NOT NULL,
    PRIMARY KEY (chunk_id, term)
);
CREATE INDEX IF NOT EXISTS chunk_terms_term_idx ON rag.chunk_terms(term);

CREATE TABLE IF NOT EXISTS rag.term_df (
    term TEXT PRIMARY KEY,
    df   INT NOT NULL
);

-- 单行统计（N / 平均块长）：BM25 需要，入库时刷新；查询时零 count(*)
CREATE TABLE IF NOT EXISTS rag.corpus_stats (
    id       INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    n_chunks BIGINT NOT NULL DEFAULT 0,
    avg_len  DOUBLE PRECISION NOT NULL DEFAULT 0
);
"""

_pool: asyncpg.Pool | None = None
# 公司名缓存（实体锚定用）：进程内，60s TTL，见 company_names()
_company_cache: list[str] | None = None
_company_cache_at: float = 0.0


async def _ensure_hnsw_opclass(conn) -> None:
    """确保 HNSW 索引存在且算子类与查询运算符匹配（ADR-08，Python 侧条件执行）。"""
    row = await conn.fetchrow(
        """
        SELECT op.opcname AS opclass
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        JOIN pg_class t ON t.oid = i.indrelid
        JOIN pg_opclass op ON op.oid = i.indclass[1]
        WHERE t.relnamespace = 'rag'::regnamespace
          AND t.relname = 'chunks'
          AND c.relname = 'chunks_embedding_hnsw'
        """
    )
    if row and row["opclass"] == "vector_cosine_ops":
        return  # 索引已正确：热路径零 DDL
    if row:
        log.warning("store.hnsw_opclass_mismatch", extra={"context": {"found": row["opclass"]}})
        await conn.execute("DROP INDEX IF EXISTS rag.chunks_embedding_hnsw")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw "
        "ON rag.chunks USING hnsw (embedding vector_cosine_ops)"
    )

async def init_store(dsn: str | None = None) -> asyncpg.Pool:
    """建 schema（幂等）并返回连接池。

    dsn 参数（FR-2）：评测进程传入独立评测库 DSN，**同一个进程内所有
    store.get_pool() 调用都走它**——评测的 TRUNCATE/灌数据因此只作用于
    评测库，绝不碰开发库。业务进程（API）不传，沿用 settings.pg_dsn。
    """
    global _pool
    settings = get_settings()
    _pool = await asyncpg.create_pool(
        dsn or settings.pg_dsn, min_size=settings.pg_pool_min, max_size=settings.pg_pool_max
    )
    async with _pool.acquire() as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS rag")
        # 用字面替换而不是 .format()：SQL 里有 JSONB 默认值的花括号，
        # format 会把它们当占位符直接炸（IndexError）
        await conn.execute(SCHEMA_SQL.replace("{dim}", str(settings.embedding_dim)))
        # HNSW 索引条件建/纠（在连接内：需要 conn；逻辑见 _ensure_hnsw_opclass）
        await _ensure_hnsw_opclass(conn)
        # 列宽自检（最终审查修复）：CREATE TABLE IF NOT EXISTS 不会给旧库改列宽，
        # 换 embedding_dim 后旧 chunks 表还是老宽度，第一次 INSERT 才被 pgvector
        # 用一句 expected 256 dimensions, not 512 打脸。这里启动时就给干净报错。
        col = await conn.fetchval(
            "SELECT format_type(a.atttypid, a.atttypmod) "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            + "WHERE n.nspname = 'rag' AND c.relname = 'chunks' "
            + "AND a.attname = 'embedding'"
        )
        if col:
            m = re.search(r"vector.(\d+).", col)
            if m and int(m.group(1)) != settings.embedding_dim:
                db_dim = int(m.group(1))
                raise ValueError(
                    "rag.chunks.embedding 列宽 " + str(db_dim)
                    + " 与配置 embedding_dim=" + str(settings.embedding_dim)
                    + " 不一致：换模型/改维度需要先迁移旧列（ALTER TABLE rag.chunks "
                    + "ALTER COLUMN embedding TYPE vector(" + str(settings.embedding_dim)
                    + ") 并重建 HNSW 索引），否则要到第一次 INSERT 才报错"
                )
    log.info("store.ready", extra={"context": {"dim": settings.embedding_dim}})
    return _pool


async def close_store() -> None:
    """关闭连接池（评测进程切换 DSN / 测试收尾用）；同时清公司名缓存。"""
    global _pool, _company_cache, _company_cache_at
    if _pool is not None:
        await _pool.close()
        _pool = None
    _company_cache = None
    _company_cache_at = 0.0


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
    """批量写入块。先删旧块再插（文档重建时不留孤儿），同步维护词法索引。

    FR-4：写块同时更新 `chunk_terms`（chunk→term tf）与 `term_df`（全局 df），
    查询时只查倒排索引，不再对 chunks 全表 ILIKE。删除旧块前先扣减旧词频，
    否则重灌同一篇会让 df 越来越大（IDF 失真、热门词被过度惩罚）。
    """
    if len(chunks) != len(vectors):
        raise ValueError("chunks 与 vectors 数量不一致")
    if not chunks:
        return 0
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        old_ids = [
            r["id"] for r in await conn.fetch(
                "SELECT id FROM rag.chunks WHERE source = $1", chunks[0].source
            )
        ]
        if old_ids:
            await _decay_term_df(conn, old_ids)
        await conn.execute("DELETE FROM rag.chunks WHERE source = $1", chunks[0].source)

        new_ids: list[int] = []
        for i, c in enumerate(chunks):
            cid = await conn.fetchval(
                """
                INSERT INTO rag.chunks
                    (source, section_path, chunk_index, kind, text, embedding, char_len)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                c.source, c.section_path, c.chunk_index, c.kind, c.text,
                _vec_literal(vectors[i]), len(c.text),
            )
            new_ids.append(cid)
        await _index_chunk_terms(conn, new_ids, chunks)
        await _refresh_corpus_stats(conn)
        await conn.execute("DELETE FROM rag.term_df WHERE df <= 0")
    log.info("chunks.upserted", extra={"context": {"source": chunks[0].source, "n": len(chunks)}})
    return len(chunks)


async def _decay_term_df(conn, chunk_ids: list[int]) -> None:
    """旧块删除前扣减 df（与 _index_chunk_terms 的增量对称）。"""
    await conn.execute(
        """
        UPDATE rag.term_df td
           SET df = td.df - sub.c
          FROM (
                SELECT term, count(*) AS c
                  FROM rag.chunk_terms
                 WHERE chunk_id = ANY($1::bigint[])
                 GROUP BY term
               ) sub
         WHERE td.term = sub.term
        """,
        chunk_ids,
    )


async def _index_chunk_terms(conn, chunk_ids: list[int], chunks: list[Chunk]) -> None:
    """把每块的词条（正文 + source 元数据）写入倒排索引并累加 df。"""
    from collections import Counter

    rows: list[tuple[int, str, int]] = []
    for cid, chunk in zip(chunk_ids, chunks, strict=True):
        # source 也进索引：财务块的正文只有指标、公司名只在 source 里
        # （旧 ILIKE 路同时查 text/source，这一条是它的等价物）
        terms = _extract_terms(chunk.text, limit=64) + _extract_terms(
            chunk.source, limit=16
        )
        for term, tf in Counter(terms).items():
            rows.append((cid, term, tf))
    if not rows:
        return
    await conn.executemany(
        "INSERT INTO rag.chunk_terms (chunk_id, term, tf) VALUES ($1, $2, $3)"
        " ON CONFLICT (chunk_id, term) DO UPDATE SET tf = rag.chunk_terms.tf + EXCLUDED.tf",
        rows,
    )
    await conn.execute(
        """
        INSERT INTO rag.term_df (term, df)
        SELECT term, count(*) FROM rag.chunk_terms
         WHERE chunk_id = ANY($1::bigint[])
         GROUP BY term
        ON CONFLICT (term) DO UPDATE SET df = rag.term_df.df + EXCLUDED.df
        """,
        chunk_ids,
    )


async def _refresh_corpus_stats(conn) -> None:
    await conn.execute(
        """
        INSERT INTO rag.corpus_stats (id, n_chunks, avg_len)
        SELECT 1, count(*), coalesce(avg(char_len), 0) FROM rag.chunks
        ON CONFLICT (id) DO UPDATE
           SET n_chunks = EXCLUDED.n_chunks, avg_len = EXCLUDED.avg_len
        """
    )


async def reset_lexical_index() -> None:
    """清空词法索引（评测/测试 TRUNCATE 文档后必须调用，否则 df 是脏的）。"""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE rag.chunk_terms")
        await conn.execute("TRUNCATE rag.term_df")
        await conn.execute("TRUNCATE rag.corpus_stats")
        await conn.execute(
            "INSERT INTO rag.corpus_stats (id, n_chunks, avg_len) VALUES (1, 0, 0)"
        )
    invalidate_company_cache()


def invalidate_company_cache() -> None:
    """文档集合变化后清公司名缓存（评测重灌/测试隔离）。"""
    global _company_cache, _company_cache_at
    _company_cache = None
    _company_cache_at = 0.0



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


async def keyword_search(
    query: str,
    top_k: int,
    industry: str | None = None,
    weights: dict[str, float] | None = None,
) -> list[dict]:
    """关键词召回：BM25（倒排索引）+ 预计算 df（FR-4）。

    旧实现每次查询对 chunks 全表 ILIKE 匹配 12 个二元组、再全表 count(*) 算
    df——O(N) 扫描 × 12，万级块下 P95 秒级，且 pg_trgm 索引对 2 字符模式
    基本无效。现在：
    - 入库时维护 `chunk_terms`（chunk→term tf）与 `term_df`（term→df）
    - 查询把 12 个词当参数查倒排索引，SQL 内用 BM25(k1=1.2, b=0.75) 打分
    - N / 平均块长取自 `corpus_stats` 单行表，查询期零 count(*)
    - weights 只保留领域加权（指标词 ×2），IDF 由 BM25 的 df 直接算
    """
    terms = _extract_terms(query)[:12]
    if not terms:
        return []
    weights = weights or {}
    term_list = list(dict.fromkeys(terms))
    term_weights = [float(weights.get(t, 1.0)) for t in term_list]
    pool = get_pool()
    async with pool.acquire() as conn:
        stats = await conn.fetchrow(
            "SELECT n_chunks, avg_len FROM rag.corpus_stats WHERE id = 1"
        )
        if not stats or not stats["n_chunks"]:
            # 索引未建立（旧库/未重灌）：退回旧 ILIKE 实现，避免静默零召回
            return await _keyword_search_legacy(conn, term_list, top_k, industry, weights)
        rows = await conn.fetch(
            """
            WITH q AS (
                SELECT * FROM unnest($1::text[], $2::float8[]) AS t(term, weight)
            ), st AS (
                SELECT n_chunks, avg_len FROM rag.corpus_stats WHERE id = 1
            )
            SELECT c.source, c.section_path, c.chunk_index, c.kind, c.text,
                   SUM(
                       q.weight
                       * ln(1 + (st.n_chunks - td.df + 0.5) / (td.df + 0.5))
                       * (ct.tf * 2.2)
                       / (ct.tf + 1.2 * (0.25 + 0.75 * c.char_len
                                          / NULLIF(st.avg_len, 0)))
                   ) AS score
              FROM q
              JOIN rag.term_df td   ON td.term = q.term
              JOIN rag.chunk_terms ct ON ct.term = q.term
              JOIN rag.chunks c      ON c.id = ct.chunk_id
              CROSS JOIN st
             WHERE ($3::text IS NULL OR c.source IN (
                    SELECT source FROM rag.documents WHERE industry = $3))
             GROUP BY c.id, c.source, c.section_path, c.chunk_index,
                      c.kind, c.text, st.n_chunks, st.avg_len
             ORDER BY score DESC
             LIMIT $4
            """,
            term_list, term_weights, industry, top_k,
        )
    return [dict(r) for r in rows]


async def _keyword_search_legacy(
    conn, terms: list[str], top_k: int, industry: str | None, weights: dict[str, float]
) -> list[dict]:
    """旧 ILIKE 实现（仅索引未建立时兜底，保证升级期不静默零召回）。"""
    n = len(terms)

    def _w(i: int) -> str:
        return str(round(weights.get(terms[i], 1.0), 6))

    hit_clause = " + ".join(
        "((text ILIKE '%'||$" + str(i + 2) + "||'%')::int * length($" + str(i + 2) + ")"
        " + (source ILIKE '%'||$" + str(i + 2) + "||'%')::int * length($" + str(i + 2) + ")"
        " * 0.5) * " + _w(i)
        for i in range(n)
    )
    where_clause = " OR ".join(
        "text ILIKE '%'||$" + str(i + 2) + "||'%'"
        " OR source ILIKE '%'||$" + str(i + 2) + "||'%'"
        for i in range(n)
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
    rows = await conn.fetch(sql, top_k, *terms, industry)
    return [dict(r) for r in rows]


async def company_names() -> list[str]:
    """全部已知公司名（source 形如 `{industry}-{idx:03d}-{company}-{year}`）。

    返回公司简称与 3 字缩写（FR-1：问题会用简称/缩写而非全名）。实体锚定
    （D13）靠"问题里出现哪个名字"给对应文档加权——易混公司共享前缀时，
    完整简称是最长匹配依据；缩写在简称里是前缀，最长匹配会优先选简称。
    60s TTL：一次评测上千次检索，公司集合在评测周期内不变。
    """
    global _company_cache, _company_cache_at
    import time as _time

    now = _time.monotonic()
    if _company_cache is not None and now - _company_cache_at < 60.0:
        return list(_company_cache)
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT source, meta FROM rag.documents")
    names: list[str] = []
    for row in rows:
        parts = row["source"].split("-")
        if len(parts) >= 3 and parts[2]:
            names.append(parts[2])
        meta = row["meta"] or {}
        # asyncpg 默认把 jsonb 返回成 str（未注册 codec），这里显式解析
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        abbr = meta.get("abbr") if isinstance(meta, dict) else None
        if abbr:
            names.append(str(abbr))
    _company_cache, _company_cache_at = list(dict.fromkeys(names)), now
    return list(_company_cache)


async def corpus_stats() -> tuple[int, float]:
    """(N, 平均块长) —— 入库时维护在 corpus_stats 单行表，查询期零 count(*)。"""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT n_chunks, avg_len FROM rag.corpus_stats WHERE id = 1"
        )
    if not row:
        return 0, 0.0
    return int(row["n_chunks"]), float(row["avg_len"] or 0.0)


async def term_df(terms: list[str]) -> dict[str, int]:
    """一批词各自的文档频率（df）——从入库时维护的 term_df 表查（FR-4）。

    旧实现在查询时对 chunks 全表做 12 路 ILIKE + 逐列聚合，是关键词路最贵的
    一步；现在退化成一次主键/索引查询。索引未建立（旧库未重灌）时兜底到
    旧算法，保证升级期数值不突变。
    """
    terms = list(dict.fromkeys(terms))
    if not terms:
        return {}
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT term, df FROM rag.term_df WHERE term = ANY($1::text[])", terms
        )
        counts = dict.fromkeys(terms, 0)
        for row in rows:
            counts[row["term"]] = int(row["df"])
        if not any(counts.values()):
            n_chunks = await conn.fetchval("SELECT count(*) FROM rag.chunks")
            if n_chunks:
                return await _term_df_legacy(conn, terms)
    return counts


async def _term_df_legacy(conn, terms: list[str]) -> dict[str, int]:
    """旧的全表 df 计算（仅索引未建立时兜底）。"""
    clauses = " OR ".join(
        "text ILIKE '%'||$" + str(i + 1) + "||'%'"
        " OR source ILIKE '%'||$" + str(i + 1) + "||'%'"
        for i in range(len(terms))
    )
    selects = ", ".join(
        "((text ILIKE '%'||$" + str(i + 1) + "||'%')::int"
        " + (source ILIKE '%'||$" + str(i + 1) + "||'%')::int > 0)::int AS hit_" + str(i)
        for i in range(len(terms))
    )
    rows = await conn.fetch(f"SELECT {selects} FROM rag.chunks WHERE {clauses}", *terms)
    counts = dict.fromkeys(terms, 0)
    for row in rows:
        for i, t in enumerate(terms):
            counts[t] += row["hit_" + str(i)]
    return counts


def idf_weights(terms: list[str], df: dict[str, int], n_docs: int) -> dict[str, float]:
    """BM25 风格 IDF：词越稀有权重越高，且保证为正。

    n_docs 为 0（空库）时全部等权——空库没什么可加权的。
    """
    import math

    if n_docs <= 0:
        return dict.fromkeys(terms, 1.0)
    out: dict[str, float] = {}
    for t in terms:
        d = df.get(t, 0)
        out[t] = math.log((n_docs - d + 0.5) / (d + 0.5) + 1.0)
    return out


# 金融指标词典：查询命中指标时，含该指标短语的块加权——问"归母净利润"
# 时该把写净利润的财务块排前，而不是只写营业收入的概况块
# （评测实测：无此加权时 answer_accuracy 卡在 70%，根因是概况块霸榜）
FINANCIAL_METRICS = (
    "营业收入",
    "归母净利润",
    "净利润",
    "毛利率",
    "研发费用率",
    "研发费用",
    "经营性现金流",
    "现金流",
    "资产负债率",
    "每股收益",
)

METRIC_BOOST = 4.0


def boost_metric_terms(question: str, weights: dict[str, float]) -> dict[str, float]:
    """查询命中金融指标时，把属于该指标的检索词权重乘以 METRIC_BOOST。

    指标词是查询的"意图核心"：公司名只定位文档，指标才定位到回答问题的
    那个块。纯 IDF 分不开两者（公司名在一篇里也只出现一次，df 同样低），
    所以用领域词典显式表达这个先验。
    """
    hit_metrics = [m for m in FINANCIAL_METRICS if m in question]
    if not hit_metrics:
        return weights
    out = dict(weights)
    for term, w in list(out.items()):
        if any(term in m for m in hit_metrics):
            out[term] = w * METRIC_BOOST
    return out


def _extract_terms(query: str, limit: int = 12) -> list[str]:
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
    return list(seen)[:limit]
