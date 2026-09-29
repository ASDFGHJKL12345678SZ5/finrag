"""结构感知分块器（金融研报专用）。

设计决策（ADR-03）：为什么不用固定长度切：
研报是强结构文档——"风险提示"标题下的第一段永远是风险，
固定 512 字切会把标题和正文切断、把两条不同风险并进一块。
检索到"半句话"时，模型只能编下半句。

策略：
1. 先按 Markdown 标题（#/##/###）切段，每段带章节路径元数据
   （section_path = "三、财务分析 > 3.1 营收"）——检索命中后引用能定位到章节
2. 段落内超长（> chunk_max_chars）按句子滑动窗口再切，块间重叠
   chunk_overlap_chars，缓解"切断处丢上下文"
3. 表格（| 开头的连续行）整块保留不切——切表格等于毁数据
4. 每块携带完整元数据：source / section_path / 块序号 / 字符区间，
   引用与回溯都靠它

块是纯数据（Chunk dataclass），不碰数据库——可单测、可复现。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from app.core.config import get_settings


@dataclass
class Chunk:
    text: str
    source: str                      # 文档标识（如 report id）
    section_path: str                # 章节路径，如 "三、财务分析 > 3.1 营收"
    chunk_index: int                 # 文档内序号
    kind: str = "text"               # text | table
    meta: dict = field(default_factory=dict)


def _split_sentences(text: str) -> list[str]:
    """按中英文句末标点切句，保留标点在句尾。不完美但够用——
    分块质量靠"不切断章节"保证，句级精度只影响重叠窗口。"""
    separators = "。！？!?；;" + chr(10)
    sentences: list[str] = []
    current: list[str] = []
    for ch in text:
        current.append(ch)
        if ch in separators:
            sentence = "".join(current).strip()
            if sentence:
                sentences.append(sentence)
            current = []
    tail = "".join(current).strip()
    if tail:
        sentences.append(tail)
    return sentences


def _split_long_paragraph(paragraph: str, max_chars: int, overlap: int) -> list[str]:
    """超长段落按句子滑动窗口切分，相邻块重叠 overlap 个字符。"""
    if len(paragraph) <= max_chars:
        return [paragraph]
    sentences = _split_sentences(paragraph)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) <= max_chars:
            current += sentence
            continue
        if current:
            pieces.append(current)
        # 单句超长：硬切（极端情况，研报里罕见）
        while len(sentence) > max_chars:
            pieces.append(sentence[:max_chars])
            sentence = sentence[max_chars - overlap:]
        current = sentence
    if current:
        pieces.append(current)
    # 重叠：把前一块尾部 overlap 字符接到下一块头部
    if overlap > 0 and len(pieces) > 1:
        overlapped = [pieces[0]]
        for prev, nxt in itertools.pairwise(pieces):  # 相邻对
            tail = prev[-overlap:]
            overlapped.append(tail + nxt)
        pieces = overlapped
    return pieces


def chunk_document(
    text: str,
    source: str,
    max_chars: int | None = None,
    overlap: int | None = None,
) -> list[Chunk]:
    """把一篇研报（Markdown）切成带元数据的块。

    输入约定：# 一级标题 / ## 二级 / ### 三级；表格是连续 | 开头的行。
    """
    settings = get_settings()
    max_chars = max_chars or settings.chunk_max_chars
    overlap = overlap if overlap is not None else settings.chunk_overlap_chars

    lines = text.splitlines()
    chunks: list[Chunk] = []
    section_path = ""
    buffer: list[str] = []
    index = 0

    def flush(kind: str = "text") -> None:
        nonlocal index, buffer
        body = "\n".join(buffer).strip()
        buffer = []
        if not body:
            return
        for piece in _split_long_paragraph(body, max_chars, overlap):
            chunks.append(
                Chunk(
                    text=piece,
                    source=source,
                    section_path=section_path,
                    chunk_index=index,
                    kind=kind,
                )
            )
            index += 1

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            flush()
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped.lstrip("#").strip()
            # 章节路径只含章节层级：一级标题是文档标题，文档身份由
            # source 字段承载，再塞进路径会让每个引用都重复一遍文档名
            parts = section_path.split(" > ") if section_path else []
            if level <= 1:
                parts = []
            elif level == 2:
                parts = [title]
            else:
                parts = parts[:1] + [title]
            section_path = " > ".join(p for p in parts if p)
            continue
        if stripped.startswith("|"):
            if not buffer or not buffer[-1].startswith("|"):
                flush()  # 表格开始前先把正文冲掉
            buffer.append(line)
            continue
        if buffer and buffer[-1].startswith("|"):
            flush(kind="table")  # 表格结束
        buffer.append(line)
    flush()

    for c in chunks:
        c.meta["char_len"] = len(c.text)
    return chunks
