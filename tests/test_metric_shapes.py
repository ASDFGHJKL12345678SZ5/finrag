"""RW-3 FinRAG 指标形态白名单 + 通用未来判据：不误伤正常问句。"""
from __future__ import annotations

import pytest

from app.rag.metric_lexicon import is_future_question, unsupported_terms_in
from app.rag.sufficiency import check_sufficiency

NORMAL_QUESTIONS = [
    "宁德润能2025年的营业收入是多少？",
    "华曜新能2024年的归母净利润是多少？",
    "康泓生物2023年的毛利率是多少？",
    "芯澈微电2025年的研发费用率是多少？",
    "方衡半导体的经营性现金流是多少？",
    "禾裕食品2022年的营业收入同比增长多少？",
    "泰醴饮料2024年的总收入是多少？",
    "恒通银行2023年的营收规模是多少？",
    "蓝熙信托2025年的收入总额是多少？",
    "春山食品2021年的营收是多少？",
    "博济制药2024年的净利润是多少？",
    "晶石科技2023年的毛利率水平是多少？",
    "晟济健康2022年的研发投入占比是多少？",
    "安平医疗2025年的经营现金流是多少？",
    "浦泰银行2024年的同比增速是多少？",
    "青蓝材料2023年的订单量是多少？",
    "这个问题里百分比是多少？",  # “百分比”是问句结构词，不是指标
    # Q2：自然措辞里的“数/数值”不是指标形态（v2 曾因此误拒 24%）
    "给我一个数：华曜新能2024年的总收入",
    "我只需要一个数：华曜新能2024年的总收入",
    "华曜新能2024年的总收入，请给准确数值",
]

EXTRA_METRICS = [
    ("ROE 是多少？", "ROE"),
    ("资产负债率是多少？", "资产负债率"),
    ("净资产收益率是多少？", "净资产收益率"),
    ("存货周转率是多少？", "存货周转率"),
    ("员工人数是多少？", "员工人数"),
    ("商誉是多少？", "商誉"),
    ("总资产是多少？", "总资产"),
    # Q2：指标名词根 + 数 仍应识别为形态
    ("用户数是多少？", "用户数"),
    ("客户数是多少？", "客户数"),
]

FUTURE_QUESTIONS = [
    "预测宁德润能2025年的营业收入",
    "下一年度营业收入会怎样？",
    "接下来毛利率会如何？",
    "会不会涨？",
    "后续走势如何？",
    "明年上半年的营收预计多少？",
]


@pytest.mark.parametrize("question", NORMAL_QUESTIONS)
def test_正常问句不触发指标与未来拒答(question: str) -> None:
    assert unsupported_terms_in(question) == []
    assert is_future_question(question) is False


@pytest.mark.parametrize("question,metric", EXTRA_METRICS)
def test_语料外指标被形态识别(question: str, metric: str) -> None:
    assert metric in unsupported_terms_in(question)


@pytest.mark.parametrize("question", FUTURE_QUESTIONS)
def test_通用未来措辞被识别(question: str) -> None:
    assert is_future_question(question)


def test_形态白名单守卫拒绝语料外指标() -> None:
    chunks = [{"text": "宁德润能2025年营业收入518.75亿元，同比增长13.1%。"}]
    assert check_sufficiency("宁德润能2025年的ROE是多少？", chunks) == "metric_not_in_evidence"
    assert check_sufficiency("宁德润能2025年的营业收入是多少？", chunks) is None


def test_未来措辞守卫拒绝且带年份时交给年份守卫() -> None:
    chunks = [{"text": "宁德润能2025年营业收入518.75亿元。"}]
    assert check_sufficiency("下一年度营业收入会怎样？", chunks) == "future_not_in_evidence"
    # 带明确年份时由年份守卫判（2025 在证据里 -> 不因未来词拒）
    assert check_sufficiency("预测宁德润能2025年的营业收入", chunks) is None
