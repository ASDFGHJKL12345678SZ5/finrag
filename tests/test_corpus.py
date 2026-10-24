"""语料合法性 + 难基准不变量（FR-1）。

信任根：每条 ground truth 必须能在语料里回溯；题库不得把公司全名写进问题；
拒答题问的年份/指标/未来措辞必须真的不在语料里。
"""
from __future__ import annotations

import re

import pytest

from app.rag.corpus import (
    UNSUPPORTED_METRICS,
    WRONG_YEARS,
    all_facts,
    build_benchmark,
    build_companies,
    build_corpus,
    build_registry,
)

BODY_METRICS = ("营业收入", "同比增长", "归母净利润", "毛利率", "研发费用率", "经营性现金流")


@pytest.fixture(scope="module")
def reports():
    return build_corpus(n_companies=5, years=(2023, 2024, 2025), seed=7)


@pytest.fixture(scope="module")
def full_reports():
    return build_corpus()


@pytest.fixture(scope="module")
def benchmark(full_reports):
    return build_benchmark(full_reports)


def test_语料确定性():
    a = build_corpus(n_companies=3, years=(2024, 2025), seed=42)
    b = build_corpus(n_companies=3, years=(2024, 2025), seed=42)
    assert [r.text for r in a] == [r.text for r in b]
    assert [r.source for r in a] == [r.source for r in b]
    c = build_corpus(n_companies=3, years=(2024, 2025), seed=43)
    assert [r.text for r in a] != [r.text for r in c]


def test_公司池200家_40组易混_简称与缩写唯一():
    companies = build_companies()
    assert len(companies) == 200
    assert len({c.group for c in companies}) == 40  # 计划要求 ≥30 组
    assert len({c.short for c in companies}) == 200
    assert len({c.abbr for c in companies}) == 200
    for c in companies:
        assert c.short in c.full
        assert len(c.abbr) == 3
        assert c.abbr not in c.short, "缩写不能是简称子串（锚定会错位）"
    shorts = [c.short for c in companies]
    for a in shorts:
        for b in shorts:
            assert a == b or (a not in b and b not in a), f"简称互为子串: {a}/{b}"


def test_每篇9条事实_证据词可回溯(reports):
    by_source = {r.source: r.text for r in reports}
    assert len(reports) == 15  # 5 公司 × 3 年
    for r in reports:
        assert len(r.facts) == 9
        assert len([f for f in r.facts if f.table_only]) == 3
        assert len([f for f in r.facts if not f.table_only]) == 6
        text = by_source[r.source]
        for f in r.facts:
            for term in f.evidence_terms:
                assert term in text, f"{f.fact_id} 的证据词 {term!r} 不在语料中"


def test_分业务事实只出现在表格里(reports):
    for r in reports:
        table_only = [f for f in r.facts if f.table_only]
        for f in table_only:
            value = f.answer
            assert r.text.count(value) == 1, f"{f.fact_id} 的值 {value} 出现不止一次"
            pos = r.text.find(value)
            line = r.text[:pos].rsplit("\n", 1)[-1]
            assert "|" in line, "分业务值必须只在 Markdown 表格行里"


def test_source唯一且带年份(reports):
    assert len({r.source for r in reports}) == len(reports)
    for r in reports:
        assert r.source.endswith(f"-{r.year}")
        assert str(r.year) in r.title


def test_fact_registry与all_facts同源(full_reports):
    reg = build_registry(full_reports)
    assert len(reg) == len(all_facts(full_reports)) == 9000
    by_id = {f.fact_id: f for f in all_facts(full_reports)}
    for question, fact in reg.questions():
        assert by_id[fact.fact_id] is fact
        assert question == fact.question


def test_题库800题分类数量(benchmark):
    assert len(benchmark) == 800
    cats = {c: sum(1 for q in benchmark if q.category == c)
            for c in ("exact", "abbr", "calculation", "refusal")}
    assert cats == {"exact": 300, "abbr": 300, "calculation": 100, "refusal": 100}
    subtypes = {q.subtype for q in benchmark if q.category == "calculation"}
    assert {"yoy", "yoy_diff", "segment_share", "industry_max"} <= subtypes


def test_问题不写公司全名(benchmark):
    full_names = [c.full for c in build_companies()]
    for q in benchmark:
        assert not any(name in q.question for name in full_names), q.question


def test_简称档含缩写或同义说法(benchmark, full_reports):
    abbr_map = {r.source: r.company.abbr for r in full_reports if r.company}
    abbr_qs = [q for q in benchmark if q.category == "abbr"]
    assert abbr_qs
    n_abbr = sum(1 for q in abbr_qs if abbr_map.get(q.source) in q.question)
    assert n_abbr >= len(abbr_qs) * 0.4, "至少四成改写题用 3 字缩写"


def test_计算题答案可回溯(benchmark, full_reports):
    by_source = {r.source: r.text for r in full_reports}
    for q in benchmark:
        if q.category != "calculation":
            continue
        assert q.source in by_source
        if q.answer in by_source[q.source]:
            continue
        # 计算值（同比/差值/占比）可能只以两个操作数的形式出现：检查操作数在源文里
        assert re.search(r"\d", q.answer)
        src_nums = {round(float(n), 2) for n in re.findall(r"\d+(?:\.\d+)?", by_source[q.source])}
        ans_nums = [round(float(n), 2) for n in re.findall(r"\d+(?:\.\d+)?", q.answer)]
        assert all(n in src_nums for n in ans_nums), (q.answer, ans_nums)


def test_拒答题问的年份指标确实不在语料(full_reports):
    corpus_text = " ".join(r.text for r in full_reports)
    for hq in (q for q in build_benchmark(full_reports) if q.category == "refusal"):
        if hq.subtype == "wrong_year":
            assert any(str(y) in hq.question for y in WRONG_YEARS)
        elif hq.subtype == "missing_metric":
            assert any(m in hq.question for m in UNSUPPORTED_METRICS)
        else:
            assert any(k in hq.question for k in ("预测", "明年", "未来"))
        for y in WRONG_YEARS:
            assert f"{y}年" not in corpus_text
        for metric in UNSUPPORTED_METRICS:
            assert metric not in corpus_text


def test_拒答题不含mock幻觉触发词(benchmark):
    triggers = ("预测", "未来三年", "2030年", "股价目标")
    refusal_qs = [q for q in benchmark if q.category == "refusal"]
    # 未来预测题允许"预测/未来"（这正是守卫要拦的），但错年份/缺指标题不行
    for q in refusal_qs:
        if q.subtype == "future_prediction":
            continue
        assert not any(t in q.question for t in triggers), q.question


def test_行业极值题不给公司名(benchmark):
    for q in benchmark:
        if q.category == "calculation" and q.subtype == "industry_max":
            assert q.answer not in q.question, "极值题不能把答案公司名写进问题"
