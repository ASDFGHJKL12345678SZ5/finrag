"""语料合法性测试：ground truth 与语料必须同源一致。

这是评测闭环的信任根：如果登记的事实在语料里找不到，
D6 的"答案正确性"判定就是空中楼阁。每条事实的 evidence_terms
必须能在对应研报文本里找到。
"""
import re

import pytest

from app.rag.corpus import all_facts, build_corpus


def test_语料确定性():
    a = build_corpus(n_reports=6, seed=42)
    b = build_corpus(n_reports=6, seed=42)
    assert [r.text for r in a] == [r.text for r in b]
    assert [r.source for r in a] == [r.source for r in b]


def test_不同种子不同语料():
    a = build_corpus(n_reports=6, seed=1)
    b = build_corpus(n_reports=6, seed=2)
    assert [r.text for r in a] != [r.text for r in b]


def test_事实全部可在语料中回溯():
    reports = build_corpus(n_reports=10, seed=7)
    by_source = {r.source: r.text for r in reports}
    facts = all_facts(reports)
    assert len(facts) > 0
    for f in facts:
        text = by_source[f.source]
        for term in f.evidence_terms:
            assert term in text, f"事实 {f.fact_id} 的证据词 [{term}] 不在语料中"


def test_行业覆盖与source唯一():
    reports = build_corpus(n_reports=15, seed=3)
    assert len({r.source for r in reports}) == 15
    assert len({r.industry for r in reports}) >= 3

def test_同一公司不重复出报告():
    """数据合法性：同名多篇会让事实问题有多个答案，ground truth 歧义。

    实测教训：30 篇语料里春山乳业出现 3 次（不同数字），
    "春山乳业营业收入是多少"有 3 个答案，评测 6 个 miss 全源于此。
    """
    reports = build_corpus(n_reports=30, seed=42)
    companies = [r.title.split("2025")[0] for r in reports]
    assert len(companies) == len(set(companies)), (
        f"公司重复: {[c for c in companies if companies.count(c) > 1]}"
    )


def test_事实答案数字能在语料文本中找到():
    """ground truth 可回溯：标准答案里的数字必须出现在报告正文里。"""
    reports = build_corpus(n_reports=5, seed=7)
    for f in all_facts(reports):
        src = next(r for r in reports if r.source == f.source)
        for num in re.findall(r"\d+(?:\.\d+)?", f.answer):
            assert num in src.text, f"{f.fact_id} 的数字 {num} 不在语料中"

def test_fact_registry与all_facts同源():
    """FactRegistry（最终审查落地）与 all_facts 必须完全一致。"""
    from app.rag.corpus import all_facts, build_corpus, build_registry

    reports = build_corpus()
    reg = build_registry(reports)
    assert len(reg) == len(all_facts(reports)) == 90
    by_id = {f.fact_id: f for f in all_facts(reports)}
    for q, fact in reg.questions():
        assert by_id[fact.fact_id] is fact


def test_fact_registry拒绝重复登记():
    """fact_id 重复是语料生成 bug——登记表必须在入口处炸出来。"""
    from app.rag.corpus import Fact, FactRegistry

    reg = FactRegistry()
    fact = Fact(fact_id="f1", source="s", question="q", answer="a", evidence_terms=[])
    reg.register(fact)
    with pytest.raises(ValueError, match="重复登记"):
        reg.register(fact)
