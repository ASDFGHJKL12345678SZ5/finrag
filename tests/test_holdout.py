"""RW-2 FinRAG 留出集：规模/类别/未覆盖指标与未来措辞的自检。"""
from app.eval.holdout import HOLDOUT_CORPUS_SEED, build_holdout
from app.rag.corpus import build_corpus


def _bench():
    return build_holdout(build_corpus(seed=HOLDOUT_CORPUS_SEED))


def test_留出集200题与类别分布():
    bench = _bench()
    assert len(bench) == 200
    from collections import Counter

    counts = Counter(q.category for q in bench)
    assert counts == {"exact": 80, "abbr": 60, "calculation": 20, "refusal": 40}


def test_拒答包含现有词表外的指标与未来措辞():
    refusal = [q for q in _bench() if q.category == "refusal"]
    metrics = {"ROE", "资产负债率", "净资产收益率", "存货周转率", "员工人数"}
    assert any(m in q.question for q in refusal for m in metrics)
    futures = {"下一年度", "接下来", "会不会涨"}
    assert any(f in q.question for q in refusal for f in futures)


def test_问题不写公司全名():
    from app.rag.corpus import build_companies

    fulls = [c.full for c in build_companies()]
    for q in _bench():
        assert not any(full in q.question for full in fulls)
