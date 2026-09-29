"""LLM 客户端：mock（规则假模型）+ OpenAI 兼容（DeepSeek）。

与 DataCrew 相同的双模式哲学：
- mock 模式让 CI/无 key 环境可复现全链路（包括"幻觉型 LLM"威胁模拟——
  引用校验的价值恰恰体现在 LLM 编造时）
- real 模式走 OpenAI 兼容协议（DeepSeek/Qwen 均可），换 base_url+key 即切

mock 的生成策略：从检索到的 chunk 里抽取含数字的句子拼答案——
它"看过证据"，所以引用校验应该通过；另设"幻觉型"意图（问题含特定
触发词时硬编语料里没有的数字），用来验证校验层能拦住。
"""
from __future__ import annotations

import os
import re
from typing import Any, Protocol

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("llm")

# 幻觉型触发词：问题含这些词时，mock 会编造语料中不存在的数字
_HALLUCINATION_TRIGGERS = ("预测", "未来三年", "2030年", "股价目标")


class LLM(Protocol):
    name: str

    def generate(self, prompt: str, **kwargs: Any) -> str: ...


class MockLLM:
    """规则假模型：证据摘录型（默认）+ 幻觉型（触发词命中时）。"""

    name = "mock-rag"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str, **kwargs: Any) -> str:
        self.calls += 1
        question = _extract_field(prompt, "问题")
        evidence = _extract_evidence(prompt)
        if any(t in question for t in _HALLUCINATION_TRIGGERS):
            # 幻觉型：编一个语料里没有的数字（引用校验必须拦住）
            return (
                f"根据研报分析，{question.rstrip('？?')}的预期值为 999.99 亿元，"
                "远超行业平均水平。[c1]"
            )
        if not evidence:
            return "检索结果中没有相关资料，无法回答。"
        # 证据摘录型：取前两条含数字的证据句，附原始引用编号
        picked = [pair for pair in evidence if re.search(r"\d", pair[1])][:2] or evidence[:2]
        body = "".join(f"{i + 1}. {s}[{cid}]" for i, (cid, s) in enumerate(picked))
        return f"根据检索到的研报内容：{body}"


class OpenAILLM:
    """OpenAI 兼容客户端（DeepSeek）。仅在 real 模式使用。"""

    name = "deepseek-chat"

    def __init__(self) -> None:
        from openai import OpenAI  # 延迟导入：mock 模式不装也能跑

        settings = get_settings()
        self._client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key or os.environ.get("LLM_API_KEY", ""),
        )
        self._model = settings.llm_model

    def generate(self, prompt: str, **kwargs: Any) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=kwargs.get("temperature", 0.1),
        )
        return resp.choices[0].message.content or ""


def _extract_field(prompt: str, field: str) -> str:
    """从 prompt 里取 'field：xxx' 行的值。"""
    for line in prompt.splitlines():
        if line.startswith(field + "："):
            return line[len(field) + 1:].strip()
    return ""


def _extract_evidence(prompt: str) -> list[tuple[str, str]]:
    """从 prompt 的证据区提取 (引用编号, 正文) 对。

    prompt 的证据区格式（见 graph._build_prompt）：
        [c1] source / section_path
        正文第一行
        正文第二行...
    [cN] 行是标题，跟在它后面、直到空行/下一个 [cN] 的非空行才是正文。
    必须保留原始编号——mock 自己重新编号会指错证据，引用校验必然误杀
    （ Smoke 教训：prompt 里 [c2] 的证据被标成 [c1] 输出，校验按 [c1]
    回溯到的是一块无关 chunk）。
    """
    evidence: list[tuple[str, str]] = []
    lines = prompt.splitlines()
    in_evidence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("证据"):
            in_evidence = True
            i += 1
            continue
        if not in_evidence:
            i += 1
            continue
        if line.startswith("[c") and "]" in line:
            cid = line[1 : line.index("]")]
            j = i + 1
            body: list[str] = []
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip() or nxt.startswith("[c"):
                    break
                body.append(nxt.strip())
                j += 1
            if body:
                evidence.append((cid, "".join(body)))
            i = j
            continue
        i += 1
    return evidence


def get_llm() -> LLM:
    settings = get_settings()
    if settings.llm_mode == "mock":
        return MockLLM()
    return OpenAILLM()
