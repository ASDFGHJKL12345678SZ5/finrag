"""FR-5 可靠性：LLM 重试只对可恢复错误、401/400 立即失败。"""
from __future__ import annotations

import pytest

from app.rag import llm


class StatusErr(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"status {code}")
        self.status_code = code


def test_重试判据只放可恢复错误():
    assert llm._is_retryable(TimeoutError())
    assert llm._is_retryable(ConnectionResetError())
    assert llm._is_retryable(StatusErr(429))
    assert llm._is_retryable(StatusErr(500))
    assert llm._is_retryable(StatusErr(503))
    assert not llm._is_retryable(StatusErr(400))
    assert not llm._is_retryable(StatusErr(401))
    assert not llm._is_retryable(StatusErr(403))
    assert not llm._is_retryable(ValueError("bad json"))


def test_401_不重试直接失败(monkeypatch):
    """key 错重试 3 次 = 白烧 3 倍调用与 7 秒退避。"""
    calls = {"n": 0}

    class FakeCompletions:
        def create(self, **_kwargs):
            calls["n"] += 1
            raise StatusErr(401)

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    obj = llm.OpenAILLM.__new__(llm.OpenAILLM)
    obj._client = FakeClient()
    obj._model = "deepseek-chat"
    obj.calls = 0

    with pytest.raises(StatusErr):
        obj.generate("hello")
    assert calls["n"] == 1, f"401 被重试了 {calls['n']} 次"
