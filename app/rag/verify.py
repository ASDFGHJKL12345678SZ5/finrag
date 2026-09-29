"""引用校验：答案里的每个数字论断必须能在被引 chunk 里找到依据。

设计决策（ADR-07）：金融场景幻觉零容忍，但"让 LLM 自己保证不编"
不可靠——所以校验是结构性的：抽取答案中的数字论断（含数字的句子），
逐个检查其数字与关键词是否出现在它引用的 chunk 文本里。任一论断
找不到依据 -> 整篇答案降级为"资料不足"拒答，而不是"带病输出"。

与 DataCrew 安全闸的同构思想：不信任生成端，用确定性规则兜底。
这里刻意用词面重合而不是语义相似——语义判断又需要一个模型，
而词面校验对"编数字"这类最高危幻觉已经足够（模型编的数字几乎不可能
恰好出现在语料里），且完全确定、可单测、零依赖。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_NUM = re.compile(r"\d+(?:\.\d+)?%?")

# 无引用论断的哨兵：刻意不用中文状态词——改写查询按词形过滤 missing
# 列表，中文状态词会被当证据词拼回问题，污染下一轮检索
NO_CITATION = "<no-citation>"


@dataclass
class ClaimVerdict:
    claim: str          # 论断原文（含数字的句子）
    citation_id: str    # 论断引用的编号
    supported: bool     # 数字+关键词是否都在被引 chunk 里
    missing: list[str]  # 缺失的证据词


def split_claims(answer: str) -> list[tuple[str, str]]:
    """把答案拆成 (论断, 引用编号) 对。

    两步预处理（缺一步引用校验就形同虚设）：
    1. 引用归属：LLM 习惯把引用放在句子末尾（"……56.4%。[c1]"），而按
       句号切会把 [c1] 切到下一段开头——claim 和它的引用被拆开，校验
       必然误杀。先把句末的 [cN] 挪到句首，让引用跟着它修饰的句子走。
    2. 去列表序号："1. 宁德润能……" 里的 "1" 是序号不是数据，不剥掉
       会被当成数字论断，还常因无引用而误杀整篇答案。
    """
    # 句末引用 -> 句首（引用修饰的是它前面的句子）
    normalized = re.sub(
        r"([。；;!?！？])\s*\[c(\d+)\]",
        r"[c\2]\1",
        answer,
    )
    claims: list[tuple[str, str]] = []
    for sentence in re.split(r"[。；;\n]", normalized):
        sentence = re.sub(
            r"(^|[：:，,；;])\s*\d+[.、)）]\s*", r"\1",
            sentence.strip(),
        )
        # 先摘引用标记再查数字——[c1] 里的 1 不是数据，
        # 顺序反了会让每个带引用的句子都被当成数字论断
        m = re.search(r"\[c(\d+)\]", sentence)
        citation_id = f"c{m.group(1)}" if m else ""
        sentence = re.sub(r"\[c\d+\]\s*", "", sentence)
        if not sentence or not _NUM.search(sentence):
            continue
        claims.append((sentence, citation_id))
    return claims


def verify_answer(
    answer: str, chunks: list[dict]
) -> tuple[bool, float, list[ClaimVerdict]]:
    """校验答案。返回 (是否通过, 支持率, 逐论断裁定)。

    门禁策略是严格的：每个含数字的论断都必须带引用、且数字与实词都能
    在被引 chunk 里找到——一个都不放过。金融场景"带病输出"比"拒答"
    危险得多，所以这里不做比例折中；min_citation_overlap 只作为报告
    指标（支持率）留在配置里，不参与门禁。
    没有数字论断的纯定性回答直接放行——校验只管数字幻觉。
    """
    by_id = {c.get("citation_id"): c for c in chunks}
    verdicts: list[ClaimVerdict] = []
    for claim, citation_id in split_claims(answer):
        chunk = by_id.get(citation_id)
        if chunk is None:
            verdicts.append(ClaimVerdict(claim, citation_id, False, [NO_CITATION]))
            continue
        missing = _missing_evidence(claim, chunk["text"])
        verdicts.append(ClaimVerdict(claim, citation_id, not missing, missing))

    if not verdicts:
        return True, 1.0, verdicts  # 纯定性回答，放行
    supported = sum(1 for v in verdicts if v.supported)
    ratio = supported / len(verdicts)
    passed = all(v.supported for v in verdicts)
    return passed, ratio, verdicts


def _missing_evidence(claim: str, evidence: str) -> list[str]:
    """论断里找不到依据的成分。

    两层检查，严格程度不同：
    1. 数字（硬门禁）：论断里的每个数字都必须在被引证据里——模型编的
       数字几乎不可能恰好出现在语料里，这一层拦最高危的幻觉。
    2. 内容词（软门禁）：把论断切中文二元组，至少 2 个要能在证据里
       找到——防止"数字碰巧对上但话题完全不对"的张冠李戴。
       软门禁不要求全部命中：答案的引导语（"根据检索到的研报内容："）
       不是论断内容，逐词要求全中会把正常答案误杀（实测踩坑）。
    """
    missing: list[str] = []
    for num in _NUM.findall(claim):
        if num not in evidence:
            missing.append(num)
    bigrams = _content_bigrams(claim)
    hits = sum(1 for g in bigrams if g in evidence)
    if bigrams and hits < 2:
        missing.append(f"内容词命中不足({hits}/{len(bigrams)})")
    return missing


def _content_bigrams(text: str) -> list[str]:
    """论断的中文内容二元组（去停用 glue：问答套话不是证据）。"""
    stop = {"根据", "研报", "内容", "检索", "资料", "显示", "所示", "如下"}
    out: list[str] = []
    for run in re.findall(r"[一-龥]{2,}", text):
        if len(run) == 2:
            if run not in stop:
                out.append(run)
        else:
            out.extend(run[i : i + 2] for i in range(len(run) - 1))
    return [g for g in out if g not in stop]
