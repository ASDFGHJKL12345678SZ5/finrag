"""Langfuse 可观测（FR-8）：每次问答一条 trace，检索/守卫/生成/校验为 span。

设计边界（与 DataCrew 同款）：
1. 可观测不是强依赖：没配 key / SDK 缺失 / 云端不可用，问答主链路零影响
2. SDK 关在这一个文件里：langfuse v2 的 client.trace/span API 与 v3+ 的
   OTel 模型差异很大，换版本只改这里
3. 记录的是**问答级** trace（不是 LLM 调用级）：input=问题，output=终态，
   span=各节点（retrieve/check_sufficiency/generate/verify），失败静默
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

log = get_logger("observability")

_client: Any | None = None
_init_attempted = False


def get_langfuse() -> Any | None:
    """按配置返回 Langfuse 客户端；未配置/未安装/初始化失败都返回 None。"""
    global _client, _init_attempted
    if _init_attempted:
        return _client
    _init_attempted = True
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        log.info("langfuse.enabled", extra={"context": {"host": settings.langfuse_host}})
    except Exception as e:  # noqa: BLE001 - SDK 缺失/版本不兼容：降级为日志
        log.warning("langfuse.degraded", extra={"context": {"reason": str(e)[:120]}})
        _client = None
    return _client


def start_trace(question: str, session_id: str | None = None) -> Any | None:
    client = get_langfuse()
    if client is None:
        return None
    try:
        return client.trace(
            name="finrag.qa",
            input={"question": question},
            session_id=session_id or "default",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("langfuse.trace_failed", extra={"context": {"reason": str(e)[:120]}})
        return None


def record_span(
    trace: Any | None,
    name: str,
    metadata: dict[str, Any] | None = None,
    output: Any | None = None,
) -> None:
    """记一个节点 span（trace 为 None 时零成本返回；任何异常只降级）。"""
    if trace is None:
        return
    try:
        span = trace.span(name=name, metadata=metadata or {}, output=output)
        span.end()
    except Exception as e:  # noqa: BLE001
        log.warning("langfuse.span_failed", extra={"context": {"reason": str(e)[:120]}})


def end_trace(
    trace: Any | None,
    output: dict[str, Any] | None = None,
    status: str | None = None,
) -> None:
    if trace is None:
        return
    try:
        trace.update(output=output or {}, metadata={"status": status or "unknown"})
        client = get_langfuse()
        if client is not None:
            client.flush()
    except Exception as e:  # noqa: BLE001
        log.warning("langfuse.end_failed", extra={"context": {"reason": str(e)[:120]}})


def shutdown() -> None:
    """进程退出前刷缓冲区。"""
    if _client is not None:
        try:
            _client.flush()
        except Exception as e:  # noqa: BLE001 - 退出路径只记日志，不阻塞停机
            log.warning("langfuse.flush_failed", extra={"context": {"reason": str(e)[:120]}})
