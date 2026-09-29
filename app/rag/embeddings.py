"""Embedding 客户端：本地 ONNX 模型 + 确定性 mock 兜底。

设计决策（ADR-02）：
- 用 fastembed 跑 bge-small-zh-v1.5（ONNX Runtime），不用 torch：
  金融语料不出内网 + 评测可复现（模型固定 -> 向量固定 -> 检索结果固定）
- mock 模式给确定性哈希向量：CI 不下载模型、离线可跑测试。
  哈希向量无语义（检索结果无意义），但维度/归一化/存储链路完全真实——
  它测的是"管道通不通"，不是"检索准不准"
- 统一 normalize：向量检索用内积，归一化后内积 == 余弦，pgvector 里
  vector_cosine_ops 与手工算分都对得上
"""
from __future__ import annotations

import hashlib
import struct
from typing import Protocol

import numpy as np

from app.core.config import get_settings


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:
        """返回 (n, dim) 的 float32 归一化矩阵。"""
        ...


class LocalEmbedder:
    """fastembed 本地模型。首次使用会下载模型（走 HF 镜像/代理）。"""

    def __init__(self, model_name: str, dim: int) -> None:
        from fastembed import TextEmbedding  # 延迟导入：mock 模式不装也能跑

        self._model = TextEmbedding(model_name=model_name)
        self.dim = dim
        # 启动即校验维度：模型换了 dim 没改，pgvector 建列会静默出错
        probe = self.embed(["维度自检"])
        actual = int(probe.shape[1])
        if actual != dim:
            raise ValueError(
                f"embedding 维度不匹配：配置 {dim}，模型 {model_name} 实际 {actual}。"
                "换模型必须同步改配置里的 embedding_dim"
            )

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = list(self._model.embed(texts))
        arr = np.asarray(vectors, dtype=np.float32)
        return _l2_normalize(arr)


class MockEmbedder:
    """确定性哈希向量：同一文本永远同一向量，CI/离线可用。

    实现：sha256(text) 的字节流按 4 字节一组解释为 uint32，线性映射到
    [-1, 1] 后填满 dim 维，再归一化。
    没有语义，但确定、分布均匀、维度真实——足够测管道。

    为什么不用 struct 直接解 float：随机 4 字节解出的 float 值域横跨
    1e-38 ~ 1e38，归一化后个别分量主演、其余趋 0——所有向量余弦都约等于
    1，检索失去任何区分度（测试 test_mock_不同文本不同向量 会挂）。
    映射到 [-1, 1] 后分量同量级，向量在高维球面上均匀铺开。
    """

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        rows = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            need = self.dim * 4
            filler = (digest * (need // len(digest) + 1))[:need]
            ints = struct.unpack("<" + "I" * self.dim, filler)
            # uint32 -> [-1, 1)：除以 2^32 映射到 [0,1)，再平移
            rows.append([(v / 4294967296.0) * 2.0 - 1.0 for v in ints])
        arr = np.asarray(rows, dtype=np.float32)
        return _l2_normalize(arr)


def _l2_normalize(arr: np.ndarray) -> np.ndarray:
    """逐行 L2 归一化。零向量原样返回（避免除 0）。

    先清 NaN/Inf：随机字节解出的 float 可能含非有限值（mock 向量的来源），
    不清理会让归一化产出 NaN，检索得分全变 nan。
    范数用 float64 算：随机字节的值域可达 1e38，float32 平方直接溢出成
    inf，整个向量会被除成 0（两个文本得到相同的零向量）。
    """
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    norms = np.linalg.norm(arr.astype(np.float64), axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms).astype(np.float32)
    return (arr.astype(np.float64) / norms).astype(np.float32)


_embedder_singleton: Embedder | None = None


def get_embedder() -> Embedder:
    """按配置返回 embedder，进程内单例（模型只加载一次，加载要几秒）。"""
    global _embedder_singleton
    if _embedder_singleton is None:
        settings = get_settings()
        if settings.embedding_mode == "mock":
            _embedder_singleton = MockEmbedder(dim=settings.embedding_dim)
        else:
            _embedder_singleton = LocalEmbedder(
                model_name=settings.embedding_model, dim=settings.embedding_dim
            )
    return _embedder_singleton
