"""入库流水线：研报 -> 分块 -> 向量化 -> pgvector。

顺序即架构：chunker 是纯函数（可单测）-> embedder 是协议（可换实现）
-> store 是唯一的数据库出入口。ingest 只做编排，不含业务判断。
"""
from __future__ import annotations

import numpy as np

from app.core.logging import get_logger
from app.rag import store
from app.rag.chunker import chunk_document
from app.rag.corpus import Report, all_facts, build_corpus
from app.rag.embeddings import get_embedder

log = get_logger("ingest")


async def ingest_report(report: Report) -> int:
    """单篇入库：分块 -> 向量化 -> 写库。返回块数。"""
    chunks = chunk_document(report.text, source=report.source)
    if not chunks:
        log.warning("ingest.empty", extra={"context": {"source": report.source}})
        return 0
    vectors = get_embedder().embed([c.text for c in chunks])
    await store.upsert_document(
        source=report.source,
        title=report.title,
        industry=report.industry,
        report_date=report.report_date,
        meta={},
    )
    return await store.upsert_chunks(chunks, vectors)


async def ingest_corpus(n_reports: int = 30, seed: int = 20260929) -> dict:
    """整库入库。返回统计（报告数/块数/事实数）。"""
    reports = build_corpus(n_reports=n_reports, seed=seed)
    total_chunks = 0
    for report in reports:
        total_chunks += await ingest_report(report)
    stats = {
        "reports": len(reports),
        "chunks": total_chunks,
        "facts": len(all_facts(reports)),
    }
    log.info("ingest.done", extra={"context": stats})
    return stats


def embed_query(query: str) -> np.ndarray:
    return get_embedder().embed([query])[0]
