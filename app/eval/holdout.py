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

import json
import random
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from app.rag.corpus import (
    BenchQuestion,
    Report,
    all_facts,
    build_companies,
    build_corpus,
)
from app.rag.metric_lexicon import is_future_question

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


# ================================================================ 留出集 v2（R2）
#
# 出题人：独立隔离会话。它只拿到 `corpus_format.md`（语料字段格式）与
# `assignment.md`（200 题规格），没有 v1 题目、失败明细或实现代码。
# 它会话产出的模板/候选清单逐字落在 `holdout_v2_spec.json`（本文件不改写题面），
# 这里只做实例化与机械选择：
# - exact 80 / abbr 60：只用 v1 未出现过的 4 字简称与 3 字缩写（v1 用过的名字
#   由确定性重建 v1 题库得到，不手写清单）；
# - calculation 20：8 yoy + 6 segment_share + 6 industry_max；industry_max
#   不带“行业”二字，用来验证 R1 的元数据驱动识别；
# - refusal 40：20 指标 + 10 未来 + 10 错年份。20 个指标从作者给的 30 个候选里
#   机械分成“当前守卫抓不到”（形态外）与“抓得到”（对照组），前者放进 v2 测边界。
#
# 语料种子 20261111（训练 20260929 / v1 20261026），数字全变。

HOLDOUT_V2_CORPUS_SEED = 20261111
_V2_SPEC_PATH = Path(__file__).with_name("holdout_v2_spec.json")
# 训练集与 v1 用过的错年份：v2 尽量避开，换 2009/2011/2013–2017/2028–2030
_V2_YEAR_EXCLUDE = frozenset({2018, 2019, 2020, 2026, 2027})


@lru_cache(maxsize=1)
def _v2_spec() -> dict:
    return json.loads(_V2_SPEC_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def v1_used_names() -> tuple[frozenset[str], frozenset[str]]:
    """v1 用过的 4 字简称 / 3 字缩写（确定性重建 v1 题库，不手写清单）。"""
    v1 = build_holdout(build_corpus(seed=HOLDOUT_CORPUS_SEED))
    by_short = {c.short: c for c in build_companies()}
    shorts = {
        q.source.split("-")[2]
        for q in v1
        if q.source and q.source.count("-") >= 2
    }
    abbrs = {
        by_short[q.source.split("-")[2]].abbr
        for q in v1
        if q.category == "abbr" and q.source and q.source.count("-") >= 2
    }
    return frozenset(shorts), frozenset(abbrs)


def _usable_templates(templates: list[str], required: tuple[str, ...]) -> list[str]:
    out = [t for t in templates if all("{" + p + "}" in t for p in required)]
    if not out:
        raise ValueError(f"没有可用模板（需要占位符 {required}）：{templates}")
    return out


def _split_even(total: int, parts: int) -> list[int]:
    base, rem = divmod(total, parts)
    return [base + (1 if i < rem else 0) for i in range(parts)]


def _one_per_company(facts, by_source, rng):
    """每个公司只留一条事实再打散（exact/abbr 用，避免同一简称重复）。

    注意：返回的 report 必须是**该 fact 自己的**那篇研报（年份/答案同源）；
    早期版误用了同公司第一条事实的 report，导致题面年份与金标年份错位。
    """
    by_company: dict[str, list] = defaultdict(list)
    for fact in sorted(facts, key=lambda f: f.fact_id):
        by_company[by_source[fact.source].company.short].append(fact)
    picks = []
    for items in by_company.values():
        fact = rng.choice(items)
        picks.append((fact, by_source[fact.source]))
    rng.shuffle(picks)
    return picks


def _one_per_source(facts, rng):
    """每篇研报只留一条事实再打散（yoy/share 用，避免同一文档重复）。"""
    by_source: dict[str, list] = defaultdict(list)
    for fact in sorted(facts, key=lambda f: f.fact_id):
        by_source[fact.source].append(fact)
    picks = [rng.choice(items) for items in by_source.values()]
    rng.shuffle(picks)
    return picks


# 冻结的 refusal 指标集：v2 首跑（commit bc3f55c）实际使用的 20 题。
# 为什么不再按当前守卫重算：Q2 修复后 `员工总数` 会从“形态内”变“形态外”，
# 若运行时重新分类，代码一改测试集就跟着变——留出集必须冻结。
_V2_REFUSAL_METRICS = (
    "净利率", "资本开支", "自由现金流", "研发费用", "政府补助",
    "汇兑损益", "在手订单",
    "资产负债率", "净资产收益率", "每股收益", "每股净资产",
    "销售费用率", "管理费用率", "财务费用率", "存货周转率",
    "应收账款周转天数", "流动比率", "速动比率", "总资产", "净资产",
)
_V2_BOUNDARY_METRICS = frozenset(_V2_REFUSAL_METRICS[:7])


def _select_refusal_metrics(metrics: list[str]) -> tuple[list[str], list[str]]:
    """返回冻结的 refusal 指标顺序与“形态外”集合（参数仅为兼容旧调用）。"""
    return list(_V2_REFUSAL_METRICS), list(_V2_BOUNDARY_METRICS)


def _select_future_phrases(phrases: list[str]) -> tuple[list[str], list[str]]:
    escaped = [p for p in phrases if not is_future_question(p)]
    marked = [p for p in phrases if is_future_question(p)]
    picked = escaped[:5]
    picked += marked[: 10 - len(picked)]
    if len(picked) < 10:
        picked += [p for p in phrases if p not in picked][: 10 - len(picked)]
    return picked[:10], escaped


def build_holdout_v2(reports: list[Report]) -> list[BenchQuestion]:
    """R2 留出集 v2：200 题 = exact 80 / abbr 60 / calculation 20 / refusal 40。"""
    spec = _v2_spec()
    rng = random.Random("holdout-finrag-v2")
    v1_shorts, v1_abbrs = v1_used_names()
    indexed = [r for r in reports if r.company is not None]
    by_source = {r.source: r for r in indexed}
    body = [f for f in all_facts(reports) if not f.table_only and f.source in by_source]
    fresh = [f for f in body if by_source[f.source].company.short not in v1_shorts]
    questions: list[BenchQuestion] = []

    # ---- exact 80：新简称 + 作者的指标说法 + 作者模板 ----
    metrics = list(spec["exact"]["metric_labels"])
    exact_templates = _usable_templates(
        spec["exact"]["templates"], ("short", "year", "metric")
    )
    for i, (metric, k) in enumerate(
        zip(metrics, _split_even(80, len(metrics)), strict=True)
    ):
        picks = _one_per_company(
            [f for f in fresh if f.metric == metric], by_source, rng
        )[:k]
        if len(picks) < k:
            raise ValueError(f"exact 公司不足：{metric} 需要 {k}，只有 {len(picks)}")
        labels = spec["exact"]["metric_labels"][metric]
        for j, (fact, report) in enumerate(picks):
            label = labels[j % len(labels)]
            template = exact_templates[(i + j) % len(exact_templates)]
            questions.append(BenchQuestion(
                qid=f"v2-exact-{len(questions):04d}", category="exact",
                question=template.format(
                    short=report.company.short,  # type: ignore[union-attr]
                    year=report.year, metric=label,
                ),
                source=fact.source, answer=fact.answer,
                evidence_terms=list(fact.evidence_terms), expected="answer",
                subtype=metric,
            ))

    # ---- abbr 60：v1 未用过的 3 字缩写，每指标 10 题 ----
    labels_by_metric = spec["abbr"]["metric_labels"]
    templates = _usable_templates(
        spec["abbr"]["templates"], ("abbr", "year", "metric")
    )
    used_abbrs: set[str] = set()
    for i, metric in enumerate(metrics):
        picked = []
        for fact, report in _one_per_company(
            [f for f in fresh if f.metric == metric], by_source, rng
        ):
            abbr = report.company.abbr  # type: ignore[union-attr]
            if abbr in v1_abbrs or abbr in used_abbrs:
                continue
            used_abbrs.add(abbr)
            picked.append((fact, report))
            if len(picked) == 10:
                break
        if len(picked) < 10:
            raise ValueError(f"abbr 新缩写不足：{metric} 只有 {len(picked)}")
        labels = labels_by_metric[metric]
        for j, (fact, report) in enumerate(picked):
            label = labels[j % len(labels)]
            template = templates[(i + j) % len(templates)]
            questions.append(BenchQuestion(
                qid=f"v2-abbr-{len(questions):04d}", category="abbr",
                question=template.format(
                    abbr=report.company.abbr,  # type: ignore[union-attr]
                    year=report.year, metric=label,
                ),
                source=fact.source, answer=fact.answer,
                evidence_terms=list(fact.evidence_terms), expected="answer",
                subtype="abbr",
            ))

    # ---- calculation 20：8 yoy + 6 share + 6 industry_max ----
    yoy_templates = _usable_templates(
        spec["calculation"]["yoy"]["templates"], ("short", "year")
    )
    yoy_picks = _one_per_source(
        [f for f in fresh if f.metric == "同比增长"], rng
    )[:8]
    if len(yoy_picks) < 8:
        raise ValueError(f"yoy 文档不足：{len(yoy_picks)}")
    for j, fact in enumerate(yoy_picks):
        report = by_source[fact.source]
        questions.append(BenchQuestion(
            qid=f"v2-calc-{len(questions):04d}", category="calculation",
            question=yoy_templates[j % len(yoy_templates)].format(
                short=report.company.short,  # type: ignore[union-attr]
                year=report.year,
            ),
            source=fact.source, answer=fact.answer,
            evidence_terms=list(fact.evidence_terms), expected="answer",
            subtype="yoy",
        ))

    share_templates = _usable_templates(
        spec["calculation"]["segment_share"]["templates"],
        ("short", "year", "segment"),
    )
    share_pool = [
        f for f in all_facts(reports)
        if f.table_only and f.source in by_source
        and by_source[f.source].company.short not in v1_shorts
    ]
    share_picks = _one_per_source(share_pool, rng)[:6]
    if len(share_picks) < 6:
        raise ValueError(f"share 文档不足：{len(share_picks)}")
    for j, fact in enumerate(share_picks):
        report = by_source[fact.source]
        answer = f"{fact.share:.2f}%"
        questions.append(BenchQuestion(
            qid=f"v2-calc-{len(questions):04d}", category="calculation",
            question=share_templates[j % len(share_templates)].format(
                short=report.company.short,  # type: ignore[union-attr]
                year=report.year, segment=fact.segment,
            ),
            source=fact.source, answer=answer,
            evidence_terms=[fact.segment, answer], expected="answer",
            subtype="segment_share",
        ))

    industry_templates = _usable_templates(
        spec["calculation"]["industry_max"]["templates"], ("industry", "year")
    )
    combos = sorted({(r.industry, r.year) for r in indexed})
    valid_combos: list[tuple[str, int]] = []
    for combo in combos:
        candidates = [
            r for r in indexed if r.industry == combo[0] and r.year == combo[1]
        ]
        top = max(r.revenue for r in candidates)
        if sum(1 for r in candidates if r.revenue == top) == 1:
            valid_combos.append(combo)
    by_industry: dict[str, list] = defaultdict(list)
    for combo in valid_combos:
        by_industry[combo[0]].append(combo)
    if len(by_industry) != 5:
        raise ValueError(f"有效行业组合覆盖不足 5 个行业：{sorted(by_industry)}")
    chosen: list[tuple[str, int]] = [
        rng.choice(items) for _industry, items in sorted(by_industry.items())
    ]
    chosen += _pick(rng, [c for c in valid_combos if c not in chosen], 1)
    for j, (industry, year) in enumerate(chosen[:6]):
        candidates = [
            r for r in indexed if r.industry == industry and r.year == year
        ]
        winner = max(candidates, key=lambda r: r.revenue)
        assert winner.company is not None
        questions.append(BenchQuestion(
            qid=f"v2-calc-{len(questions):04d}", category="calculation",
            question=industry_templates[j % len(industry_templates)].format(
                industry=industry, year=year,
            ),
            source=winner.source, answer=winner.company.short,
            evidence_terms=[
                winner.company.short, "营业收入", f"{winner.revenue:.2f}亿元",
            ],
            expected="answer", subtype="industry_max",
        ))
    if sum(1 for q in questions if q.subtype == "industry_max") != 6:
        raise ValueError("industry_max 组题数不足 6（有并列或数据不足）")

    # ---- refusal 40：20 指标 + 10 未来 + 10 错年份 ----
    refusal = spec["refusal"]
    picked_metrics, boundary = _select_refusal_metrics(
        list(refusal["unsupported_metrics"])
    )
    shorts = sorted({by_source[f.source].company.short for f in fresh})
    for i, metric in enumerate(picked_metrics):
        questions.append(BenchQuestion(
            qid=f"v2-ref-{len(questions):04d}", category="refusal",
            question=f"{shorts[i % len(shorts)]}{2021 + i % 5}年的{metric}是多少？",
            source=None, answer=None, evidence_terms=[], expected="refuse",
            subtype="boundary_metric" if metric in boundary else "shaped_metric",
        ))

    picked_future, escaped = _select_future_phrases(
        list(refusal["future_phrases"])
    )
    for i, phrase in enumerate(picked_future):
        questions.append(BenchQuestion(
            qid=f"v2-ref-{len(questions):04d}", category="refusal",
            question=f"{shorts[(i + 11) % len(shorts)]}{phrase}",
            source=None, answer=None, evidence_terms=[], expected="refuse",
            subtype="future_escaped" if phrase in escaped else "future_marked",
        ))

    wrong_years = [
        int(y) for y in refusal["wrong_years"] if int(y) not in _V2_YEAR_EXCLUDE
    ]
    if len(wrong_years) < 10:
        wrong_years = [int(y) for y in refusal["wrong_years"]]
    for i, year in enumerate(wrong_years[:10]):
        questions.append(BenchQuestion(
            qid=f"v2-ref-{len(questions):04d}", category="refusal",
            question=f"{shorts[(i + 23) % len(shorts)]}{year}年的总收入是多少？",
            source=None, answer=None, evidence_terms=[], expected="refuse",
            subtype="wrong_year",
        ))

    if len(questions) != 200:
        raise ValueError(f"留出集 v2 题数 {len(questions)} != 200")
    if len({q.question for q in questions}) != 200:
        raise ValueError("留出集 v2 出现重复题面")
    # 不变量：可答题的题面年份必须与源研报年份一致（题面/金标同源）
    for q in questions:
        if q.expected == "answer":
            source_year = str(by_source[q.source].year)
            if source_year not in q.question:
                raise ValueError(
                    f"{q.qid} 题面年份与源研报不一致：{q.question} -> {q.source}"
                )
    return questions
