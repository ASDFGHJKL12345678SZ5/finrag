"""金融指标同义词典（独立于出题代码的判据来源）。

为什么单独成模块（FR-2）：旧版守卫从 `app.rag.corpus` 导入
`UNSUPPORTED_METRICS / WRONG_YEARS`——出题代码知道答案，守卫也用出题代码的
清单判断题，拒答 100% 是**循环论证**。本模块只描述"金融文本里同一个指标
有哪些常见说法"，不包含任何题目、答案或语料事实；评测集可以换、语料可以换，
守卫判据不变。

维护约定：
- 新增语料指标时同步补 `METRIC_GROUPS`（canonical + synonyms）；
- 判据只用"问题里的指标词是否出现在证据中"，不做语义推断，保持可解释。
"""
from __future__ import annotations

# canonical 指标 → 文本里可能出现的说法（含 canonical 本身）
# 顺序有意义：先匹配到的组即 canonical，用于跨说法归一。
METRIC_GROUPS: dict[str, tuple[str, ...]] = {
    "营业收入": ("营业收入", "营收", "总收入", "营收规模", "收入总额"),
    "归母净利润": ("归母净利润", "归母净利", "净利润", "净利"),
    "毛利率": ("毛利率",),
    "研发费用率": ("研发费用率", "研发投入占比", "研发费率"),
    "经营性现金流": ("经营性现金流", "经营现金流", "经营活动现金流"),
    "同比增长": ("同比增长", "同比增速", "增速", "增长率"),
    "订单量": ("订单量", "订单数量", "下单量"),
}

# 金融语料**明确不覆盖**的指标：问这些必须拒答，不许拿营收/利润充当答案。
# 与 METRIC_GROUPS 分开：这些即使语料将来覆盖，也要先登记进 METRIC_GROUPS。
UNSUPPORTED_METRICS: tuple[str, ...] = (
    "市值",
    "总市值",
    "股息率",
    "市盈率",
    "市净率",
    "股价",
    "目标价",
    "分红",
    "每股股利",
    "每股收益",
    "PE",
    "PB",
)

# 未来/预测类措辞：不带具体年份时也是无答案题（语料只有历史数据）
FUTURE_MARKERS: tuple[str, ...] = (
    "明年",
    "后年",
    "未来",
    "预计",
    "预测",
    "展望",
    "前景",
    "将会",
    "有望",
)


def metric_terms_in(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """问题里出现了哪些指标组：[(canonical, 该组在文本中出现的说法)]。

    返回命中的组与命中词，供守卫生成可解释的拒答原因（"数据里没有毛利率"）。
    """
    hits: list[tuple[str, tuple[str, ...]]] = []
    for canonical, synonyms in METRIC_GROUPS.items():
        found = tuple(s for s in synonyms if s in text)
        if found:
            hits.append((canonical, found))
    return hits


def expand_metric_terms(text: str) -> list[str]:
    """把问题里的指标说法扩成该组的全部词（FR-1 检索用）。

    为什么需要：语料正文写"营业收入"，问题可能写"营收/总收入/收入总额"。
    BM25 是字面匹配，"营收"并不是"营业收入"的连续子串——不扩词就会捞到
    同公司的现金流块（实体命中、指标没命中），答案错得"有据可依"。
    只扩**问题里已出现的指标组**，不做整句改写，避免引入无关词。
    """
    out: list[str] = []
    for canonical, synonyms in METRIC_GROUPS.items():
        if any(s in text for s in synonyms):
            for term in (canonical, *synonyms):
                if term not in out:
                    out.append(term)
    return out


def unsupported_terms_in(text: str) -> list[str]:
    return [term for term in UNSUPPORTED_METRICS if term in text]


def is_future_question(text: str) -> bool:
    return any(marker in text for marker in FUTURE_MARKERS)
