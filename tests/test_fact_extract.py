"""RW-1：聚合事实必须来自正文/表格，不能读 ground truth。"""
from __future__ import annotations

import ast
import asyncio
import os
from dataclasses import replace
from pathlib import Path

os.environ["EMBEDDING_MODE"] = "mock"

from app.rag.chunker import chunk_document
from app.rag.corpus import build_corpus
from app.rag.fact_extract import doc_from_report, extract_facts

ROOT = Path(__file__).resolve().parents[1]


def _offenders(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "facts":
            bad.append(f"{path.name}:{node.lineno} .facts 属性访问")
        if isinstance(node, ast.Name) and node.id == "all_facts":
            bad.append(f"{path.name}:{node.lineno} all_facts 引用")
        if isinstance(node, ast.ImportFrom) and node.module == "app.rag.corpus":
            for alias in node.names:
                if alias.name == "all_facts":
                    bad.append(f"{path.name}:{node.lineno} import all_facts")
    return bad


def test_生产路径不读ground_truth():
    offenders: list[str] = []
    for name in ("ingest.py", "fact_extract.py", "aggregate.py", "store.py"):
        offenders.extend(_offenders(ROOT / "app" / "rag" / name))
    assert not offenders, "生产路径出现 ground truth 引用：\n" + "\n".join(offenders)


def test_抽取正文与分业务表格各九条():
    reports = build_corpus(n_companies=2, years=(2024,), seed=42)
    for report in reports:
        doc = doc_from_report(report)
        rows = extract_facts(doc, chunk_document(doc.text, source=doc.source))
        assert len(rows) == 9
        metrics = [r["metric"] for r in rows]
        assert metrics.count("分业务收入") == 3
        # 与 ground truth 对账（测试代码允许读 facts）
        gt = {(f.metric, f.segment or None) for f in report.facts}
        ext = {(r["metric"], r["segment"] or None) for r in rows}
        assert gt == ext


def test_抽取准确率P_R():
    reports = build_corpus(n_companies=10, years=(2024, 2025), seed=7)
    tp = fp = fn = 0
    for report in reports:
        doc = doc_from_report(report)
        rows = extract_facts(doc, chunk_document(doc.text, source=doc.source))
        ext = {(r["metric"], r["segment"] or None): r for r in rows}
        gt = {(f.metric, f.segment or None): f for f in report.facts}
        for key, fact in gt.items():
            expected = float(
                "".join(ch for ch in fact.answer if ch.isdigit() or ch in ".-")
            )
            if key in ext and abs(ext[key]["value"] - expected) < 1e-9:
                tp += 1
            else:
                fn += 1
        for key in ext:
            if key not in gt:
                fp += 1
    assert fp == 0 and fn == 0
    assert tp / (tp + fp) == 1.0 and tp / (tp + fn) == 1.0


def _run(body):
    from app.rag import store

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


def test_改坏表格单元格聚合结果跟着变():
    """数据必须来自文本：把赢家营收改大后，聚合结果跟着变，而不是旧 facts。"""
    from app.rag import store
    from app.rag.ingest import ingest_report

    reports = build_corpus(n_companies=2, years=(2024,), seed=42)

    async def body():
        winner = max(reports, key=lambda r: float(r.facts[0].answer.replace("亿元", "")))
        original = winner.facts[0].answer
        mutated = replace(
            winner,
            text=winner.text.replace(
                f"实现营业收入{original}",
                "实现营业收入9999.99亿元",
            ),
        )
        assert mutated.text != winner.text
        for report in reports:
            await ingest_report(mutated if report is winner else report)
        row = await store.aggregate_facts("营业收入", 2024)
        assert row is not None
        assert row["value"] == 9999.99
        assert row["answer"] == "9999.99亿元"

    _run(body)
