"""合成研报语料 + 事实登记表 + 分级评测题面。

为什么语料是合成的（而不是爬真实研报）：
1. 版权：真实研报有版权，公开仓库放全文有法律风险
2. 评测合法性：ground truth 必须可证明与语料一致。合成语料的每个数字
   都由本生成器产出并登记进 FactRegistry，评测时逐条回溯——
   "答案对不对"有确定来源，不依赖人工标注
3. 可复现：固定随机种子，语料逐字节确定，任何人重跑得到同一库

FactRegistry 是评测集的种子：每条事实带 (问题, 标准答案, 证据关键词)，
评测"检索命中率"检查证据 chunk 是否被召回，"答案正确性"检查生成结果
是否包含标准答案的关键数字。

D13 评测分级（为什么早期指标全是 100%——太假，必须改）：
早期评测集与语料同源到"没有难度"：30 篇语料一篇一公司，问题里公司名
逐字出现，关键词检索（trigram 子串匹配）必然全中——recall@5=100% 是
**结构性必然**，不是系统能力。真实系统的 recall@5 到不了 100%，因为
真实问题有同义改写（"营收"≠"营业收入"）、有易混实体（"华曜新能" vs
"华曜新源"）、有语料覆盖不到的问题（问 2024/问市值）。本模块把这三类
难度升级为**带自动 ground truth 的一等公民**：
- variants   ：同义改写题（指标同义词替换），检索必须靠语义而非字面
- siblings   ：易混公司干扰报告（同行业、名称共享前缀），检索必须做
               实体判别而非"见词就收"
- hallucination：三类无答案题（错年份 / 语料外指标 / 未来预测），
               全部不应触发"有据回答"，且都不含 mock 幻觉触发词
- contamination：跨公司污染探针（问 A 不能答出 B 的数字）
分级之后，exact 档接近 100% 是**管线自检**（承认它），paraphrase /
拒答 / 污染档才是**质量声明**——简历只引用后者。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.core.logging import get_logger

log = get_logger("corpus")

INDUSTRIES = ["新能源", "半导体", "医药生物", "食品饮料", "银行"]

# 每行业 8 家：40 个唯一公司名。评测规模（30 篇）内保证一篇一公司——
# 同一公司出多篇不同数字的报告会让"X公司营业收入是多少"有多个答案，
# ground truth 歧义（实测 93.3% 里的 miss 全是这个原因）
COMPANIES = {
    "新能源": ["宁德润能", "华曜新能", "青蓝动力", "恒曜锂能", "晟科新源", "蓝弦能源", "璟泰新材", "曜景光伏"],
    "半导体": ["芯澈微电", "方衡半导体", "晶石科技", "曜微半导体", "弦科微", "晶曜电子", "方晟集成", "蓝澈半导体"],
    "医药生物": ["康泓生物", "安平制药", "博济医疗", "恒润医药", "璟和生物", "蓝霖制药", "晟济医疗", "泰康生物"],
    "食品饮料": ["禾裕食品", "春山乳业", "醴泉酒业", "恒禾食品", "璟山乳业", "蓝泉酒业", "晟裕食品", "泰醴酿造"],
    "银行": ["恒通银行", "浦泰银行", "民熙银行", "璟通银行", "蓝熙银行", "晟泰银行", "泰民银行", "和浦银行"],
}

# 易混公司后缀池：sibling 报告与原公司共享前缀（trigram 大面积重合），
# 但全名不同——检索必须靠完整实体判别，不能"见前缀就收"
_SIBLING_SUFFIXES = ("科技", "新源", "动力", "新能", "能源", "材料", "电子", "实业")

# 指标同义词：改写题用。刻意选**不出现在语料文本里**的写法——
# 否则改写题和原题一样简单，分级失去意义
REVENUE_SYNONYMS = ("总收入", "营收规模")
NETPROFIT_SYNONYMS = ("归母净利", "净利润")
GROWTH_SYNONYMS = ("增速", "同比增长情况")

# 语料明确不覆盖的金融指标：问这些必须拒答（指标守卫的判据来源）。
# 语料只产出 营业收入/同比增长/归母净利润/毛利率/研发费用率/经营性现金流
UNSUPPORTED_METRICS = ("市值", "股息率", "市盈率", "市净率", "股价", "分红", "每股股利", "总市值")

# 无答案题的年份：语料全部是 2025 年数据，问这些年份必须拒答
WRONG_YEARS = ("2024", "2026")


@dataclass
class Fact:
    fact_id: str
    source: str
    question: str
    answer: str            # 标准答案（含关键数字）
    evidence_terms: list[str]  # 证据 chunk 必须包含的词（检索命中判据）
    variants: list[str] = field(default_factory=list)  # 同义改写题（同源同解）


@dataclass
class HallucinationQuestion:
    """无答案题：语料里没有答案，正确行为是拒答。"""

    question: str
    htype: str             # wrong_year / missing_metric / future_prediction
    company: str
    reason_hint: str       # 人读的说明（进报告，不进判据）


@dataclass
class ContaminationPair:
    """跨公司污染探针：问 A 公司，答案不能出现 B 公司的数字。"""

    question: str          # 关于 A 的问题（A 的 canonical 问题）
    company: str           # A
    distractor_company: str  # B（易混 sibling）
    a_answer: str          # A 的标准答案数字
    b_answer: str          # B 的标准答案数字（出现即污染）


class FactRegistry:
    """事实登记表：语料产出的全部 (fact_id -> Fact) 索引（最终审查修复）。

    早期版 README/runner docstring 一直引用 FactRegistry，实现却只有
    all_facts() 列表推导——名字只活在文档里。现在把它落成真类型：build 时
    登记、by_id 回溯、questions 供评测遍历，"评测 ground truth 与语料
    同源"由此有代码对应物。
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


def build_registry(reports: list[Report]) -> FactRegistry:
    """从语料构建事实登记表（all_facts 的同源版本，数据完全一致）。"""
    registry = FactRegistry()
    for r in reports:
        for f in r.facts:
            registry.register(f)
    return registry


@dataclass
class Report:
    source: str
    title: str
    industry: str
    report_date: str
    text: str
    facts: list[Fact] = field(default_factory=list)
    # 易混干扰报告的"原型公司"（None 表示这是基准报告）——污染探针按它配对
    confusable_with: str | None = None


def _fmt_money(yi: float) -> str:
    """金额统一亿元、两位小数——语料与标准答案的表示必须一致。"""
    return f"{yi:.2f}亿元"


def _sibling_name(company: str, taken: set[str]) -> str:
    """生成易混公司名：共享前缀（trigram 大面积重合），全名不同。

    从后缀池里挑一个与原后缀不同的换上；与全部既有名不重复、
    互不为子串（ILike 全名匹配不能误伤——子串不变量由测试看守）。
    """
    for suffix in _SIBLING_SUFFIXES:
        cand = company[:-2] + suffix
        if cand == company or cand in taken:
            continue
        if any(cand in t or t in cand for t in taken):
            continue
        return cand
    raise ValueError(f"无法为 {company} 生成易混名（后缀池耗尽）")


def _gen_one(rng: random.Random, industry: str, idx: int, company: str | None = None) -> Report:
    # 按序取公司（调用方保证 idx 行业内唯一）——同一公司不重复出报告，
    # 否则同名多篇会让事实问题有多个答案，ground truth 歧义
    if company is None:
        company = COMPANIES[industry][idx % len(COMPANIES[industry])]
    year = 2025
    revenue = round(rng.uniform(20, 800), 2)
    growth = round(rng.uniform(-15, 60), 1)
    margin = round(rng.uniform(5, 40), 1)
    rd_ratio = round(rng.uniform(3, 18), 1)
    net_profit = round(revenue * rng.uniform(0.03, 0.25), 2)
    report_date = f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
    source = f"{industry}-{idx:03d}-{company}"

    text_lines = [
        f"# {company}{year}年年度研究报告",
        "",
        f"**行业**：{industry}　**报告日期**：{report_date}　**评级**：增持",
        "",
        "## 一、公司概况",
        "",
        (
            f"{company}是{industry}行业的代表性企业，主营业务覆盖"
            f"{rng.choice(['上游材料', '中游制造', '下游应用', '全产业链'])}环节。"
            f"公司{year}年实现营业收入{_fmt_money(revenue)}，同比增长{growth}%。"
        ),
        "",
        "## 二、财务分析",
        "",
        "### 2.1 营收与利润",
        "",
        (
            f"{year}年公司营业收入{_fmt_money(revenue)}，同比增长{growth}%；"
            f"归母净利润{_fmt_money(net_profit)}。毛利率{margin}%，"
            f"研发费用率{rd_ratio}%。"
        ),
        "",
        "### 2.2 现金流",
        "",
        (
            f"经营性现金流净额{_fmt_money(round(net_profit * rng.uniform(0.6, 1.4), 2))}，"
            "现金流状况良好。"
        ),
        "",
        "## 三、风险提示",
        "",
        (
            f"1. 行业竞争加剧，价格战风险；2. 原材料价格波动风险；"
            f"3. {industry}政策变化风险；4. 汇率波动风险。"
        ),
        "",
    ]

    facts = [
        Fact(
            fact_id=f"{source}-revenue",
            source=source,
            question=f"{company}{year}年的营业收入是多少？",
            answer=f"{_fmt_money(revenue)}",
            evidence_terms=[company, "营业收入", _fmt_money(revenue)],
            # 同义改写："营业收入"换成语料里没有的写法——关键词路（trigram）
            # 命中率必然下降，检索必须靠语义/实体判别（D13 分级的意义）
            variants=[
                f"{company}{year}年的{s}是多少？" for s in REVENUE_SYNONYMS
            ],
        ),
        Fact(
            fact_id=f"{source}-growth",
            source=source,
            question=f"{company}{year}年营收同比增长多少？",
            answer=f"{growth}%",
            evidence_terms=[company, "同比增长", f"{growth}%"],
            variants=[
                f"{company}{year}年营收的{s}如何？" for s in GROWTH_SYNONYMS
            ],
        ),
        Fact(
            fact_id=f"{source}-netprofit",
            source=source,
            question=f"{company}{year}年的归母净利润是多少？",
            answer=f"{_fmt_money(net_profit)}",
            evidence_terms=[company, "归母净利润", _fmt_money(net_profit)],
            variants=[
                f"{company}{year}年的{s}是多少？" for s in NETPROFIT_SYNONYMS
            ],
        ),
    ]
    return Report(
        source=source,
        title=f"{company}{year}年年度研究报告",
        industry=industry,
        report_date=report_date,
        text="\n".join(text_lines),
        facts=facts,
    )


def build_corpus(
    n_reports: int = 30, seed: int = 20260929, distractors: bool = True
) -> list[Report]:
    """生成 n 篇研报（行业轮转）+ 易混干扰报告，返回报告与全部登记事实。

    distractors=True 时，每第 4 家基准公司配一篇同行业易混报告
    （共享前缀、不同全名、不同数字）——检索难度的来源（D13）。
    """
    rng = random.Random(seed)
    reports: list[Report] = []
    taken: set[str] = set()
    for i in range(n_reports):
        industry = INDUSTRIES[i % len(INDUSTRIES)]
        report = _gen_one(rng, industry, i)
        reports.append(report)
        taken.add(report.title.split("2025")[0])
        if distractors and i % 4 == 0:
            base_company = report.title.split("2025")[0]
            sibling = _sibling_name(base_company, taken)
            taken.add(sibling)
            sib_report = _gen_one(rng, industry, 100 + i, company=sibling)
            sib_report.confusable_with = base_company
            reports.append(sib_report)
    log.info(
        "corpus.built",
        extra={"context": {"reports": len(reports), "seed": seed, "distractors": distractors}},
    )
    return reports


def all_facts(reports: list[Report]) -> list[Fact]:
    """便捷入口：直接要列表用这个；要登记表语义（按 id 回溯）用 build_registry。"""
    return [f for r in reports for f in r.facts]


def build_hallucination_questions(reports: list[Report]) -> list[HallucinationQuestion]:
    """三类无答案题（D13）——全部不应得到"有据回答"。

    刻意不含 mock 的幻觉触发词（"预测/未来三年/2030年/股价目标"）：
    早期版拒答率 100% 是触发词撞上的规则假模型；这里的题目考的是
    真实的"语料没有答案"场景——错年份、语料外指标、未来预测。
    """
    out: list[HallucinationQuestion] = []
    for r in reports[:10]:
        company = r.title.split("2025")[0]
        out.append(HallucinationQuestion(
            question=f"{company}2024年的营业收入是多少？",
            htype="wrong_year",
            company=company,
            reason_hint="语料只有 2025 年数据，问 2024 年无答案",
        ))
        out.append(HallucinationQuestion(
            question=f"{company}2025年的总市值是多少？",
            htype="missing_metric",
            company=company,
            reason_hint="市值不在语料覆盖的指标范围内",
        ))
        out.append(HallucinationQuestion(
            question=f"{company}2026年的营业收入预计是多少？",
            htype="future_prediction",
            company=company,
            reason_hint="未来数据语料没有，预测题必须拒答",
        ))
    return out


def build_contamination_pairs(reports: list[Report]) -> list[ContaminationPair]:
    """跨公司污染探针（D13）：问 A 不能答出 B 的数字。

    只对"易混公司对"生成（B 是 A 的干扰报告）——同名不同姓的场景
    最能暴露"检索到相关但错误的文档"这类静默故障。
    """
    by_company: dict[str, Report] = {}
    siblings: dict[str, Report] = {}
    for r in reports:
        company = r.title.split("2025")[0]
        by_company[company] = r
        if r.confusable_with:
            siblings[r.confusable_with] = r
    pairs: list[ContaminationPair] = []
    for base, sib in siblings.items():
        base_fact = next(f for f in by_company[base].facts if f.fact_id.endswith("-revenue"))
        sib_fact = next(f for f in sib.facts if f.fact_id.endswith("-revenue"))
        pairs.append(ContaminationPair(
            question=base_fact.question,
            company=base,
            distractor_company=sib.title.split("2025")[0],
            a_answer=base_fact.answer,
            b_answer=sib_fact.answer,
        ))
    return pairs
