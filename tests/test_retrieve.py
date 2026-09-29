"""融合重排单测：纯函数，不碰数据库。

覆盖：min-max 归一化的退化处理、两路召回的分数合并、来源去重
（防单篇霸榜——混合检索的经典失效模式）。
"""
from app.rag.retrieve import _min_max, fuse


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
