"""embedding 契约测试（mock 模式，不下载模型）。

守住三条契约：确定性、归一化、维度。换真模型后这些测试同样必须过。
"""
import numpy as np

from app.rag.embeddings import MockEmbedder, _l2_normalize

DIM = 512


def test_mock_确定性():
    e = MockEmbedder(dim=DIM)
    a = e.embed(["宁德时代2025年营收"])
    b = e.embed(["宁德时代2025年营收"])
    assert np.array_equal(a, b)


def test_mock_不同文本不同向量():
    e = MockEmbedder(dim=DIM)
    a = e.embed(["营业收入"])[0]
    b = e.embed(["归母净利润"])[0]
    assert not np.allclose(a, b)


def test_归一化后模长为1():
    e = MockEmbedder(dim=DIM)
    vec = e.embed(["任意文本"])[0]
    assert abs(float(np.linalg.norm(vec)) - 1.0) < 1e-5


def test_维度与声明一致():
    e = MockEmbedder(dim=DIM)
    assert e.embed(["x", "y", "z"]).shape == (3, DIM)


def test_零向量不除零():
    arr = np.zeros((2, 4), dtype=np.float32)
    out = _l2_normalize(arr)
    assert np.all(np.isfinite(out))
