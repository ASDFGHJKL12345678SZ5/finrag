"""从研报正文 / 表格抽取结构化事实（FR-10 返工：不得读取 ground truth）。

为什么必须这样（返工单 RW-1）：
旧版 `ingest` 直接把 `Report.facts`（语料生成器产出的**标准答案**）写进
`rag.facts`，聚合节点查表等于抄答案——industry_max 20/20 不能证明系统能从
文本里取数。本模块只接收 `text / source / year / company` 元信息，用
"指标词 + 数值 + 单位" 与 Markdown 表格解析事实；**抽不出来就不入表**。

评测代码（`app/eval/*`、tests）可以读 `Report.facts` 做 P/R 对账；生产入库
路径不允许 import corpus 的 `all_facts`，由 AST 不变量测试守死。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 正文里的六类指标：metric -> (正则, 单位)
_BODY_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("营业收入", re.compile(r"实现营业收入\s*([-\d,]+(?:\.\d+)?)\s*亿元"), "亿元"),
    ("同比增长", re.compile(r"同比增长\s*(-?\d+(?:\.\d+)?)\s*%"), "%"),
    ("归母净利润", re.compile(r"归母净利润\s*([-\d,]+(?:\.\d+)?)\s*亿元"), "亿元"),
    ("毛利率", re.compile(r"毛利率\s*(\d+(?:\.\d+)?)\s*%"), "%"),
    ("研发费用率", re.compile(r"研发费用率\s*(\d+(?:\.\d+)?)\s*%"), "%"),
    ("经营性现金流", re.compile(r"经营性现金流净额\s*([-\d,]+(?:\.\d+)?)\s*亿元"), "亿元"),
)
_TABLE_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([-\d,.]+)\s*(亿元)\s*\|\s*([\d.]+)\s*%\s*\|\s*$")


@dataclass(frozen=True)
class IngestDoc:
    """入库 DTO：只有文本与元信息，**没有 facts**。"""

    source: str
    title: str
    industry: str | None
    report_date: str | None
    text: str
    year: int
    company_short: str | None = None
    abbr: str | None = None
    code: str | None = None


def doc_from_report(report) -> IngestDoc:
    """从 Report 只取文本/元信息构建 DTO；不读 report.facts。"""
    company = getattr(report, "company", None)
    return IngestDoc(
        source=report.source,
        title=report.title,
        industry=report.industry,
        report_date=report.report_date,
        text=report.text,
        year=int(report.year or 0),
        company_short=getattr(company, "short", None),
        abbr=getattr(company, "abbr", None),
        code=getattr(company, "code", None),
    )


def _num(raw: str) -> float:
    return float(raw.replace(",", ""))


def _chunk_index_for(chunks: list, needle: str) -> int:
    for chunk in chunks:
        if needle in chunk.text:
            return int(chunk.chunk_index)
    return 0


def _body_facts(doc: IngestDoc, chunks: list) -> list[dict]:
    rows: list[dict] = []
    for metric, pattern, unit in _BODY_PATTERNS:
        match = pattern.search(doc.text)
        if not match:
            continue
        raw = match.group(1)
        answer = f"{raw}{unit}"
        rows.append({
            "fact_id": f"{doc.source}-{metric}",
            "year": doc.year,
            "metric": metric,
            "segment": None,
            "value": _num(raw),
            "unit": unit,
            "answer": answer,
            "chunk_index": _chunk_index_for(chunks, match.group(0)),
        })
    return rows


def _segment_facts(doc: IngestDoc, chunks: list) -> list[dict]:
    """只解析「分业务收入」小节里的 Markdown 表格（表头含“业务板块/收入”）。"""
    heading = re.search(r"#{2,4}\s*[^\n]*分业务收入[^\n]*\n", doc.text)
    if not heading:
        return []
    tail = doc.text[heading.end():]
    next_heading = re.search(r"\n#{2,4}\s", tail)
    block = tail[: next_heading.start()] if next_heading else tail
    rows: list[dict] = []
    for line in block.splitlines():
        m = _TABLE_ROW.match(line.strip())
        if not m:
            continue
        segment = m.group(1).strip()
        if segment in ("业务板块", "业务", "---"):
            continue
        raw_value = m.group(2)
        value = _num(raw_value)
        answer = f"{raw_value}亿元"
        rows.append({
            "fact_id": f"{doc.source}-segment-{segment}",
            "year": doc.year,
            "metric": "分业务收入",
            "segment": segment,
            "value": value,
            "unit": "亿元",
            "answer": answer,
            "chunk_index": _chunk_index_for(chunks, line.strip()),
        })
    return rows


def extract_facts(doc: IngestDoc, chunks: list) -> list[dict]:
    """正文 + 分业务表格 -> rag.facts 行。抽不出来就不入表。"""
    return [*_body_facts(doc, chunks), *_segment_facts(doc, chunks)]
