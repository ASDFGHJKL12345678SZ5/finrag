"""金融指标形态识别（独立于出题代码的判据来源）。

FR-2 旧版用一张“不支持的指标清单”（市值/股息率/…）判拒答；RW-3 返工：
清单是穷举的，语料外指标换个说法（ROE/资产负债率/员工人数）就漏。
现在改成**正向白名单 + 指标形态**：
- 支持的指标只来自 `METRIC_GROUPS`（canonical + 同义说法）；
- 问题里出现“金融指标形态”（…率 / …额 / …比 / …数 / …量 / …天数 / 单价 /
  ROE / 商誉 / 有息负债…）但不在白名单 → 判为不覆盖，拒答；
- 未来问题用通用时间指向词（明年/未来/下一年度/接下来/会不会…）判定，
  不再枚举出题模板。

注意：本模块只描述金融文本的形态，不包含题目/答案/语料事实；评测集可以换。
"""
from __future__ import annotations

import re

# canonical 指标 → 文本里可能出现的说法（含 canonical 本身）
# 顺序有意义：先匹配到的组即 canonical，用于跨说法归一。
METRIC_GROUPS: dict[str, tuple[str, ...]] = {
    "营业收入": ("营业收入", "营收", "总收入", "营收规模", "收入总额", "收入"),
    "归母净利润": ("归母净利润", "归母净利", "净利润", "净利"),
    "毛利率": ("毛利率",),
    "研发费用率": ("研发费用率", "研发投入占比", "研发费率"),
    "经营性现金流": ("经营性现金流", "经营现金流", "经营活动现金流"),
    "同比增长": ("同比增长", "同比增速", "增速", "增长率"),
    "订单量": ("订单量", "订单数量", "下单量"),
}

_SUPPORTED_TERMS: set[str] = {
    term for group in METRIC_GROUPS.values() for term in group
} | set(METRIC_GROUPS)

# 指标形态：…率/…额/…量/…天数/单价/杠杆 + 指标名词根 + 英文缩写。
# Q2 修复：裸 `数` 后缀会把“给我一个数 / 我只需要一个数 / 请给准确数值”
# 当成未覆盖指标，在生成前误拒（v2 实测 24%）。`数` 只在前面是指标名词根
# （订单数/用户数/员工人数…）时才算形态；单独的数/数值归入泛词。
# 另：裸 `比` 会从“2021年比前一年”“多大比例”里截出“年比/多大比”，
# 同样误伤正常问句，已去掉；真正的比率形态由“比率/率”覆盖。
_METRIC_SHAPE = re.compile(
    r"(?:[一-龥A-Za-z]{1,10}(?:收益率|周转率|负债率|毛利率|净利率|费用率|比率|率"
    r"|额|量|天数|单价|杠杆)"
    r"|(?:订单|用户|客户|商品|员工|会员|门店|库存|交易|访问|点击|转化"
    r"|应收|应付|商家|渠道|SKU|sku)(?:人数|数量|数)"
    r"|ROE|ROA|ROIC|EPS|PE|PB|商誉|有息负债"
    r"|总资产|净资产|总负债|总股本|总市值|流通市值|所有者权益"
    r"|应收账款|存货|固定资产|市值|股价|目标价|分红|每股收益|每股股利)"
)

# 这些是问句结构词/泛指标词，不是“问了一个未覆盖指标”
_GENERIC_WORDS = {
    "百分比", "占比", "比例", "比重", "数量", "金额", "总额", "余额",
    "收入", "利润", "指标", "数据", "多少", "公司", "行业", "同比", "环比",
    # Q2：自然措辞里“一个数/数值/个数/数字”不是指标名
    "一个数", "数值", "个数", "数字",
}

# 通用“时间指向未来”词：相对时间/预测语气，不枚举出题模板
_FUTURE_RE = re.compile(
    r"明年|后年|未来|下一年度?|接下来|今后|后续|来年|下一财年|中长期"
    r"|走势|会不会|能否|预计|预测|展望|前景|将会|有望"
)


def metric_terms_in(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """问题里出现了哪些**受支持**指标组：[(canonical, 命中说法)]。"""
    hits: list[tuple[str, tuple[str, ...]]] = []
    for canonical, synonyms in METRIC_GROUPS.items():
        found = tuple(s for s in synonyms if s in text)
        if found:
            hits.append((canonical, found))
    return hits


def expand_metric_terms(text: str) -> list[str]:
    """把问题里的指标说法扩成该组的全部词（检索用）。"""
    out: list[str] = []
    for canonical, synonyms in METRIC_GROUPS.items():
        if any(s in text for s in synonyms):
            for term in (canonical, *synonyms):
                if term not in out:
                    out.append(term)
    return out


def _covered_by_supported(candidate: str) -> bool:
    return any(
        candidate == supported or candidate in supported or supported in candidate
        for supported in _SUPPORTED_TERMS
    )


def unsupported_terms_in(text: str) -> list[str]:
    """问题里出现的“金融指标形态但不在白名单”的词（去重、长词优先）。"""
    candidates = {
        c for c in _METRIC_SHAPE.findall(text)
        if len(c) >= 2 and not any(g in c for g in _GENERIC_WORDS)
    }
    out: list[str] = []
    for candidate in sorted(candidates, key=len, reverse=True):
        if _covered_by_supported(candidate):
            continue
        if any(candidate in existing for existing in out):
            continue
        out.append(candidate)
    return out


def is_future_question(text: str) -> bool:
    """通用未来判据：出现未来时间/预测语气即视为未来问题。"""
    return bool(_FUTURE_RE.search(text))
