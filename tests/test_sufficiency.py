"""证据充分性守卫单测（D13 R7）：纯函数，不碰数据库。

守门的两条规则都是字面存在性判定——这保证了：
1. 拒答可解释（refusal_reason 是确定的原因码，不是模型的侥幸）
2. 零 LLM 成本、零模型依赖，mock 与真实模式同一判据
"""
from app.rag.sufficiency import (
    METRIC_NOT_IN_EVIDENCE,
    YEAR_NOT_IN_EVIDENCE,
    check_sufficiency,
)


def _chunk(text: str) -> dict:
    return {
        "source": "新能源-000-宁德润能",
        "chunk_index": 0,
        "section_path": "二、财务分析",
        "kind": "text",
        "text": text,
    }


CORPUS_CHUNK = _chunk(
    "2025年公司营业收入123.45亿元，同比增长12.3%；归母净利润10.50亿元。"
)


def test_问题年份不在证据中时拒答():
    """问 2024 年，证据全是 2025——语料没有答案，必须拒答。"""
    assert check_sufficiency("宁德润能2024年的营业收入是多少？", [CORPUS_CHUNK]) == (
        YEAR_NOT_IN_EVIDENCE
    )


def test_未来年份同样拒答():
    """问 2026 年（预测题的无触发词形态）——没有来路的数据不能答。"""
    assert check_sufficiency("宁德润能2026年的营业收入预计是多少？", [CORPUS_CHUNK]) == (
        YEAR_NOT_IN_EVIDENCE
    )


def test_问题年份在证据中时放行():
    assert check_sufficiency("宁德润能2025年的营业收入是多少？", [CORPUS_CHUNK]) is None


def test_语料外指标拒答():
    """问市值——语料只覆盖营收/利润类指标，正确行为是说不知道。"""
    q = "宁德润能2025年的总市值是多少？"
    assert check_sufficiency(q, [CORPUS_CHUNK]) == METRIC_NOT_IN_EVIDENCE


def test_多年份问题任一缺证据即拒答():
    """"2024到2026的趋势"——区间里有任何一个年份没证据就不能答。"""
    q = "宁德润能2024到2026年的营收趋势如何？"
    assert check_sufficiency(q, [CORPUS_CHUNK]) == YEAR_NOT_IN_EVIDENCE


def test_空检索不重复判定():
    """空检索归 verify 的 retrieval_empty 守卫，这里不抢戏。"""
    assert check_sufficiency("任意问题", []) is None


def test_不含指标词与年份的问题直接放行():
    q = "这家公司主要做什么业务？"
    assert check_sufficiency(q, [CORPUS_CHUNK]) is None
