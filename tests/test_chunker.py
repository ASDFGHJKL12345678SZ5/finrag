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
