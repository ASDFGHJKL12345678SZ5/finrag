"""入库流水线：研报 -> 分块 -> 向量化 -> pgvector。

顺序即架构：chunker 是纯函数（可单测）-> embedder 是协议（可换实现）
-> store 是唯一的数据库出入口。ingest 只做编排，不含业务判断。
"""
from __future__ import annotations

import asyncio

import numpy as np

from app.core.logging import get_logger
from app.rag import store
from app.rag.chunker import chunk_document
from app.rag.corpus import Report, build_corpus
from app.rag.embeddings import get_embedder
from app.rag.fact_extract import doc_from_report, extract_facts

log = get_logger("ingest")


async def ingest_doc(doc) -> int:
    """单篇入库核心：只接收不含 facts 的 IngestDoc。"""
    chunks = chunk_document(doc.text, source=doc.source)
    if not chunks:
        log.warning("ingest.empty", extra={"context": {"source": doc.source}})
        return 0
    vectors = get_embedder().embed([c.text for c in chunks])
    meta = {}
    if doc.company_short:
        meta = {"abbr": doc.abbr, "short": doc.company_short, "code": doc.code}
    await store.upsert_document(
        source=doc.source,
        title=doc.title,
        industry=doc.industry,
        report_date=doc.report_date,
        meta=meta,
    )
    await store.upsert_facts(doc.source, extract_facts(doc, chunks))
    return await store.upsert_chunks(chunks, vectors)


async def ingest_report(report: Report) -> int:
    """兼容入口：Report -> IngestDoc（只读文本/元信息），再走 ingest_doc。"""
    return await ingest_doc(doc_from_report(report))


async def ingest_corpus(
    n_companies: int = 200,
    years: tuple[int, ...] | None = None,
    seed: int = 20260929,
) -> dict:
    """整库入库（默认 200 公司 × 5 年 = 1000 篇）。返回统计。"""
    reports = build_corpus(n_companies=n_companies, years=years, seed=seed)
    total_chunks = 0
    total_facts = 0
    for report in reports:
        doc = doc_from_report(report)
        total_chunks += await ingest_doc(doc)
        total_facts += len(extract_facts(doc, chunk_document(doc.text, source=doc.source)))
    stats = {
        "reports": len(reports),
        "chunks": total_chunks,
        "facts": total_facts,
    }
    log.info("ingest.done", extra={"context": stats})
    return stats


def embed_query(query: str) -> np.ndarray:
    return get_embedder().embed([query])[0]


async def embed_query_async(query: str) -> np.ndarray:
    """embed_query 的线程池版本（FR-5）。

    ONNX 推理是**同步 CPU 计算**，直接放在事件循环里会让整个 worker 在
    每次检索时停摆几十到几百毫秒——并发请求全部排队（低并发测不出来，
    /ask 并发一上来 P95 就炸）。用 asyncio.to_thread 把阻塞计算挪到默认
    线程池，事件循环只负责等 Future。
    """
    return await asyncio.to_thread(embed_query, query)
