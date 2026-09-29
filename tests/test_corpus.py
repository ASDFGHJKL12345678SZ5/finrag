"""语料合法性测试：ground truth 与语料必须同源一致。

这是评测闭环的信任根：如果登记的事实在语料里找不到，
D6 的"答案正确性"判定就是空中楼阁。每条事实的 evidence_terms
必须能在对应研报文本里找到。
"""
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
