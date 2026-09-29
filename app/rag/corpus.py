"""合成研报语料 + 事实登记表。

为什么语料是合成的（而不是爬真实研报）：
1. 版权：真实研报有版权，公开仓库放全文有法律风险
2. 评测合法性：ground truth 必须可证明与语料一致。合成语料的每个数字
   都由本生成器产出并登记进 FactRegistry，评测时逐条回溯——
   "答案对不对"有确定来源，不依赖人工标注
3. 可复现：固定随机种子，语料逐字节确定，任何人重跑得到同一库

FactRegistry 是 D6 评测集的种子：每条事实带 (问题, 标准答案, 证据关键词)，
评测"检索命中率"检查证据 chunk 是否被召回，"答案正确性"检查生成结果
是否包含标准答案的关键数字。
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


@dataclass
class Fact:
    fact_id: str
    source: str
    question: str
    answer: str            # 标准答案（含关键数字）
    evidence_terms: list[str]  # 证据 chunk 必须包含的词（检索命中判据）


@dataclass
class Report:
    source: str
    title: str
    industry: str
    report_date: str
    text: str
    facts: list[Fact] = field(default_factory=list)


def _fmt_money(yi: float) -> str:
    """金额统一亿元、两位小数——语料与标准答案的表示必须一致。"""
    return f"{yi:.2f}亿元"


def _gen_one(rng: random.Random, industry: str, idx: int) -> Report:
    # 按序取公司（调用方保证 idx 行业内唯一）——同一公司不重复出报告，
    # 否则同名多篇会让事实问题有多个答案，ground truth 歧义
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
        ),
        Fact(
            fact_id=f"{source}-growth",
            source=source,
            question=f"{company}{year}年营收同比增长多少？",
            answer=f"{growth}%",
            evidence_terms=[company, "同比增长", f"{growth}%"],
        ),
        Fact(
            fact_id=f"{source}-netprofit",
            source=source,
            question=f"{company}{year}年的归母净利润是多少？",
            answer=f"{_fmt_money(net_profit)}",
            evidence_terms=[company, "归母净利润", _fmt_money(net_profit)],
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


def build_corpus(n_reports: int = 30, seed: int = 20260929) -> list[Report]:
    """生成 n 篇研报（行业轮转），返回报告与全部登记事实。"""
    rng = random.Random(seed)
    reports = []
    for i in range(n_reports):
        industry = INDUSTRIES[i % len(INDUSTRIES)]
        reports.append(_gen_one(rng, industry, i))
    log.info("corpus.built", extra={"context": {"reports": len(reports), "seed": seed}})
    return reports


def all_facts(reports: list[Report]) -> list[Fact]:
    return [f for r in reports for f in r.facts]
