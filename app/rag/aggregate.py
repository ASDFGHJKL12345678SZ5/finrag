"""跨文档聚合问答（FR-10）：行业极值类问题不再靠检索撞运气。

问题形态："食品饮料行业 2024 年营业收入最高的公司是哪家？"
- 单文档 RAG 只能捞到个别公司，无法在 40 家公司里做 argmax（FR-9 实测
  industry_max 3/20）；
- 入库时已把每条事实落成 `rag.facts` 行，这里用一条 SQL 取极值，再取赢家
  文档里的证据块做引用。**确定性数学，不调用 LLM**。

边界：只支持"整公司口径"的指标（营业收入/归母净利润/毛利率/研发费用率/
经营性现金流/同比增长），分业务口径不支持；没有数据时返回 None，由 graph
判 insufficient（不能拿别家数字充数）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.logging import get_logger
from app.rag import store

log = get_logger("aggregate")


def industry_words() -> tuple[str, ...]:
    """行业词表 = 语料元信息 `industry` 字段的取值集合，本模块不手写。

    R1：旧版在这里手写 `("新能源", "半导体", ...)`，与语料生成器各维护一份；
    语料换行业/加行业时两处会漂移。词表只从 `corpus.INDUSTRIES`（也就是
    `Report.industry` 的全部取值）派生，检测逻辑对任意行业自动生效。
    函数内导入是为了让"词表随语料元信息变化"可被单测直接验证（monkeypatch
    `corpus.INDUSTRIES` 后立即生效），也避免导入本模块时把语料生成器拖进来。
    """
    from app.rag.corpus import INDUSTRIES

    return tuple(INDUSTRIES)

# 顺序有意义：先匹配更具体的指标，避免"净利润"被"收入"这类泛词抢走
_METRIC_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("归母净利润", ("归母净利润", "净利润", "净利")),
    ("营业收入", ("营业收入", "营收", "收入")),
    ("毛利率", ("毛利率",)),
    ("研发费用率", ("研发费用率", "研发投入占比")),
    ("经营性现金流", ("经营性现金流", "经营现金流")),
    ("同比增长", ("同比增长", "同比增速")),
)
_SUPERLATIVE_WORDS = ("最高", "最大", "第一", "居首", "榜首", "最多")
_YEAR_RE = re.compile(r"20\d{2}")


@dataclass(frozen=True)
class AggregateSpec:
    industry: str
    year: int
    metric: str
    direction: str = "max"

    @property
    def question_label(self) -> str:
        return f"{self.year}年{self.industry}行业{self.metric}最高的公司"


def detect_aggregate(question: str) -> AggregateSpec | None:
    """识别行业极值问题；不是这类问题返回 None（走普通 RAG 链路）。

    判据是四项同时命中：**行业词**（语料 industry 取值）+ 极值词 + 年份 + 指标。
    "行业"二字只是可选的加强词，不再是必要条件——留出集 v1 的失败题写的是
    "银行 2022 年营收居首的公司"，不含"行业"，旧判据（`"行业" not in question`）
    直接漏判，退回单文档 RAG 后只能在个别公司里挑，industry_max 0/6。
    """
    industry = next(
        (w for w in sorted(industry_words(), key=len, reverse=True) if w in question),
        None,
    )
    if not industry:
        return None
    if not any(w in question for w in _SUPERLATIVE_WORDS):
        return None
    year_match = _YEAR_RE.search(question)
    if not year_match:
        return None
    metric = None
    for canonical, synonyms in _METRIC_PATTERNS:
        if any(s in question for s in synonyms):
            metric = canonical
            break
    if not metric:
        return None
    return AggregateSpec(industry=industry, year=int(year_match.group()), metric=metric)


async def answer_aggregate(spec: AggregateSpec) -> dict | None:
    """SQL 取极值 + 赢家证据块；返回 {answer, retrieved}，无数据返回 None。"""
    row = await store.aggregate_facts(spec.metric, spec.year, spec.industry)
    if row is None:
        return None
    source = row["source"]
    company = source.split("-")[2] if source.count("-") >= 2 else row["title"]
    chunks = await store.chunks_for_source(source)
    # 引用块必须包含答案数字（verify 会按数字回溯），数字找不到就退回首块
    chunk = next((c for c in chunks if row["answer"] in c["text"]), None)
    if chunk is None and chunks:
        chunk = chunks[0]
    if chunk is None:
        return None
    chunk = dict(chunk)
    chunk["citation_id"] = "c1"
    answer = (
        f"{spec.year}年{spec.industry}行业{spec.metric}最高的公司是"
        f"{company}（{row['answer']}）[c1]"
    )
    log.info(
        "aggregate.done",
        extra={"context": {"industry": spec.industry, "year": spec.year,
                           "metric": spec.metric, "company": company}},
    )
    return {"answer": answer, "retrieved": [chunk], "company": company,
            "value": row["value"]}
