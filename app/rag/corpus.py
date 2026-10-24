"""合成研报语料 + 事实登记表 + 难基准题库（FR-1）。

为什么语料是合成的（而不是爬真实研报）：
1. 版权：真实研报有版权，公开仓库放全文有法律风险
2. 评测合法性：ground truth 必须可证明与语料一致。合成语料的每个数字
   都由本生成器产出并登记进 FactRegistry，评测时逐条回溯——
   "答案对不对"有确定来源，不依赖人工标注
3. 可复现：固定随机种子，语料逐字节确定，任何人重跑得到同一库

FR-1 把语料从"40 公司 × 1 年 × 3 事实 / 30 篇评测"升级为**难基准**：
- 规模：200 家公司 × 5 年（2021–2025）= 1000 篇研报，约 1.1 万块
- 易混公司组：40 组 × 5 家（同行业、共享 2 字前缀、不同 2 字后缀），
  问题只写 4 字简称或 3 字缩写 abbr，**不写公司全名**
- 每篇 9 条事实：6 条正文指标（营收/同比/归母净利/毛利率/研发费用率/
  经营性现金流）+ 3 条分业务收入（**只出现在 Markdown 表格里**）
- 每篇还有"多年对比表"（2021..当年），计算题（同比/两年差/占比/
  行业极值）的 ground truth 可回溯到正文或表格
- 题库 800 题：精确 300 / 简称·改写 300 / 计算 100 / 拒答 100

为什么难（旧基准为什么"全是 100%"）：
旧语料一篇一公司、问题里公司名逐字出现，关键词检索必然全中。新基准
的区分度来自四层互相独立的难度，每一层都有可自动核验的 ground truth：
1. 实体混淆：40 组易混公司共享前缀（"华曜新能/华曜新源/华曜能源…"），
   检索必须做实体判别，见词就收会把兄弟公司的数字答出来
2. 别名：问题用 4 字简称或 3 字缩写，公司名不再逐字等于文档标识
3. 同义改写：营业收入/营收/总收入/营收规模/收入总额换着说，关键词路
   必须靠语义/实体，而不是字面重合
4. 聚合与拒答：计算题要跨年/跨表/跨公司取数；拒答题问语料没有的
   年份（2019/2020/2026/2027）、指标（市值/市盈率/股价/股息率）和
   未来预测——正确答案是"不知道"

FactRegistry 仍是评测集的种子：每条事实带 (问题, 标准答案, 证据关键词)，
`build_benchmark` 只从这些已登记事实派生题目，因此 ground truth 与语料
同源、可逐条回溯。`seed` 同时驱动财务序列与题目抽样，多种子可复跑。

数据契约（与 store/agent 协作）：
- source = `{industry}-{idx:03d}-{short}-{year}`（company_names 从这里
  解析简称；abbr/code/full 走 documents.meta，ingest_report 写入）
- 语料年份严格限定 2021–2025：拒答题的错年份（2019/2020/2026/2027）
  绝不能出现在正文/表格/报告日期里，否则年份守卫的判据被污染
"""
from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from functools import lru_cache

from app.core.logging import get_logger

log = get_logger("corpus")

INDUSTRIES = ["新能源", "半导体", "医药生物", "食品饮料", "银行"]

DEFAULT_YEARS: tuple[int, ...] = (2021, 2022, 2023, 2024, 2025)
DEFAULT_N_COMPANIES = 200
MAX_COMPANIES = 200
FIRST_YEAR = 2021
LAST_YEAR = 2025
# 财务序列的内部起点：2021 的同比需要 2020 的基数，但 2020 **不写进语料**
BASE_YEAR = 2020

# 每行业 8 个 2 字前缀 × 5 个 2 字后缀 = 40 家；同一前缀的 5 家构成
# 一个易混公司组（同行业、共享前缀、不同后缀）
_COMPANY_PREFIXES: dict[str, tuple[str, ...]] = {
    "新能源": ("华曜", "青蓝", "恒曜", "晟科", "蓝弦", "璟泰", "曜景", "中瀚"),
    "半导体": ("芯澈", "方衡", "晶石", "曜微", "弦科", "晶曜", "方晟", "蓝澈"),
    "医药生物": ("康泓", "安平", "博济", "恒润", "璟和", "蓝霖", "晟济", "泰康"),
    "食品饮料": ("禾裕", "春山", "醴泉", "恒禾", "璟山", "蓝泉", "晟裕", "泰醴"),
    "银行": ("恒通", "浦泰", "民熙", "璟通", "蓝熙", "晟泰", "泰民", "和浦"),
}
_COMPANY_SUFFIXES: dict[str, tuple[str, ...]] = {
    "新能源": ("新能", "新源", "能源", "动力", "材料"),
    "半导体": ("微电", "半导", "电子", "集成", "芯片"),
    "医药生物": ("生物", "制药", "医疗", "医药", "健康"),
    "食品饮料": ("食品", "乳业", "酒业", "酿造", "饮料"),
    "银行": ("银行", "金控", "金融", "资本", "信托"),
}
# 3 字缩写候选的"序列字"：这些字不出现在任何简称/全名里，用它拼出的
# 缩写天然不可能是简称的子串（兜底候选，正常情况用不到）
_ABBR_MARKS = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"

# 分业务名称（每个行业三条业务线）
_SEGMENTS: dict[str, tuple[str, str, str]] = {
    "新能源": ("锂电池材料", "储能系统", "光伏组件"),
    "半导体": ("芯片设计", "晶圆制造", "封装测试"),
    "医药生物": ("创新药", "医疗器械", "原料药"),
    "食品饮料": ("乳制品", "酒类", "休闲食品"),
    "银行": ("公司金融", "零售金融", "金融市场"),
}

# 指标 canonical（Fact.metric）与问题里的自然说法
_BODY_METRICS: tuple[str, ...] = (
    "营业收入",
    "同比增长",
    "归母净利润",
    "毛利率",
    "研发费用率",
    "经营性现金流",
)
_METRIC_LABEL: dict[str, str] = {
    "营业收入": "营业收入",
    "同比增长": "营业收入同比增长",
    "归母净利润": "归母净利润",
    "毛利率": "毛利率",
    "研发费用率": "研发费用率",
    "经营性现金流": "经营性现金流净额",
}
# 同义改写：与 metric_lexicon.METRIC_GROUPS 保持一致的"语料里未必出现"的说法。
# 改写题只换问题文本，source/answer/evidence 与母题完全一致。
_METRIC_SYNONYMS: dict[str, tuple[str, ...]] = {
    "营业收入": ("营收", "总收入", "营收规模", "收入总额"),
    "同比增长": ("同比增速", "增速", "增长率"),
    "归母净利润": ("归母净利", "净利润", "净利"),
    "毛利率": ("毛利率水平",),
    "研发费用率": ("研发投入占比", "研发费率"),
    "经营性现金流": ("经营现金流", "经营活动现金流"),
}

# 语料明确不覆盖的指标：拒答题用。正文/表格/风险段都不得出现这些词。
UNSUPPORTED_METRICS: tuple[str, ...] = ("市值", "市盈率", "股价", "股息率")
# 错年份：语料只有 2021–2025，这些年份必须不存在于任何文本
WRONG_YEARS: tuple[str, ...] = ("2019", "2020", "2026", "2027")

BENCH_CATEGORIES = ("exact", "abbr", "calculation", "refusal")
# 计算题四类的抽样权重：同比 40 / 两年差 20 / 占比 20 / 行业极值 20
_CALC_WEIGHTS = (4, 2, 2, 2)
_REFUSAL_WEIGHTS = (4, 3, 3)


@dataclass(frozen=True)
class Company:
    """一家合成公司：4 字简称 / 长全名 / 3 字缩写 / 6 位代码 / 行业 / 易混组。"""

    short: str      # 4 字简称，问题里最常用（"华曜新能"）
    full: str       # 长全名（"华曜新能股份有限公司"），只出现在正文
    code: str       # 6 位证券代码
    industry: str
    abbr: str       # 唯一 3 字缩写（"华新能"），与简称互不为子串
    group: str      # 易混组 id（同组共享 2 字前缀、同行业）


@dataclass
class Fact:
    """登记事实：问题/答案/证据词与语料同源。"""

    fact_id: str
    source: str
    question: str
    answer: str            # 标准答案（含关键数字/单位）
    evidence_terms: list[str]  # 证据 chunk 必须全部包含的词（检索命中判据）
    metric: str = ""       # canonical 指标名（营业收入/归母净利润/分业务收入…）
    variants: list[str] = field(default_factory=list)  # 同义改写题（同源同解）
    table_only: bool = False  # True = 只出现在表格里的事实
    segment: str = ""      # 分业务名称（仅分业务事实）
    share: float = 0.0     # 分业务占营业收入比重（仅分业务事实）


class FactRegistry:
    """事实登记表：语料产出的全部 (fact_id -> Fact) 索引。

    build 时登记、by_id 回溯、questions 供评测遍历——"评测 ground truth
    与语料同源"由此有代码对应物，而不是一句文档承诺。
    """

    def __init__(self) -> None:
        self._facts: dict[str, Fact] = {}

    def register(self, fact: Fact) -> None:
        if fact.fact_id in self._facts:
            raise ValueError(f"fact_id 重复登记: {fact.fact_id}（语料生成有 bug）")
        self._facts[fact.fact_id] = fact

    def by_id(self, fact_id: str) -> Fact:
        return self._facts[fact_id]

    def facts(self) -> list[Fact]:
        return list(self._facts.values())

    def questions(self) -> list[tuple[str, Fact]]:
        """[(问题文本, 对应事实)]——评测 driver 的遍历入口。"""
        return [(f.question, f) for f in self._facts.values()]

    def __len__(self) -> int:
        return len(self._facts)


@dataclass
class Report:
    source: str
    title: str
    industry: str
    report_date: str
    text: str
    facts: list[Fact] = field(default_factory=list)
    # 结构化元数据：评测生成计算题/回溯 source 时不再从 title 里切字符串
    company: Company | None = None
    year: int = 0
    revenue: float = 0.0
    growth: float = 0.0
    delta: float = 0.0


@dataclass(frozen=True)
class BenchQuestion:
    """难基准题：ground truth 全部可回溯到 FactRegistry / 语料。"""

    qid: str
    category: str        # exact | abbr | calculation | refusal
    question: str
    source: str          # 回答该题所需的源文档（拒答题为空）
    answer: str          # 标准答案（拒答题为空）
    evidence_terms: list[str]  # 证据 chunk 必须全含（拒答题为空）
    expected: str        # answer | refuse
    subtype: str = ""    # 精确档=指标；简称档=abbr/synonym；计算/拒答=细类


@dataclass(frozen=True)
class _Financials:
    revenue: dict[int, float]
    growth: dict[int, float]
    delta: dict[int, float]
    net_profit: dict[int, float]
    gross_margin: dict[int, float]
    rd_ratio: dict[int, float]
    cashflow: dict[int, float]
    segments: dict[int, tuple[tuple[str, float, float], ...]]  # (名称, 收入, 占比%)


def _fmt_money(yi: float) -> str:
    return f"{yi:.2f}亿元"


def _fmt_pct(pct: float) -> str:
    return f"{pct:.2f}%"


def _abbr_candidates(short: str) -> list[str]:
    """3 字缩写候选：先自然组合，再带序列字兜底（保证可分配）。"""
    prefix, suffix = short[:2], short[2:4]
    out = [
        prefix[0] + suffix,          # 华曜新能 -> 华新能
        prefix[1] + suffix,          # 曜新能
        prefix + suffix[0],          # 华曜新
        prefix[0] + prefix[1] + suffix[0],  # 华曜新
    ]
    for mark in _ABBR_MARKS:
        out.append(prefix[1] + suffix[0] + mark)  # 曜新甲
        out.append(prefix[0] + suffix[1] + mark)  # 华能甲
    return out


def _assign_abbrs(companies: list[Company]) -> list[Company]:
    """给每家公司分配唯一 3 字缩写，保证与任何简称/全名互不为子串。

    为什么这么严：`detect_anchor` 做最长匹配、`fuse` 用包含判断判 source。
    如果缩写是某家简称的子串（哪怕同公司），问 A 会误锚到 B 的文档。
    """
    shorts = {c.short for c in companies}
    fulls = {c.full for c in companies}
    used: set[str] = set()
    out: list[Company] = []
    for company in companies:
        for cand in _abbr_candidates(company.short):
            if len(cand) != 3 or cand in used:
                continue
            if any(cand in s for s in shorts):
                continue  # 不能是任何简称的子串
            if any(cand in f for f in fulls):
                continue  # 不能是任何全名的子串
            used.add(cand)
            out.append(replace(company, abbr=cand))
            break
        else:  # pragma: no cover - 候选池足够大，正常不会耗尽
            raise ValueError(f"无法为 {company.short} 生成唯一 3 字缩写")
    return out


@lru_cache(maxsize=8)
def _companies_cached(n_companies: int) -> tuple[Company, ...]:
    n = max(0, min(n_companies, MAX_COMPANIES))
    base: list[Company] = []
    for i in range(n):
        industry = INDUSTRIES[i % len(INDUSTRIES)]
        within = i // len(INDUSTRIES)
        group_idx = within // 5
        member = within % 5
        short = _COMPANY_PREFIXES[industry][group_idx] + _COMPANY_SUFFIXES[industry][member]
        base.append(
            Company(
                short=short,
                full=f"{short}股份有限公司",
                code=f"{600000 + i + 1:06d}",
                industry=industry,
                abbr="",  # 由 _assign_abbrs 统一分配（依赖全局唯一性）
                group=f"G{industry_index(industry) * 8 + group_idx:02d}",
            )
        )
    return tuple(_assign_abbrs(base))


def industry_index(industry: str) -> int:
    return INDUSTRIES.index(industry)


def build_companies(n_companies: int = DEFAULT_N_COMPANIES) -> list[Company]:
    """按确定性顺序返回公司（行业轮转，200 家覆盖 5 行业 × 40 家）。"""
    return list(_companies_cached(n_companies))


def _financials(company: Company, seed: int) -> _Financials:
    """按公司生成 2020–2025 连续财务序列（确定性 seed，趋势连续）。

    2020 只作为 2021 同比的内部基数，**不写进任何文本**——否则
    "问 2020 年"的拒答题就有了证据，年份守卫的 ground truth 被污染。
    """
    rng = random.Random(f"{seed}|{company.code}|financial")
    base_revenue = round(rng.uniform(18.0, 900.0), 2)
    trend = rng.uniform(2.0, 20.0)
    revenue: dict[int, float] = {BASE_YEAR: base_revenue}
    growth: dict[int, float] = {}
    delta: dict[int, float] = {}
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        raw = max(0.8, min(45.0, trend + rng.uniform(-4.0, 4.0)))
        prev = revenue[year - 1]
        cur = round(prev * (1 + raw / 100.0), 2)
        revenue[year] = cur
        delta[year] = round(cur - prev, 2)
        growth[year] = round(delta[year] / prev * 100.0, 2)

    net_margin = rng.uniform(5.0, 24.0)
    gross_base = rng.uniform(20.0, 60.0)
    rd_base = rng.uniform(3.0, 12.0)
    cash_base = rng.uniform(0.7, 1.3)
    net_profit: dict[int, float] = {}
    gross_margin: dict[int, float] = {}
    rd_ratio: dict[int, float] = {}
    cashflow: dict[int, float] = {}
    segments: dict[int, tuple[tuple[str, float, float], ...]] = {}
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        margin = max(1.0, net_margin + rng.uniform(-1.5, 1.5))
        net_profit[year] = round(revenue[year] * margin / 100.0, 2)
        gross_margin[year] = round(max(8.0, gross_base + rng.uniform(-2.0, 2.0)), 2)
        rd_ratio[year] = round(max(0.5, rd_base + rng.uniform(-0.6, 0.6)), 2)
        cashflow[year] = round(
            net_profit[year] * max(0.3, cash_base + rng.uniform(-0.2, 0.2)), 2
        )
        a = rng.uniform(0.32, 0.52)
        b = rng.uniform(0.18, 0.34)
        v1 = round(revenue[year] * a, 2)
        v2 = round(revenue[year] * b, 2)
        v3 = round(revenue[year] - v1 - v2, 2)
        names = _SEGMENTS[company.industry]
        rows = []
        for name, value in zip(names, (v1, v2, v3), strict=True):
            share = round(value / revenue[year] * 100.0, 2)
            rows.append((name, value, share))
        segments[year] = tuple(rows)
    return _Financials(
        revenue=revenue,
        growth=growth,
        delta=delta,
        net_profit=net_profit,
        gross_margin=gross_margin,
        rd_ratio=rd_ratio,
        cashflow=cashflow,
        segments=segments,
    )


def _gen_report(company: Company, year: int, fin: _Financials, seed: int) -> Report:
    """生成一篇年报：全名/简称/abbr/代码 + 财务数字 + 两张表 + 风险段。"""
    rng = random.Random(f"{seed}|{company.code}|{year}|report")
    report_date = f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
    short, abbr, full, code = company.short, company.abbr, company.full, company.code
    revenue = fin.revenue[year]
    growth = fin.growth[year]
    delta = fin.delta[year]
    net_profit = fin.net_profit[year]
    gross = fin.gross_margin[year]
    rd = fin.rd_ratio[year]
    cash = fin.cashflow[year]
    rev_s, np_s = _fmt_money(revenue), _fmt_money(net_profit)
    growth_s, gross_s, rd_s = _fmt_pct(growth), _fmt_pct(gross), _fmt_pct(rd)
    delta_s, cash_s = _fmt_money(delta), _fmt_money(cash)

    history = [y for y in range(FIRST_YEAR, year + 1)]
    compare_rows = [
        f"| {y} | {_fmt_money(fin.revenue[y])} | {_fmt_pct(fin.growth[y])} "
        f"| {_fmt_money(fin.net_profit[y])} | {_fmt_pct(fin.gross_margin[y])} |"
        for y in history
    ]
    segment_rows = [
        f"| {name} | {_fmt_money(value)} | {_fmt_pct(share)} |"
        for name, value, share in fin.segments[year]
    ]

    text = "\n".join(
        [
            f"# {short}{year}年年度研究报告",
            "",
            (
                f"**行业**：{company.industry}　**报告日期**：{report_date}　"
                f"**评级**：增持　**证券代码**：{code}"
            ),
            "",
            "## 一、公司概况",
            "",
            (
                f"公司全称：{full}；证券简称：{short}；证券简称缩写：{abbr}；"
                f"证券代码：{code}。"
            ),
            "",
            (
                f"公司是{company.industry}行业的代表性企业，主营业务覆盖研发、"
                "生产与销售全链条，产品与服务广泛应用于下游核心客户。公司持续"
                "投入技术研发与产能建设，市场份额保持稳定。"
            ),
            "",
            "## 二、行业分析",
            "",
            (
                f"{company.industry}行业需求与宏观景气度、产业政策和技术迭代相关。"
                "报告期内行业竞争格局保持稳定，头部企业凭借规模与研发优势维持"
                "较强议价能力。"
            ),
            "",
            "## 三、财务分析",
            "",
            "### 3.1 营收与利润",
            "",
            (
                f"{short}（{abbr}）{year}年实现营业收入{rev_s}，同比增长{growth_s}，"
                f"较上年增加{delta_s}；归母净利润{np_s}，毛利率{gross_s}，"
                f"研发费用率{rd_s}。"
            ),
            "",
            (
                "公司收入结构保持稳定，成本控制与产品结构优化共同支撑盈利水平，"
                "期间费用率保持在合理区间。"
            ),
            "",
            "### 3.2 现金流",
            "",
            (
                f"{short}（{abbr}）{year}年经营性现金流净额{cash_s}，与净利润规模"
                "基本匹配，现金流出主要集中在原材料采购与产能建设。"
            ),
            "",
            "### 3.3 分业务收入",
            "",
            f"| 业务板块 | {year}年收入 | 占比 |",
            "| --- | --- | --- |",
            *segment_rows,
            "",
            "### 3.4 多年对比",
            "",
            "| 年份 | 营业收入 | 同比增长 | 归母净利润 | 毛利率 |",
            "| --- | --- | --- | --- | --- |",
            *compare_rows,
            "",
            "## 四、风险提示",
            "",
            (
                "- 行业竞争加剧，价格战可能压缩毛利空间。\n"
                "- 原材料价格波动可能影响成本端。\n"
                f"- {company.industry}产业政策变化可能带来不确定性。\n"
                "- 汇率波动可能影响海外业务结算。"
            ),
            "",
            (
                "公司通过多元化采购、套期保值与客户结构优化对冲上述风险，"
                "并持续跟踪政策与市场变化。"
            ),
            "",
        ]
    )

    source = f"{company.industry}-{_company_index(company):03d}-{short}-{year}"
    facts: list[Fact] = []

    def body_fact(metric: str, answer: str, terms: list[str]) -> None:
        label = _METRIC_LABEL[metric]
        facts.append(
            Fact(
                fact_id=f"{source}-{metric}",
                source=source,
                question=f"{short}{year}年的{label}是多少？",
                answer=answer,
                evidence_terms=terms,
                metric=metric,
                variants=[
                    f"{short}{year}年的{syn}是多少？"
                    for syn in _METRIC_SYNONYMS[metric]
                    if syn != label
                ],
            )
        )

    body_fact("营业收入", rev_s, [short, f"{year}年", "营业收入", rev_s])
    body_fact("同比增长", growth_s, [short, "同比增长", growth_s])
    body_fact("归母净利润", np_s, [short, "归母净利润", np_s])
    body_fact("毛利率", gross_s, [short, "毛利率", gross_s])
    body_fact("研发费用率", rd_s, [short, "研发费用率", rd_s])
    body_fact("经营性现金流", cash_s, [short, "经营性现金流", cash_s])

    for i, (name, value, share) in enumerate(fin.segments[year], start=1):
        value_s = _fmt_money(value)
        facts.append(
            Fact(
                fact_id=f"{source}-segment-{i}",
                source=source,
                question=f"{short}{year}年{name}业务收入是多少？",
                answer=value_s,
                # 证据词只取表格里出现的词（表格事实只在表格）
                evidence_terms=[name, value_s],
                metric="分业务收入",
                table_only=True,
                segment=name,
                share=share,
            )
        )

    return Report(
        source=source,
        title=f"{short}{year}年年度研究报告",
        industry=company.industry,
        report_date=report_date,
        text=text,
        facts=facts,
        company=company,
        year=year,
        revenue=revenue,
        growth=growth,
        delta=delta,
    )


def _company_index(company: Company) -> int:
    """公司序号：与 build_companies 的顺序一致（source 里的人类可读编号）。"""
    return int(company.code) - 600001


def build_corpus(
    n_companies: int = DEFAULT_N_COMPANIES,
    years: Sequence[int] | None = None,
    seed: int = 20260929,
    n_reports: int | None = None,
) -> list[Report]:
    """生成 `n_companies × len(years)` 篇研报（默认 200×5=1000 篇）。

    - years 只允许 2021–2025 的子集：语料年份是拒答题 ground truth 的一部分
    - `n_reports` 是旧的冒烟入口（兼容 FR-1 前的调用）：等价于
      "取 n_companies 家公司、只生成最新年份"，报告数 == 公司数
    """
    if n_reports is not None:
        n_companies = n_reports
        years = (LAST_YEAR,)
    selected_years = tuple(sorted(years or DEFAULT_YEARS))
    if not selected_years:
        return []
    bad = [y for y in selected_years if y < FIRST_YEAR or y > LAST_YEAR]
    if bad:
        raise ValueError(
            f"语料年份必须在 {FIRST_YEAR}–{LAST_YEAR}：{bad}（拒答题依赖语料没有其他年份）"
        )
    if n_companies < 0:
        raise ValueError("n_companies 不能为负")
    if n_companies > MAX_COMPANIES:
        raise ValueError(f"n_companies 最多 {MAX_COMPANIES}（公司名池只有这么多）")

    companies = build_companies(n_companies)
    reports: list[Report] = []
    for company in companies:
        fin = _financials(company, seed)
        for year in selected_years:
            reports.append(_gen_report(company, year, fin, seed))
    log.info(
        "corpus.built",
        extra={
            "context": {
                "reports": len(reports),
                "companies": len(companies),
                "years": list(selected_years),
                "facts": len(reports) * 9,
                "seed": seed,
            }
        },
    )
    return reports


def all_facts(reports: list[Report]) -> list[Fact]:
    """便捷入口：直接要列表用这个；要登记表语义（按 id 回溯）用 build_registry。"""
    return [f for r in reports for f in r.facts]


def build_registry(reports: list[Report]) -> FactRegistry:
    """从语料构建事实登记表（all_facts 的同源版本，数据完全一致）。"""
    registry = FactRegistry()
    for r in reports:
        for f in r.facts:
            registry.register(f)
    return registry


# ---------------------------------------------------------------------------
# 题库（FR-1）
# ---------------------------------------------------------------------------


def _split_counts(total: int, weights: Sequence[int]) -> list[int]:
    """按权重把 total 拆成整数份（余数从前往后分），保证各份之和 == total。"""
    if total <= 0:
        return [0] * len(weights)
    denom = sum(weights)
    counts = [total * w // denom for w in weights]
    for i in range(total - sum(counts)):
        counts[i % len(counts)] += 1
    return counts


def _sample(rng: random.Random, items: Sequence, k: int) -> list:
    """确定性抽样：k 超过候选数时取全部（小语料冒烟不报错）。"""
    if k <= 0 or not items:
        return []
    return rng.sample(list(items), min(k, len(items)))


def _question_with_name(name: str, fact: Fact, year: int) -> str:
    if fact.metric == "分业务收入":
        label = f"{fact.segment}业务收入"
    else:
        label = _METRIC_LABEL[fact.metric]
    return f"{name}{year}年的{label}是多少？"


def _industry_extremes(reports: list[Report]) -> list[tuple[str, int, Report]]:
    """(行业, 年份) -> 营业收入最高的报告（行业极值题的 ground truth）。"""
    best: dict[tuple[str, int], Report] = {}
    for r in reports:
        if r.company is None:
            continue
        key = (r.industry, r.year)
        cur = best.get(key)
        if cur is None or r.revenue > cur.revenue:
            best[key] = r
    return [(ind, year, best[(ind, year)]) for ind, year in sorted(best)]


def build_benchmark(
    reports: list[Report],
    seed: int = 42,
    n_exact: int = 300,
    n_abbr: int = 300,
    n_calc: int = 100,
    n_refusal: int = 100,
) -> list[BenchQuestion]:
    """从语料派生 800 题难基准（exact/abbr/calculation/refusal）。

    所有答案都来自 `reports[*].facts`（FactRegistry 同源）；计算题只用
    正文/表格里已出现的数字（同比、较上年增加额、占比、行业最高营收），
    拒答题的年份/指标/未来措辞在语料里不存在——ground truth 可逐条回溯。
    """
    rng = random.Random(f"bench|{seed}")
    indexed = [r for r in reports if r.company is not None]
    by_source = {r.source: r for r in indexed}
    body_facts = [f for r in indexed for f in r.facts if not f.table_only]
    by_metric: dict[str, list[Fact]] = {
        metric: sorted(
            (f for f in body_facts if f.metric == metric), key=lambda f: f.fact_id
        )
        for metric in _BODY_METRICS
    }

    questions: list[BenchQuestion] = []

    # ---- 精确档：4 字简称 + canonical 指标 ----
    exact_counts = _split_counts(n_exact, [1] * len(_BODY_METRICS))
    for metric, k in zip(_BODY_METRICS, exact_counts, strict=True):
        for fact in _sample(rng, by_metric[metric], k):
            questions.append(
                BenchQuestion(
                    qid=f"exact-{len(questions) + 1:04d}",
                    category="exact",
                    question=fact.question,
                    source=fact.source,
                    answer=fact.answer,
                    evidence_terms=list(fact.evidence_terms),
                    expected="answer",
                    subtype=metric,
                )
            )

    # ---- 简称·改写档：一半 3 字缩写，一半指标同义说法 ----
    abbr_counts = _split_counts(n_abbr, [1] * len(_BODY_METRICS))
    for metric, k in zip(_BODY_METRICS, abbr_counts, strict=True):
        picked = _sample(rng, by_metric[metric], k)
        synonyms = _METRIC_SYNONYMS[metric]
        for j, fact in enumerate(picked):
            report = by_source[fact.source]
            assert report.company is not None
            if j % 2 == 0:
                question = _question_with_name(report.company.abbr, fact, report.year)
                subtype = "abbr"
            else:
                synonym = synonyms[(j // 2) % len(synonyms)]
                question = f"{report.company.short}{report.year}年的{synonym}是多少？"
                subtype = "synonym"
            questions.append(
                BenchQuestion(
                    qid=f"abbr-{len(questions) + 1:04d}",
                    category="abbr",
                    question=question,
                    source=fact.source,
                    answer=fact.answer,
                    evidence_terms=list(fact.evidence_terms),
                    expected="answer",
                    subtype=subtype,
                )
            )

    # ---- 计算档：同比 40 / 两年差 20 / 分业务占比 20 / 行业极值 20 ----
    calc_counts = _split_counts(n_calc, _CALC_WEIGHTS)
    growth_reports = sorted(
        (r for r in indexed if r.year >= 2022), key=lambda r: r.source
    )
    share_facts = sorted(
        (f for r in indexed for f in r.facts if f.table_only), key=lambda f: f.fact_id
    )
    calc_questions: list[BenchQuestion] = []

    for report in _sample(rng, growth_reports, calc_counts[0]):
        fact = next(f for f in report.facts if f.metric == "同比增长")
        calc_questions.append(
            BenchQuestion(
                qid="calc-tmp",
                category="calculation",
                question=f"{report.company.short}{report.year}年的营业收入同比增长了多少？",  # type: ignore[union-attr]
                source=report.source,
                answer=fact.answer,
                evidence_terms=list(fact.evidence_terms),
                expected="answer",
                subtype="yoy",
            )
        )
    for report in _sample(rng, growth_reports, calc_counts[1]):
        delta_s = _fmt_money(report.delta)
        prev_revenue = round(report.revenue - report.delta, 2)
        calc_questions.append(
            BenchQuestion(
                qid="calc-tmp",
                category="calculation",
                question=(
                    f"{report.company.short}{report.year}年的营业收入"
                    "较上年增加了多少亿元？"  # type: ignore[union-attr]
                ),
                source=report.source,
                answer=delta_s,
                # 增加额本身不在正文里（需要跨年相减），证据词用两年营收数字：
                # 多年对比表同一行同时含两年值，检索命中判据可验证
                evidence_terms=[
                    report.company.short,  # type: ignore[union-attr]
                    "营业收入",
                    _fmt_money(report.revenue),
                    _fmt_money(prev_revenue),
                ],
                expected="answer",
                subtype="yoy_diff",
            )
        )
    for fact in _sample(rng, share_facts, calc_counts[2]):
        report = by_source[fact.source]
        share_s = _fmt_pct(fact.share)
        calc_questions.append(
            BenchQuestion(
                qid="calc-tmp",
                category="calculation",
                question=(
                    f"{report.company.short}{report.year}年{fact.segment}业务收入"  # type: ignore[union-attr]
                    "占营业收入的比重是多少？"
                ),
                source=fact.source,
                answer=share_s,
                evidence_terms=[fact.segment, *_value_terms(fact), share_s],
                expected="answer",
                subtype="segment_share",
            )
        )
    extremes = _industry_extremes(indexed)
    for industry, year, report in _sample(rng, extremes, calc_counts[3]):
        assert report.company is not None
        calc_questions.append(
            BenchQuestion(
                qid="calc-tmp",
                category="calculation",
                question=f"{industry}行业{year}年营业收入最高的公司是哪家？",
                source=report.source,
                answer=report.company.short,
                evidence_terms=[
                    report.company.short,
                    "营业收入",
                    _fmt_money(report.revenue),
                ],
                expected="answer",
                subtype="industry_max",
            )
        )
    for i, q in enumerate(calc_questions, start=1):
        questions.append(
            BenchQuestion(
                qid=f"calc-{i:04d}",
                category=q.category,
                question=q.question,
                source=q.source,
                answer=q.answer,
                evidence_terms=q.evidence_terms,
                expected=q.expected,
                subtype=q.subtype,
            )
        )

    # ---- 拒答档：错年份 40 / 语料外指标 30 / 未来预测 30 ----
    refusal_counts = _split_counts(n_refusal, _REFUSAL_WEIGHTS)
    for i in range(n_refusal):
        if i < refusal_counts[0]:
            subtype = "wrong_year"
        elif i < refusal_counts[0] + refusal_counts[1]:
            subtype = "missing_metric"
        else:
            subtype = "future_prediction"
        if not indexed:
            break
        report = indexed[i % len(indexed)]
        assert report.company is not None
        short, year = report.company.short, report.year
        if subtype == "wrong_year":
            bad_year = WRONG_YEARS[i % len(WRONG_YEARS)]
            question = f"{short}{bad_year}年的营业收入是多少？"
        elif subtype == "missing_metric":
            metric = UNSUPPORTED_METRICS[i % len(UNSUPPORTED_METRICS)]
            question = f"{short}{year}年的{metric}是多少？"
        elif i % 2 == 0:
            question = f"预测{short}未来三年的营业收入"
        else:
            question = f"{short}明年的营业收入预计是多少？"
        questions.append(
            BenchQuestion(
                qid=f"refusal-{len(questions) + 1:04d}",
                category="refusal",
                question=question,
                source="",
                answer="",
                evidence_terms=[],
                expected="refuse",
                subtype=subtype,
            )
        )

    log.info(
        "benchmark.built",
        extra={
            "context": {
                "questions": len(questions),
                "by_category": {
                    c: sum(1 for q in questions if q.category == c) for c in BENCH_CATEGORIES
                },
                "seed": seed,
            }
        },
    )
    return questions


def _value_terms(fact: Fact) -> list[str]:
    """分业务事实的证据值词（表格里出现的是 "xx.xx亿元"）。"""
    return [t for t in fact.evidence_terms if t != fact.segment]
