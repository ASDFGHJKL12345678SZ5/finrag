"""FR-6 融合/重排纯函数测试：RRF 只看名次、重排失败必须降级。"""
from __future__ import annotations

from app.rag import rerank as rerank_mod
from app.rag.retrieve import rrf_fuse


def _row(source: str, idx: int, score: float) -> dict:
    return {"source": source, "chunk_index": idx, "text": f"{source}#{idx}", "score": score}


def test_rrf_只看名次不看分数量纲():
    # 向量分 0.9/0.8，关键词分 100/1——量纲完全不同，RRF 不应被关键词分差吞掉
    v = [_row("a", 0, 0.9), _row("b", 0, 0.8)]
    k = [_row("b", 0, 100.0), _row("a", 0, 1.0)]
    out = rrf_fuse(v, k, vector_weight=0.5, keyword_weight=0.5, k=60, max_per_source=2)
    # 两路分别第 1/2 名，总分相等；并列时顺序不作强约束，但必须都在
    assert {r["source"] for r in out} == {"a", "b"}
    assert out[0]["score"] == out[1]["score"]


def test_rrf_权重偏向向量路():
    v = [_row("a", 0, 0.9), _row("b", 0, 0.8)]
    out = rrf_fuse(v, [], vector_weight=0.6, keyword_weight=0.4)
    assert [r["source"] for r in out] == ["a", "b"]


def test_rrf_实体锚定加权():
    v = [_row("新能源-000-华曜新能-2025", 0, 0.9), _row("新能源-001-华曜新源-2025", 0, 0.89)]
    out = rrf_fuse(v, [], anchor="华曜新能", max_per_source=2)
    assert out[0]["source"].endswith("华曜新能-2025")


def test_rrf_同文档限流():
    v = [_row("a", i, 1.0 - i * 0.01) for i in range(5)]
    out = rrf_fuse(v, [], max_per_source=2)
    assert sum(1 for r in out if r["source"] == "a") == 2


def test_rerank_模型不可用时原样返回(monkeypatch):
    rows = [_row("a", 0, 0.9), _row("b", 0, 0.8)]
    monkeypatch.setattr(rerank_mod, "get_reranker", lambda: None)
    assert rerank_mod.rerank("q", rows) == rows


def test_rerank_按cross_encoder分数重排(monkeypatch):
    class FakeEncoder:
        def rerank(self, query, texts):
            # 故意反转：第二句更相关
            return [0.1 for _ in texts[:-1]] + [0.9] if len(texts) > 1 else [0.9]

    rows = [_row("a", 0, 0.9), _row("b", 0, 0.8)]
    monkeypatch.setattr(rerank_mod, "get_reranker", lambda: FakeEncoder())
    out = rerank_mod.rerank("q", rows)
    assert out[0]["source"] == "b"
    assert out[0]["rerank_score"] == 0.9
    assert out[1]["source"] == "a"


def test_rerank_推理异常降级(monkeypatch):
    class Boom:
        def rerank(self, *_args):
            raise RuntimeError("onnx crash")

    rows = [_row("a", 0, 0.9)]
    monkeypatch.setattr(rerank_mod, "get_reranker", lambda: Boom())
    assert rerank_mod.rerank("q", rows) == rows
