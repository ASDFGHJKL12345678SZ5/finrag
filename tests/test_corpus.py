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
    # distractors=True 时报告数 = 基准 + 易混干扰，唯一性按实际报告数校验
    assert len({r.source for r in reports}) == len(reports)
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
    # 每篇报告 3 条事实（含易混干扰报告）——不写死 90：
    # D13 起语料带干扰报告，事实数 = 报告数 × 3
    assert len(reg) == len(all_facts(reports)) == len(reports) * 3
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


# ---------- D13 分级评测的不变量（改造后新增） ----------


def _companies(reports) -> list[str]:
    return [r.title.split("2025")[0] for r in reports]


def test_易混公司名互不为子串():
    """ILike 全名匹配不能误伤：任何公司名不能是另一个的子串。

    实体锚定（detect_anchor/fuse）靠"公司名 in source"判别——若
    "华曜" 是 "华曜新能" 的子串，问华曜新能时会误锚到华曜的文档。
    这条不变量是锚定正确性的前提。
    """
    reports = build_corpus(n_reports=30, seed=42)
    companies = _companies(reports)
    assert len(companies) == len(set(companies))
    for a in companies:
        for b in companies:
            if a != b:
                assert a not in b and b not in a, f"公司名互为子串: {a} / {b}"


def test_易混干扰报告与基准同行业且关联正确():
    reports = build_corpus(n_reports=30, seed=42)
    siblings = [r for r in reports if r.confusable_with]
    assert siblings, "distractors=True 应生成易混干扰报告"
    for sib in siblings:
        base = next(r for r in reports if r.title.split("2025")[0] == sib.confusable_with)
        assert sib.industry == base.industry
        assert sib.title.split("2025")[0] != base.title.split("2025")[0]


def test_改写题与母题同源同解():
    """同义改写只许改问题文本，source/answer/证据必须一致——
    否则改写档的命中判定就失去了 ground truth。"""
    reports = build_corpus(n_reports=10, seed=7)
    n_variants = 0
    for f in all_facts(reports):
        assert f.variants, f"{f.fact_id} 缺改写题（D13 R2）"
        for v in f.variants:
            assert v != f.question
            n_variants += 1
        # 改写题复用母题判据：同 source 同 answer，且必须仍点名公司
        # （不点名公司的"那家公司营收如何"没有唯一 ground truth）
        company = f.source.split("-", 2)[2]
        for v in f.variants:
            assert company in v, f"改写题丢了公司实体，ground truth 不再唯一: {v}"
    assert n_variants >= 60


def test_无答案题的答案确实不在语料中():
    """三类无答案题（D13 R3）的 ground truth：年份/指标语料里没有。"""
    reports = build_corpus(n_reports=30, seed=42)
    corpus_text = " ".join(r.text for r in reports)
    from app.rag.corpus import build_hallucination_questions

    qs = build_hallucination_questions(reports)
    assert len(qs) == 30, "10 家公司 × 3 类"
    for hq in qs:
        if hq.htype == "wrong_year":
            assert "2024" in hq.question and "2024" not in corpus_text
        elif hq.htype == "future_prediction":
            assert "2026" in hq.question and "2026" not in corpus_text
        elif hq.htype == "missing_metric":
            assert "市值" in hq.question and "市值" not in corpus_text
        else:
            raise AssertionError(f"未知幻觉题型: {hq.htype}")


def test_无答案题不含mock幻觉触发词():
    """触发词撞上的 100% 拒答正是要修掉的假指标（README §4.5 诚实边界）。"""
    from app.rag.corpus import build_hallucination_questions

    reports = build_corpus(n_reports=30, seed=42)
    triggers = ("预测", "未来三年", "2030年", "股价目标")
    for hq in build_hallucination_questions(reports):
        assert not any(t in hq.question for t in triggers), (
            f"无答案题含 mock 触发词（拒答会是规则假模型的产物）: {hq.question}"
        )


def test_污染探针配对且答案不同():
    reports = build_corpus(n_reports=30, seed=42)
    from app.rag.corpus import build_contamination_pairs

    pairs = build_contamination_pairs(reports)
    assert pairs, "应生成污染探针对（D13 R4）"
    for p in pairs:
        assert p.company != p.distractor_company
        assert p.a_answer != p.b_answer, "A/B 数字相同则探针无意义"

