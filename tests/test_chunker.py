"""分块器不变量测试。

这些不是"功能测试"而是"不变量测试"：守住结构感知分块的核心理由——
章节路径正确、表格不切、块索引连续、超长必切。任何一条被破坏，
检索就会开始返回"半句话"。
"""

from app.core.config import get_settings
from app.rag.chunker import chunk_document

SAMPLE = """# 某公司2025年研究报告

## 一、公司概况

某公司是新能源行业的代表性企业。公司2025年实现营业收入123.45亿元，同比增长12.3%。

## 二、财务分析

### 2.1 营收与利润

2025年公司营业收入123.45亿元。归母净利润23.45亿元。毛利率18.5%。

| 指标 | 2024年 | 2025年 |
|---|---|---|
| 营业收入 | 110.00亿元 | 123.45亿元 |
| 归母净利润 | 20.00亿元 | 23.45亿元 |

## 三、风险提示

1. 行业竞争加剧。2. 原材料价格波动。
"""


def test_章节路径正确():
    chunks = chunk_document(SAMPLE, source="r1")
    paths = {c.section_path for c in chunks}
    assert "一、公司概况" in paths
    assert "二、财务分析 > 2.1 营收与利润" in paths
    assert "三、风险提示" in paths


def test_表格整块保留():
    chunks = chunk_document(SAMPLE, source="r1")
    tables = [c for c in chunks if c.kind == "table"]
    assert len(tables) == 1
    assert "营业收入" in tables[0].text and "123.45亿元" in tables[0].text
    # 表格块不能被截断：表头和数据行必须在同一块
    assert tables[0].text.count("|") >= 8


def test_块索引连续且从0开始():
    chunks = chunk_document(SAMPLE, source="r1")
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_超长段落必切():
    long_para = "这是一段很长的话。" * 200  # 约 1600 字
    doc = "## 长文" + chr(10) * 2 + long_para
    chunks = chunk_document(doc, source="r2")
    assert len(chunks) > 1
    settings = get_settings()
    for c in chunks:
        # 重叠会让块略超上限，但不允许超出一倍
        assert len(c.text) <= settings.chunk_max_chars * 2


def test_每个块都带source元数据():
    chunks = chunk_document(SAMPLE, source="my-source")
    assert all(c.source == "my-source" for c in chunks)
    assert all(c.meta.get("char_len", 0) > 0 for c in chunks)


def test_空文档返回空列表():
    assert chunk_document("", source="empty") == []


# ---- FR-3：表格感知分块（超长切分/表头重复/表格后接标题/文末表格）----

def _table_doc(n_rows: int) -> str:
    header = "| 指标 | 金额 | 增速 |" + chr(10) + "|---|---|---|"
    rows = chr(10).join(
        f"| 指标{i:02d} | {i * 1.1:.1f}亿元 | {i * 2.2:.1f}% |" for i in range(n_rows)
    )
    return "## 财务分析" + chr(10) + chr(10) + header + chr(10) + rows + chr(10)


def test_超长表格按行组切分且每块重复表头():
    n = 40
    chunks = chunk_document(_table_doc(n), source="t1", max_chars=220, overlap=0)
    tables = [c for c in chunks if c.kind == "table"]

    assert len(tables) > 1, "超长表格必须切，且切出来的每块都是 table"
    for c in tables:
        lines = c.text.splitlines()
        assert lines[0] == "| 指标 | 金额 | 增速 |", "每块必须以表头开头"
        assert lines[1].startswith("|---"), "表头分隔行必须跟着重复"
        assert len(c.text) <= 220, f"切分后仍超长：{len(c.text)}"
        assert all(line.startswith("|") for line in lines), "表格块不能被句子滑窗污染"

    # 数据行不丢、不重、顺序不变（跳过每块的两行表头）
    data_rows = [line for c in tables for line in c.text.splitlines()[2:]]
    assert len(data_rows) == n
    assert data_rows[0].startswith("| 指标00 |")
    assert data_rows[-1].startswith(f"| 指标{n - 1:02d} |")


def test_文末表格的_kind_必须是_table():
    doc = "## 附注" + chr(10) * 2 + "| a | b |" + chr(10) + "|---|---|" + chr(10) + "| 1 | 2 |"
    chunks = chunk_document(doc, source="t2")
    assert chunks, "不能因为文末没有正文就把表格丢掉"
    assert chunks[-1].kind == "table", "旧实现 flush 默认 text，文末表格 kind 会错"
    assert chunks[-1].section_path == "附注"
    assert "1" in chunks[-1].text and "2" in chunks[-1].text


def test_表格后接标题时_kind_与章节路径都正确():
    doc = (
        "## 一、财务" + chr(10) * 2
        + "| 指标 | 2025年 |" + chr(10) + "|---|---|" + chr(10) + "| 营收 | 12.3亿元 |"
        + chr(10) * 2
        + "## 二、风险" + chr(10) * 2 + "行业竞争加剧。"
    )
    chunks = chunk_document(doc, source="t3")
    table = next(c for c in chunks if c.kind == "table")
    assert "营收" in table.text
    assert table.section_path == "一、财务"

    risk = next(c for c in chunks if "行业竞争" in c.text)
    assert risk.kind == "text"
    assert risk.section_path == "二、风险"
    # 表格块必须在风险段之前（顺序不丢）
    assert chunks.index(table) < chunks.index(risk)


def test_无分隔行的表格也能整块保留():
    doc = "## 简表" + chr(10) * 2 + "| 项目 | 数值 |" + chr(10) + "| 收入 | 1.0亿元 |"
    chunks = chunk_document(doc, source="t4")
    assert chunks[-1].kind == "table"
    assert "1.0亿元" in chunks[-1].text

