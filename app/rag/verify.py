"""引用校验：答案里的每个数字论断必须能在被引 chunk 里找到依据。

设计决策（ADR-09）：金融场景幻觉零容忍，但"让 LLM 自己保证不编"
不可靠——所以校验是结构性的：抽取答案中的数字论断（含数字的句子），
逐个检查其数字与关键词是否出现在它引用的 chunk 文本里。任一论断
找不到依据 -> 整篇答案降级为"资料不足"拒答，而不是"带病输出"。

与 DataCrew 安全闸的同构思想：不信任生成端，用确定性规则兜底。
这里刻意用词面重合而不是语义相似——语义判断又需要一个模型，
而词面校验对"编数字"这类最高危幻觉已经足够（模型编的数字几乎不可能
恰好出现在语料里），且完全确定、可单测、零依赖。

FR-7 收紧三处（都是真实误判/漏判形态）：
1. **数字按边界匹配**：旧版子串匹配让 "5" 命中 "56.4"、"20" 命中 "2025"——
   编造的 5 亿元只要证据里出现 56.4% 就能蒙混过关。现在要求数字前后
   都不是数字/小数点。
2. **单位归一后比较**：亿元/万元/元/百分比先换算再比数值——"0.5亿元" 与
   "5000万元" 是同一个数，不能因为写法不同误杀。
3. **一句话里的所有 [cN] 都参与核对**：旧版只取第一个引用，一句话引了
   [c1][c2] 时 c2 的证据根本没被检查；现在把所有被引 chunk 拼起来核对，
   任何一个引用编号不存在都判无据。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.metric_lexicon import METRIC_GROUPS

# 数字 + 可选单位。前后都排除数字/小数点：5 不能命中 56.4，20 不能命中 2025。
_NUM = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)(?:\s*(亿元|亿|万元|万|元|%))?(?![\d.])"
)

# 无引用论断的哨兵：刻意不用中文状态词——改写查询按词形过滤 missing
# 列表，中文状态词会被当证据词拼回问题，污染下一轮检索
NO_CITATION = "<no-citation>"

# 金额单位换算到"元"；% 单独成类（不能和金额混）
_UNIT_FACTOR = {
    "亿": 1e8,
    "亿元": 1e8,
    "万": 1e4,
    "万元": 1e4,
    "元": 1.0,
}


@dataclass
class ClaimVerdict:
    claim: str          # 论断原文（含数字的句子）
    citation_id: str    # 论断引用的编号（多引用用 + 连接，如 c1+c2）
    supported: bool     # 数字+关键词是否都在被引 chunk 里
    missing: list[str]  # 缺失的证据词


_YEAR_OR_DATE_RE = re.compile(
    r"(?:19|20)\d{2}\s*年|(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}"
)


def _has_data_number(text: str) -> bool:
    """去掉年份/日期后还有没有数据数字。

    “你要查的是 2022 年的收入指标”只有一个年份，不是数据论断；早期版把它
    当数字 claim，因无引用而整篇误杀（v2 abbr 5 题）。真正的数据句
    “2022 年营收 982.68 亿元”去掉年份后仍有 982.68，照常校验。
    """
    return bool(_NUM.search(_YEAR_OR_DATE_RE.sub("", text)))


def split_claims(answer: str) -> list[tuple[str, str]]:
    """把答案拆成 (论断, 引用编号) 对。

    三步预处理（缺一步引用校验就形同虚设）：
    1. 引用归属：LLM 习惯把引用放在句子末尾（"……56.4%。[c1]"），而按
       句号切会把 [c1] 切到下一段开头——claim 和它的引用被拆开，校验
       必然误杀。先把句末的 [cN] 挪到句首，让引用跟着它修饰的句子走。
    2. 去列表序号："1. 宁德润能……" 里的 "1" 是序号不是数据，不剥掉
       会被当成数字论断，还常因无引用而误杀整篇答案。
    3. 只有年份/日期的复述句不算数据论断（"你要查的是 2022 年的收入指标"）。

    一句话里可以有多个引用（"营收 X。[c1][c2]"）：按出现顺序去重后用
    "+" 连接。
    """
    # 句末引用 -> 句首（引用修饰的是它前面的句子）。
    # 支持一句多个引用 [c1][c2]：整条引用链一起搬到句首，否则按句号切分后
    # 后面的 [c2] 会掉到下一段、失去所属论断（FR-7 多引用 bug）。
    normalized = re.sub(
        r"([。；;!?！？])\s*((?:\[c\d+\]\s*)+)",
        lambda m: m.group(2).strip() + m.group(1),
        answer,
    )
    claims: list[tuple[str, str]] = []
    for sentence in re.split(r"[。；;\n]", normalized):
        sentence = re.sub(
            r"(^|[：:，,；;])\s*\d+[.、)）](?!\d)\s*", r"\1", sentence.strip(),
        )
        # 先摘引用标记再查数字——[c1] 里的 1 不是数据，
        # 顺序反了会让每个带引用的句子都被当成数字论断
        citations = list(dict.fromkeys(re.findall(r"\[c(\d+)\]", sentence)))
        citation_id = "+".join(f"c{n}" for n in citations)
        sentence = re.sub(r"\[c\d+\]\s*", "", sentence)
        if not sentence or not _has_data_number(sentence):
            continue
        claims.append((sentence, citation_id))
    return claims


def _source_company(chunk: dict) -> str | None:
    source = chunk.get("source") or ""
    parts = source.split("-")
    return parts[2] if len(parts) >= 3 else None


def _source_year(chunk: dict) -> str | None:
    """source 形如 `{industry}-{idx}-{company}-{year}`，取末尾年份。"""
    parts = (chunk.get("source") or "").split("-")
    tail = parts[-1] if parts else ""
    return tail if len(tail) == 4 and tail.isdigit() else None


def _claim_year(claim: str) -> str | None:
    match = re.search(r"(20\d{2})\s*年", claim)
    return match.group(1) if match else None


# 拒答话术：模型“说不知道”时，把它当成 insufficient 终态（Q3）。
_DECLINE_RE = re.compile(
    r"无法确定|无法回答|无法提供|无法给出|无法计算|无法获取|无法判断|无法仅凭"
    r"|不能确定|未披露|未提供|未提及|未直接给出|未给出|没有直接给出|没有给出"
    r"|未找到|未包含|未涉及|没有(?:相关)?数据"
)


def _asked_metric_phrases(question: str) -> set[str]:
    """问题里问的指标说法：注册词 + “X年的<短语>是多少”里的短语。"""
    phrases = set(_metric_terms_in(question))
    match = re.search(
        r"(?:年|财年|季度)的([^，。？?]{2,24}?)(?:是多少|为多少|多大|怎么样|如何|是什么|$|[，。？?])",
        question,
    )
    if match:
        phrases.add(match.group(1).strip())
    return {p for p in phrases if p}


def _has_positive_metric_answer(answer: str, question: str) -> bool:
    """答案里有没有“所问指标 + 数字”的正向陈述（用于区分拒答 vs 带免责的作答）。"""
    for phrase in _asked_metric_phrases(question):
        pattern = (
            re.escape(phrase)
            + r"\s*(?:（[^）]{0,8}）)?\s*"
            + r"(?:为|是|达到|录得|约|实现|[:：])?\s*[-+]?\d"
        )
        if re.search(pattern, answer):
            return True
    return False


def is_declining_answer(answer: str, question: str = "") -> bool:
    """答案是否在宣称拒答（Q3）。

    v2 边界指标实测：模型写“无法确定自由现金流是多少”，终态却是 done——
    校验只看“有没有编数字”，没有把“明确说不知道”当成拒答。v2 的拒答答案
    往往会先复述问题、再列一条**相关指标**的数字（“未提供自由现金流，仅提供
    经营性现金流 129.88 亿元”），所以不能要求整句无数字。

    判据：首句/末句含拒答词，且答案里没有“所问指标 + 数字”的正向陈述。
    正向陈述用问题里的指标说法（含同义词）判断，避免把“给了正确数字、只是
    补了一句未提供其他项”的答案误判成拒答。
    """
    clean = re.sub(r"\[c\d+\]", "", answer or "")
    # 年份不是“答案数字”：去掉 2023年 这类年份后再切句
    clean = re.sub(r"(?:19|20)\d{2}\s*年", "", clean)
    sentences = [s.strip() for s in re.split(r"[。；;\n]", clean) if s.strip()]
    if not sentences:
        return False
    probes = sentences if len(sentences) == 1 else [sentences[0], sentences[-1]]
    if not any(_DECLINE_RE.search(s) for s in probes):
        return False
    return not (question and _has_positive_metric_answer(clean, question))


def _metric_terms_in(claim: str) -> list[str]:
    return [s for group in METRIC_GROUPS.values() for s in group if s in claim]


def repair_citations(
    answer: str,
    chunks: list[dict],
    entities: set[str] | None = None,
    question: str = "",
) -> str:
    """把"引用错块"的论断自动改引到真正含该数字的块（FR-10 / RW-5 / Q3）。

    候选块约束：
    1. 属于论断/答案里提到的公司；论断是裸数字（"670.91亿元[c1]"）时，
       用**问题里的公司**兜底；
    2. 含论断里的指标词；裸数字时用**问题里的指标**兜底；
    3. 与论断同年/同源（Q4）：论断含年份用论断年份；否则用问题年份；
       两者都没有时只允许改引到被引块同一篇 source。
    这样既不让"数字碰巧相同但公司/年份不同"的块自证，也能把 Q3 的
    "答案正确但引用落点错"的裸数字改到问题对应的块。
    找不到就原样返回，绝不为了过校验放宽数字匹配。
    """
    if not answer or not chunks:
        return answer
    original_answer = answer
    # 与 split_claims 同一套引用归属：先把句末 [cN] 挪回句首，
    # 否则按句切分会把引用切到下一段，修复时拿不到“原引用/原 source”。
    answer = re.sub(
        r"([。；;!?！？])\s*((?:\[c\d+\]\s*)+)",
        lambda m: m.group(2).strip() + m.group(1),
        answer,
    )
    entities = entities or set()
    question = question or ""
    question_years = set(re.findall(r"(?:19|20)\d{2}", question))
    question_metrics = _metric_terms_in(question)
    chunk_companies = {
        company for c in chunks
        if (company := _source_company(c)) is not None
    }
    question_entities = {company for company in chunk_companies if company in question}
    allowed_entities = entities or question_entities
    by_id = {c.get("citation_id"): c for c in chunks if c.get("citation_id")}
    parts = re.split(r"(?<=[。；;\n])", answer)
    repaired: list[str] = []
    repaired_flag = False
    for segment in parts:
        claims = split_claims(segment)
        if len(claims) != 1:
            repaired.append(segment)
            continue
        claim, citation_id = claims[0]
        cited = set(citation_id.split("+")) if citation_id else set()
        evidence = " ".join(by_id[c]["text"] for c in cited if c in by_id)
        if evidence and not _missing_evidence(claim, evidence):
            repaired.append(segment)
            continue
        claim_year = _claim_year(claim)
        target_year = claim_year or (
            next(iter(question_years)) if len(question_years) == 1 else None
        )
        cited_sources = {
            by_id[c].get("source") for c in cited if c in by_id
        }
        metric_terms = _metric_terms_in(claim) or question_metrics
        candidate = next(
            (
                c for c in chunks
                if c.get("citation_id") not in cited
                and (not allowed_entities or _source_company(c) in allowed_entities)
                and (not metric_terms or any(t in c["text"] for t in metric_terms))
                and (
                    _source_year(c) == target_year
                    if target_year
                    else (
                        _source_year(c) in question_years
                        if question_years
                        else c.get("source") in cited_sources
                    )
                )
                and not _missing_evidence(claim, c["text"])
            ),
            None,
        )
        if candidate is None:
            repaired.append(segment)
            continue
        repaired.append(f"[{candidate['citation_id']}]" + segment.lstrip())
        repaired_flag = True
    return "".join(repaired) if repaired_flag else original_answer


def verify_answer(
    answer: str, chunks: list[dict]
) -> tuple[bool, float, list[ClaimVerdict]]:
    """校验答案。返回 (是否通过, 支持率, 逐论断裁定)。

    门禁策略是严格的：每个含数字的论断都必须带引用、且数字与实词都能在
    被引 chunk 里找到——一个都不放过。金融场景"带病输出"比"拒答"
    危险得多，所以这里不做比例折中；min_citation_overlap 只作为报告
    指标（支持率）留在配置里，不参与门禁。
    没有数字论断的纯定性回答直接放行——校验只管数字幻觉。
    """
    by_id = {c.get("citation_id"): c for c in chunks}
    verdicts: list[ClaimVerdict] = []
    for claim, citation_id in split_claims(answer):
        cited_ids = citation_id.split("+") if citation_id else []
        cited_chunks = [by_id.get(cid) for cid in cited_ids]
        if not cited_ids or any(c is None for c in cited_chunks):
            verdicts.append(ClaimVerdict(claim, citation_id, False, [NO_CITATION]))
            continue
        evidence = " ".join(c["text"] for c in cited_chunks if c is not None)
        missing = _missing_evidence(claim, evidence)
        verdicts.append(ClaimVerdict(claim, citation_id, not missing, missing))

    if not verdicts:
        return True, 1.0, verdicts  # 纯定性回答，放行
    supported = sum(1 for v in verdicts if v.supported)
    ratio = supported / len(verdicts)
    passed = all(v.supported for v in verdicts)
    return passed, ratio, verdicts


def _parse_numbers(text: str) -> list[tuple[str, float, str]]:
    """[(原始写法, 归一后的数值, 类别)]，类别 = percent / money / bare。"""
    out: list[tuple[str, float, str]] = []
    for m in _NUM.finditer(text):
        raw_number, unit = m.group(1), m.group(2)
        value = float(raw_number)
        if unit == "%":
            out.append((m.group(0), value, "percent"))
        elif unit in _UNIT_FACTOR:
            out.append((m.group(0), value * _UNIT_FACTOR[unit], "money"))
        else:
            out.append((m.group(0), value, "bare"))
    return out


def _same_number(
    value: float, kind: str, other_value: float, other_kind: str
) -> bool:
    # 百分比只和百分比比：5% 不能拿 5（元/亿元）顶替
    if kind == "percent" or other_kind == "percent":
        return kind == other_kind and abs(value - other_value) <= 1e-9
    # 金额与裸数字可比：LLM 常把"378.43亿元"写成"378.43"（单位在句子里）
    # 单位写法不同则先归一（0.5亿元 == 5000万元）
    return abs(value - other_value) <= max(1e-9, abs(value) * 1e-9)


def _missing_evidence(claim: str, evidence: str) -> list[str]:
    """论断里找不到依据的成分。

    两层检查，严格程度不同：
    1. 数字（硬门禁）：论断里的每个数字都必须在被引证据里按**边界 + 单位**
       找到——模型编的数字几乎不可能恰好出现在语料里，这一层拦最高危的幻觉。
    2. 内容词（软门禁）：把论断切中文二元组，至少 2 个要能在证据里
       找到——防止"数字碰巧对上但话题完全不对"的张冠李戴。
       软门禁不要求全部命中：答案的引导语（"根据检索到的研报内容："）
       不是论断内容，逐词要求全中会把正常答案误杀（实测踩坑）。
    """
    missing: list[str] = []
    evidence_numbers = _parse_numbers(evidence)
    for raw, value, kind in _parse_numbers(claim):
        if not any(_same_number(value, kind, ev, ek) for _, ev, ek in evidence_numbers):
            missing.append(raw)
    bigrams = _content_bigrams(claim)
    hits = sum(1 for g in bigrams if g in evidence)
    # 短论断（“670.91亿元”：只有“亿元”一个内容词）不能按 2 个二元组要求，
    # 否则答案数字完全正确也会被判无据；数字硬门禁仍是主要依据。
    if bigrams and hits < min(2, len(bigrams)):
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
