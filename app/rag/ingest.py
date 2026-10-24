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
    company = report.company
    meta = (
        {"abbr": company.abbr, "short": company.short, "code": company.code}
        if company is not None
        else {}
    )
    await store.upsert_document(
        source=report.source,
        title=report.title,
        industry=report.industry,
        report_date=report.report_date,
        meta=meta,
    )
    await store.upsert_facts(report.source, _fact_rows(report))
    return await store.upsert_chunks(chunks, vectors)


def _parse_fact_value(answer: str) -> tuple[float, str] | None:
    """从 Fact.answer 抽数值与单位（"-13.1%" / "518.75亿元" / "1,234.5亿元"）。"""
    import re

    text = answer.replace(",", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    unit = "%" if "%" in text else ("亿元" if "亿元" in text else "")
    return float(match.group()), unit


def _fact_rows(report) -> list[dict]:
    """Report.facts -> rag.facts 行（FR-10 跨文档聚合的数据源）。"""
    rows: list[dict] = []
    year = report.year or 0
    for fact in report.facts:
        parsed = _parse_fact_value(fact.answer)
        if parsed is None:
            continue
        value, unit = parsed
        rows.append({
            "fact_id": fact.fact_id,
            "year": year,
            "metric": "分业务收入" if fact.segment else fact.metric,
            "segment": fact.segment or None,
            "value": value,
            "unit": unit,
            "answer": fact.answer,
        })
    return rows


async def ingest_corpus(
    n_companies: int = 200,
    years: tuple[int, ...] | None = None,
    seed: int = 20260929,
) -> dict:
    """整库入库（默认 200 公司 × 5 年 = 1000 篇）。返回统计。"""
    reports = build_corpus(n_companies=n_companies, years=years, seed=seed)
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


async def embed_query_async(query: str) -> np.ndarray:
    """embed_query 的线程池版本（FR-5）。

    ONNX 推理是**同步 CPU 计算**，直接放在事件循环里会让整个 worker 在
    每次检索时停摆几十到几百毫秒——并发请求全部排队（低并发测不出来，
    /ask 并发一上来 P95 就炸）。用 asyncio.to_thread 把阻塞计算挪到默认
    线程池，事件循环只负责等 Future。
    """
    return await asyncio.to_thread(embed_query, query)
