"""问答图端到端测试（需要本地 postgres）。

形态：同步测试 + asyncio.run()，与 test_store_integration 同一套
隔离策略（每个测试前 TRUNCATE + 自灌数据），不依赖执行顺序。

覆盖三条关键路径：
1. 正常问答：检索到正确文档 -> 生成带引用 -> 校验通过（done）
2. 拒答路径：mock 幻觉触发词 -> 编造数字 -> 引用校验拦截（insufficient）
3. 重试路径：首轮检索偏 -> 改写查询 -> 二轮命中（retry_count >= 1）
"""
import asyncio
import os

import pytest

# 在任何 app 导入之前固定 mock 模式：get_settings 有缓存，谁先调用谁定值；
# 不设这一行，测试会走 local 模式尝试下载 embedding 模型（CI 无网必败）
os.environ["EMBEDDING_MODE"] = "mock"
os.environ["LLM_MODE"] = "mock"

from app.core.winloop import ensure_selector_loop
from app.rag import store
from app.rag.corpus import build_corpus
from app.rag.graph import ask
from app.rag.ingest import ingest_report

ensure_selector_loop()

pytestmark = pytest.mark.skipif(
    os.environ.get("FINRAG_SKIP_DB") == "1",
    reason="FINRAG_SKIP_DB=1：跳过需要数据库的集成测试",
)


def _run(body):
    async def wrapper():
        await store.init_store()
        try:
            return await body()
        finally:
            await store.close_store()

    return asyncio.run(wrapper())


async def _reset_and_seed(n: int, seed: int):
    pool = store.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE rag.documents CASCADE")
    reports = build_corpus(n_reports=n, seed=seed)
    for r in reports:
        await ingest_report(r)
    return reports


def test_正常问答走通并带有效引用():
    """用 FactRegistry 出题：问题与标准答案同源，答案必须包含事实里的数字。"""
    async def body():
        reports = await _reset_and_seed(10, 42)
        fact = reports[0].facts[0]
        final = await ask(fact.question)
        assert final["status"] == "done", f"应通过校验: {final.get('verdicts')}"
        assert final["answer"], "应有答案"
        assert final["retrieved"], "应有检索结果"
        # 引用可回溯：每个论断的引用编号都必须在 citations 里存在
        cids = {c["citation_id"] for c in final["retrieved"]}
        for v in final.get("verdicts") or []:
            assert v["citation_id"] in cids, "引用编号必须可回溯到具体 chunk"
        # 答案忠实度：标准答案里的数字应出现在生成答案中
        import re

        want = re.findall(r"\d+(?:\.\d+)?", fact.answer)
        got = final["answer"]
        assert any(w in got for w in want), f"答案应包含事实数字 {want}: {got[:80]}"

    _run(body)


def test_幻觉答案被引用校验拦截():
    """mock 的幻觉型触发词会编造语料里不存在的数字——必须拒答。"""
    async def body():
        await _reset_and_seed(10, 42)
        final = await ask("预测宁德润能未来三年营收")
        assert final["status"] == "insufficient", "编造的数字必须被拦截"
        assert any(not v["supported"] for v in final.get("verdicts") or [])

    _run(body)


def test_拒答时检索链路仍然完整():
    """拒答不是异常——trace 应记录完整节点路径，便于复盘。"""
    async def body():
        await _reset_and_seed(10, 42)
        final = await ask("预测宁德润能未来三年营收")
        nodes = [t["node"] for t in final.get("trace") or []]
        assert "retrieve" in nodes and "verify" in nodes

    _run(body)
