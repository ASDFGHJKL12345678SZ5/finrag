"""R2 FinRAG 留出集 v2 的自检：配比 / 命名 / 字面 / 聚合识别 / 拒答边界。"""
from __future__ import annotations

from collections import Counter

import pytest

from app.eval.holdout import (
    HOLDOUT_V2_CORPUS_SEED,
    build_holdout_v2,
    v1_used_names,
)
from app.rag.aggregate import detect_aggregate
from app.rag.corpus import build_companies, build_corpus


@pytest.fixture(scope="module")
def bench():
    return build_holdout_v2(build_corpus(seed=HOLDOUT_V2_CORPUS_SEED))


def test_留出集v2两百题与类别分布(bench):
    assert len(bench) == 200
    assert Counter(q.category for q in bench) == {
        "exact": 80, "abbr": 60, "calculation": 20, "refusal": 40,
    }
    assert len({q.qid for q in bench}) == 200
    assert len({q.question for q in bench}) == 200


def test_不写全名且过半不含高频字面(bench):
    fulls = [c.full for c in build_companies()]
    assert not any(f in q.question for q in bench for f in fulls)
    text = " ".join(q.question for q in bench)
    dirty = sum(1 for q in bench if any(w in q.question for w in ("行业", "营收", "营业收入")))
    assert dirty < len(bench) / 2, f"含高频字面 {dirty}/200（要求 <50%）"
    assert text.count("行业") == 0


def test_只用v1未出现过的简称与缩写(bench):
    v1_shorts, v1_abbrs = v1_used_names()
    by_short = {c.short: c for c in build_companies()}
    for q in bench:
        used_shorts = [s for s in by_short if s in q.question]
        assert not any(s in v1_shorts for s in used_shorts), q.question
        if q.category == "abbr":
            assert not any(a in q.question for a in v1_abbrs), q.question


def test_行业极值不带行业二字且覆盖五个行业(bench):
    im = [q for q in bench if q.subtype == "industry_max"]
    assert len(im) == 6
    for q in im:
        assert "行业" not in q.question
        assert detect_aggregate(q.question) is not None, q.question
    industries = {
        next(w for w in ("新能源", "半导体", "医药生物", "食品饮料", "银行")
             if w in q.question)
        for q in im
    }
    assert industries == {"新能源", "半导体", "医药生物", "食品饮料", "银行"}


def test_拒答档包含边界指标与边界未来措辞(bench):
    refusal = [q for q in bench if q.category == "refusal"]
    subtypes = Counter(q.subtype for q in refusal)
    assert subtypes["boundary_metric"] >= 1
    assert subtypes["shaped_metric"] >= 1
    assert subtypes["future_escaped"] >= 1
    assert subtypes["wrong_year"] == 10
    assert sum(subtypes[k] for k in ("boundary_metric", "shaped_metric")) == 20
