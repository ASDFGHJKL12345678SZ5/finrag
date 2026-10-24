"""证据充分性守卫单测（D13 R7）：纯函数，不碰数据库。

守门的两条规则都是字面存在性判定——这保证了：
1. 拒答可解释（refusal_reason 是确定的原因码，不是模型的侥幸）
2. 零 LLM 成本、零模型依赖，mock 与真实模式同一判据
"""
from app.rag.sufficiency import (
    FUTURE_NOT_IN_EVIDENCE,
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


# ---- FR-2：证据驱动 + 独立词典 + 未来措辞 ----


def test_未来措辞且无具体年份时拒答():
    """证据只有历史数据，"未来/明年/预计"且无年份 = 无答案题。"""
    for q in (
        "宁德润能未来的营业收入会是多少？",
        "宁德润能明年的营收预计如何？",
        "预测一下这家公司的前景",
    ):
        assert check_sufficiency(q, [CORPUS_CHUNK]) == FUTURE_NOT_IN_EVIDENCE, q


def test_同义改写不被误拒():
    """问"总收入"、证据写"营业收入"——同义词典归一后必须放行。

    这是 paraphrase 档误拒的来源：守卫如果只做字面包含，改写题会被
    确定性拒答，评测会把它误记成"检索失败"。
    """
    assert check_sufficiency("宁德润能2025年的总收入是多少？", [CORPUS_CHUNK]) is None
    assert check_sufficiency("宁德润能2025年营收规模如何？", [CORPUS_CHUNK]) is None
    assert check_sufficiency("宁德润能2025年归母净利是多少？", [CORPUS_CHUNK]) is None


def test_指标词不在证据中时拒答():
    """语料有营收/利润，但证据里没有毛利率——不能拿别的指标硬答。"""
    q = "宁德润能2025年的毛利率是多少？"
    assert check_sufficiency(q, [CORPUS_CHUNK]) == METRIC_NOT_IN_EVIDENCE


def test_守卫不得依赖出题代码():
    """FR-2 不变量：sufficiency.py 的 import 里不能出现 app.rag.corpus。

    早期版从 corpus 导入 UNSUPPORTED_METRICS/WRONG_YEARS——出题代码知道答案，
    守卫再用它判"该不该拒"，拒答率 100% 是循环论证。AST 检查比 grep 可靠：
    注释/docstring 里提到 corpus 不算违规，真正的 import 才算。
    """
    import ast
    from pathlib import Path

    src = Path("app/rag/sufficiency.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        for module in modules:
            assert not module.startswith("app.rag.corpus"), f"守卫依赖了出题代码：{module}"


def test_守卫不借用出题清单常量():
    """常量名也不能借（防止用 from corpus import X 的别名绕过 import 检查）。

    用 AST 收集代码里真正出现的标识符；docstring 里提到常量名不算违规
    （说明"旧版为什么错"是必要的文档）。
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path("app/rag/sufficiency.py").read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "UNSUPPORTED_METRICS" not in names
    assert "WRONG_YEARS" not in names

