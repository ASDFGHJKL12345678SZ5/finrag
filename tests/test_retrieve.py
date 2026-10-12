"""融合重排单测：纯函数，不碰数据库。

覆盖：min-max 归一化的退化处理、两路召回的分数合并、来源去重
（防单篇霸榜——混合检索的经典失效模式）、实体锚定（D13）。
"""
from app.rag.retrieve import COMPANY_ANCHOR_BOOST, _min_max, detect_anchor, fuse


def _row(source: str, idx: int, score: float, text: str = "t") -> dict:
    return {
        "source": source,
        "chunk_index": idx,
        "section_path": "s",
        "kind": "text",
        "text": text,
        "score": score,
    }


def test_min_max_归一化():
    assert _min_max([1.0, 2.0, 3.0]) == [0.0, 0.5, 1.0]


def test_min_max_全相等时退化为1():
    """全相等不是除 0 崩溃，也不是虚高——统一 1.0。"""
    assert _min_max([5.0, 5.0]) == [1.0, 1.0]


def test_两路命中同一块时分数叠加():
    """向量路和关键词路都命中的块应该排最前——这正是混合检索的意义。"""
    v = [_row("A", 0, 1.0)]
    k = [_row("A", 0, 1.0)]
    ranked = fuse(v, k, vector_weight=0.6, keyword_weight=0.4)
    assert len(ranked) == 1
    assert abs(ranked[0]["score"] - 1.0) < 1e-9
    assert set(ranked[0]["via"]) == {"vector", "keyword"}


def test_来源去重防止单篇霸榜():
    """同一文档最多 2 块——否则前十名全是同一篇研报的段落，
    其他研报的证据被挤出上下文，模型只能基于单一来源回答。"""
    v = [_row("A", i, 10.0 - i) for i in range(5)] + [_row("B", 0, 1.0)]
    ranked = fuse(v, [], vector_weight=1.0, keyword_weight=0.0, max_per_source=2)
    sources = [r["source"] for r in ranked]
    assert sources.count("A") == 2
    assert "B" in sources


def test_分数降序输出():
    v = [_row("A", 0, 1.0), _row("B", 0, 5.0), _row("C", 0, 3.0)]
    ranked = fuse(v, [], vector_weight=1.0, keyword_weight=0.0)
    scores = [r["score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)


# ---------- 实体锚定（D13 R6） ----------


def test_detect_anchor最长匹配优先():
    """公司名互为前缀时取最长——短名会把长名的问题错锚到短名文档。"""
    companies = ["华曜", "华曜新能"]
    assert detect_anchor("华曜新能2025年营收", companies) == "华曜新能"


def test_detect_anchor无命中返回None():
    assert detect_anchor("完全无关的问题", ["宁德润能", "华曜新能"]) is None
    assert detect_anchor("", ["宁德润能"]) is None


def test_锚定把目标公司的块提分到首位():
    """同分竞争下，被点到名的公司块必须排最前（易混公司场景的判别依据）。"""
    v = [_row("新能源-000-宁德润能", 0, 1.0), _row("新能源-001-宁德润科", 0, 1.0)]
    ranked = fuse(v, [], vector_weight=1.0, keyword_weight=0.0, anchor="宁德润能")
    assert ranked[0]["source"] == "新能源-000-宁德润能"
    assert abs(ranked[0]["score"] - COMPANY_ANCHOR_BOOST) < 1e-9


def test_锚定为None时行为与旧版一致():
    v = [_row("A", 0, 1.0), _row("B", 0, 0.5)]
    r1 = fuse(v, [], vector_weight=1.0, keyword_weight=0.0)
    r2 = fuse(v, [], vector_weight=1.0, keyword_weight=0.0, anchor=None)
    assert [x["source"] for x in r1] == [x["source"] for x in r2]


def test_锚定不突破同文档限流():
    """锚定只是提分，不改限流语义——问 A 也不能让 A 的 5 块霸榜。"""
    v = [_row("A", i, 1.0 - i * 0.01) for i in range(5)] + [_row("B", 0, 0.9)]
    ranked = fuse(v, [], vector_weight=1.0, keyword_weight=0.0, anchor="A", max_per_source=2)
    assert [r["source"] for r in ranked].count("A") == 2
