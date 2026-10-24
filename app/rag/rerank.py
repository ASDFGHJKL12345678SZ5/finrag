"""本地 cross-encoder 重排（FR-6）：bge-reranker-base（fastembed / ONNX）。

为什么单独成模块：
1. 重排是可选增强——模型没下载/加载失败时**必须**退回原排序，不能把
   问答链路带崩（与 DataCrew 的"图表失败只跳过图表"同一原则）
2. 模型只加载一次（进程级单例），重排只对融合后的 topN 候选做——全量
   重排会把延迟乘上候选数
3. 与检索解耦：retrieve 仍负责召回+融合，本模块只做"给定 query 与候选，
   按语义相关度重排"
"""
from __future__ import annotations

import time
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("rerank")

_encoder: Any | None = None
_load_failed = False


def get_reranker() -> Any | None:
    """懒加载 cross-encoder；不可用返回 None（调用方原样返回候选）。"""
    global _encoder, _load_failed
    if _encoder is not None or _load_failed:
        return _encoder
    settings = get_settings()
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        t0 = time.perf_counter()
        _encoder = TextCrossEncoder(settings.rerank_model)
        log.info(
            "rerank.loaded",
            extra={"context": {"model": settings.rerank_model,
                               "load_ms": round((time.perf_counter() - t0) * 1000)}},
        )
    except Exception as e:  # noqa: BLE001 - 下载失败/缺依赖都降级
        _load_failed = True
        log.warning("rerank.degraded", extra={"context": {"reason": str(e)[:160]}})
        return None
    return _encoder


def rerank(
    query: str, rows: list[dict], top_n: int | None = None
) -> list[dict]:
    """对融合后的候选做 cross-encoder 重排；失败时原样返回。

    输入行只需 `text` 字段；输出复制行并写 `rerank_score`，按它降序。
    只对前 `rerank_candidates` 条做推理（配置），其余保持原顺序追加。
    """
    if not rows:
        return rows
    settings = get_settings()
    encoder = get_reranker()
    if encoder is None:
        return rows
    limit = min(settings.rerank_candidates, len(rows))
    head, tail = rows[:limit], rows[limit:]
    try:
        scores = list(encoder.rerank(query, [r["text"][:1200] for r in head]))
    except Exception as e:  # noqa: BLE001 - 推理失败降级
        log.warning("rerank.failed", extra={"context": {"reason": str(e)[:160]}})
        return rows
    # 模型分数可能与原融合分不同量纲：只在 head 内按 rerank 分重排，
    # 不把 rerank 分写回融合分（下游只关心顺序与 source）
    scored = sorted(zip(head, scores, strict=False), key=lambda p: float(p[1]), reverse=True)
    out = []
    for row, score in scored:
        item = dict(row)
        item["rerank_score"] = float(score)
        out.append(item)
    out.extend(tail)
    return out[:top_n] if top_n else out
