"""证据充分性守卫：问题问的东西，证据里必须真的存在。

为什么需要它（README §4.5 诚实边界第 1 条的真实缺口）：
真实 DeepSeek 模式下，问"预测宁德润能未来三年营收"这类幻觉题时，模型嘴上说
"无法预测"但检索有证据（相关公司的研报被捞回），校验器照的是"数字论断有据
即可通过"——定性回答无数字可卡，最终 status 是 done 而不是 insufficient，
拒答率掉到 0.1（1/10）。**有据可依的胡说，引用校验拦不住。**

本模块补上确定性的一层：问题里的年份 / 指标词 / 未来措辞在证据中不存在时，
直接判 insufficient——**不生成、不校验、零 LLM 调用**。判据全部可回溯
（字面存在性 + 独立同义词典），零模型依赖、可单测。

**判据来源必须是独立的（FR-2）**：早期版从 `app.rag.corpus` 导入
`UNSUPPORTED_METRICS / WRONG_YEARS`——出题代码知道答案，守卫再用出题清单判
"该不该拒"，拒答 100% 是循环论证。现在指标词来自 `app.rag.metric_lexicon`
（只描述金融文本的说法，不含题目/答案/语料），源码里由 AST 不变量测试守住
"不得 import corpus"。

**位置很重要（D13 后续修正）**：本模块由 graph.py 的 `check_sufficiency`
节点在**检索之后、生成之前**调用。早期版把它放在 verify 节点入口，而
generate 在 verify 之前——"拒答"实际发生在生成之后，错年份问题仍会
烧 3 次生成（初次 + 2 次重试），"不生成"并不成立。守卫位置由
`tests/test_graph_integration.py::test_sufficiency_refusal_skips_generate` 守死。

与 verify.py 的分工：verify 管"答案里的数字有没有编造"（生成后校验），
sufficiency 管"这个问题该不该答"（生成前门禁）。前者拦编造，后者拦
覆盖不到——合起来才是完整的抗幻觉。
"""
from __future__ import annotations

import re

from app.rag.metric_lexicon import (
    METRIC_GROUPS,
    is_future_question,
    metric_terms_in,
    unsupported_terms_in,
)

_YEAR_RE = re.compile(r"20\d{2}")

# 拒答原因码（进 trace 与评测报告，人读）
YEAR_NOT_IN_EVIDENCE = "year_not_in_evidence"
METRIC_NOT_IN_EVIDENCE = "metric_not_in_evidence"
FUTURE_NOT_IN_EVIDENCE = "future_not_in_evidence"

REFUSAL_REASONS = (
    YEAR_NOT_IN_EVIDENCE,
    METRIC_NOT_IN_EVIDENCE,
    FUTURE_NOT_IN_EVIDENCE,
)


def _evidence_years(chunks: list[dict]) -> set[str]:
    text = " ".join(c.get("text", "") for c in chunks)
    return set(_YEAR_RE.findall(text))


def _evidence_text(chunks: list[dict]) -> str:
    return " ".join(c.get("text", "") for c in chunks)


def check_sufficiency(question: str, chunks: list[dict]) -> str | None:
    """检查证据充分性。返回拒答原因码，或 None（充分，继续生成）。

    四条规则，全部是字面存在性判定：
    1. 年份守卫：问题提到的每个年份都必须出现在证据里。题库/语料只有历史
       年份——问 2024（错年份）/2026（未来预测）→ 证据中没有该年份 → 拒答。
       真实用户的时间错问在生产里极常见，让模型"看着 2025 的数据硬答 2024
       的问题"是最典型的静默幻觉。
    2. 未来措辞守卫：问题含"明年/未来/预计/预测/展望…"且**没有具体年份**
       → 拒答（语料只有历史数据，任何未来数字都是编的）。
    3. 语料外指标：市值/股息率/市盈率/股价/分红…不在任何证据里 → 拒答。
    4. 指标词守卫：问题抽取到的指标组（营业收入/归母净利润/毛利率/
       研发费用率/经营性现金流/同比增长…），其任一说法都不在证据中 →
       拒答。**同义词典做归一**：问"总收入"、证据写"营业收入"不算缺失
       （这正是 paraphrase 档误拒的来源，必须在守卫层就消掉）。
    """
    if not chunks:
        return None  # 空检索由 graph 的 retrieval_empty 守卫负责，不重复判

    evidence = _evidence_text(chunks)
    evidence_years = _evidence_years(chunks)
    for year in _YEAR_RE.findall(question):
        if year not in evidence_years:
            return YEAR_NOT_IN_EVIDENCE

    if is_future_question(question) and not _YEAR_RE.search(question):
        return FUTURE_NOT_IN_EVIDENCE

    for term in unsupported_terms_in(question):
        if term not in evidence:
            return METRIC_NOT_IN_EVIDENCE

    for canonical, _found in metric_terms_in(question):
        if not any(s in evidence for s in METRIC_GROUPS[canonical]):
            return METRIC_NOT_IN_EVIDENCE
    return None
