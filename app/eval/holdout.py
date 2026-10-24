"""FinRAG 留出集 v1（RW-2）：换语料种子 + 换问句模板的 200 题。

出题约束：
- 语料种子 **20261026**（训练集用 20260929/42），数字全变；
- 问句模板不复用 `app.rag.corpus.build_benchmark`（训练模板），改用
  “为多少 / 请给出 / 较上年增长率 / 占营业收入百分比 / 营收居首”等新说法；
- 拒答档包含 `METRIC_GROUPS/UNSUPPORTED_METRICS` 之外的指标（ROE、资产负债率、
  净资产收益率、存货周转率、员工人数…）和 `FUTURE_MARKERS` 之外的未来措辞
  （下一年度、接下来、会不会涨…）。
- 本文件在跑评测前提交；跑完不得针对失败题改代码（要改只能 holdout v2）。
"""
from __future__ import annotations

import random

from app.rag.corpus import BenchQuestion, Report, all_facts

HOLDOUT_CORPUS_SEED = 20261026

_EXTRA_METRICS = (
    "ROE", "资产负债率", "净资产收益率", "存货周转率", "员工人数",
    "研发人员数量", "客户数", "市占率", "分红率", "商誉",
    "有息负债", "经营杠杆", "速动比率", "现金比率", "应收账款周转天数",
)
_EXTRA_FUTURE = (
    "下一年度", "接下来", "会不会涨", "明年上半年", "未来 12 个月",
    "后续走势", "中长期", "来年", "今后三年", "下一财年",
)


def _pick(rng: random.Random, items: list, n: int) -> list:
    rng.shuffle(items)
    return items[:n]


def build_holdout(reports: list[Report]) -> list[BenchQuestion]:
    facts = [f for f in all_facts(reports) if not f.table_only]
    rng = random.Random("holdout-finrag-v1")
    by_source = {r.source: r for r in reports}
    questions: list[BenchQuestion] = []

    # exact 80：新问句模板
    for fact in _pick(rng, facts, 80):
        report = by_source[fact.source]
        label = fact.metric
        template = rng.choice((
            f"{report.company.short}{report.year}年的{label}为多少？",
            f"请给出{report.company.short}{report.year}年的{label}",
            f"查询{report.company.short}{report.year}年的{label}",
        ))
        questions.append(BenchQuestion(
            qid=f"h-exact-{len(questions):04d}", category="exact",
            question=template, source=fact.source, answer=fact.answer,
            evidence_terms=list(fact.evidence_terms), expected="answer",
            subtype=fact.metric,
        ))

    # abbr 60：3 字缩写 + 新说法
    for fact in _pick(rng, facts, 60):
        report = by_source[fact.source]
        abbr = report.company.abbr
        label = fact.metric
        question = rng.choice((
            f"{abbr}在{report.year}年的{label}是多少？",
            f"{abbr}{report.year}年{label}为多少？",
            f"请查{abbr}{report.year}年的{label}",
        ))
        questions.append(BenchQuestion(
            qid=f"h-abbr-{len(questions):04d}", category="abbr",
            question=question, source=fact.source, answer=fact.answer,
            evidence_terms=list(fact.evidence_terms), expected="answer",
            subtype="abbr",
        ))

    # calculation 20：8 yoy + 6 share + 6 industry max（新措辞）
    growth = [f for f in facts if f.metric == "同比增长"]
    for fact in _pick(rng, growth, 8):
        report = by_source[fact.source]
        questions.append(BenchQuestion(
            qid=f"h-calc-{len(questions):04d}", category="calculation",
            question=f"{report.company.short}{report.year}年营业收入较上年的增长率是多少？",
            source=fact.source, answer=fact.answer,
            evidence_terms=list(fact.evidence_terms), expected="answer",
            subtype="yoy",
        ))
    shares = [f for f in all_facts(reports) if f.table_only]
    for fact in _pick(rng, shares, 6):
        report = by_source[fact.source]
        questions.append(BenchQuestion(
            qid=f"h-calc-{len(questions):04d}", category="calculation",
            question=(
                f"{report.company.short}{report.year}年{fact.segment}收入"
                "占营业收入的百分比是多少？"
            ),
            source=fact.source, answer=f"{fact.share:.2f}%",
            evidence_terms=[fact.segment, f"{fact.share:.2f}%"],
            expected="answer", subtype="segment_share",
        ))
    combos = [(r.industry, r.year) for r in reports]
    for industry, year in _pick(rng, combos, 6):
        candidates = [
            r for r in reports if r.industry == industry and r.year == year
        ]
        if not candidates:
            continue
        winner = max(candidates, key=lambda r: r.revenue)
        questions.append(BenchQuestion(
            qid=f"h-calc-{len(questions):04d}", category="calculation",
            question=f"{industry}{year}年营收居首的公司是哪家？",
            source=winner.source, answer=winner.company.short,
            evidence_terms=[winner.company.short, f"{winner.revenue:.2f}亿元"],
            expected="answer", subtype="industry_max",
        ))

    # refusal 40：10 新指标 + 10 新未来措辞 + 10 错年份 + 10 新指标
    shorts = sorted({r.company.short for r in reports})
    for i in range(10):
        short = shorts[i % len(shorts)]
        metric = _EXTRA_METRICS[i]
        questions.append(BenchQuestion(
            qid=f"h-ref-{len(questions):04d}", category="refusal",
            question=f"{short}2025年的{metric}是多少？",
            source=None, answer=None, evidence_terms=[],
            expected="refuse", subtype="extra_metric",
        ))
    for i in range(10):
        short = shorts[(i + 3) % len(shorts)]
        phrase = _EXTRA_FUTURE[i]
        questions.append(BenchQuestion(
            qid=f"h-ref-{len(questions):04d}", category="refusal",
            question=f"{phrase}，{short}的营业收入会怎样？",
            source=None, answer=None, evidence_terms=[],
            expected="refuse", subtype="extra_future",
        ))
    for i in range(10):
        short = shorts[(i + 7) % len(shorts)]
        year = (2018, 2019, 2020, 2026, 2027)[i % 5]
        questions.append(BenchQuestion(
            qid=f"h-ref-{len(questions):04d}", category="refusal",
            question=f"{short}{year}年的营业收入为多少？",
            source=None, answer=None, evidence_terms=[],
            expected="refuse", subtype="wrong_year",
        ))
    for i in range(10):
        short = shorts[(i + 11) % len(shorts)]
        metric = _EXTRA_METRICS[(i + 10) % len(_EXTRA_METRICS)]
        questions.append(BenchQuestion(
            qid=f"h-ref-{len(questions):04d}", category="refusal",
            question=f"请给出{short}2024年的{metric}",
            source=None, answer=None, evidence_terms=[],
            expected="refuse", subtype="extra_metric",
        ))

    return questions
