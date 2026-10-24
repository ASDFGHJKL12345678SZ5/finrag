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
from app.rag.graph import ask, verify
from app.rag.graph import route_after_verify as g_route
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


def test_rewrite_query_保留原始问题实体():
    """改写查询必须依托 original_question（最终审查回归）。

    早期版 rewrite_query 取 question.split(" ")[0]——中文问题没有空格，
    "宁德润能2025年的营业收入是多少？"被截成"宁德润能2025年的营业收入是多少？"的
    第一个"词"整段错乱，二轮检索直接偏到无关文档。现在依托 original_question
    重建，此测试锁死该行为：改写后的问题必须仍含公司实体词。
    """
    from app.rag.graph import rewrite_query

    state = {
        "question": "宁德润能2025年的营业收入是多少？",
        "original_question": "宁德润能2025年的营业收入是多少？",
        "retry_count": 1,
    }
    out = rewrite_query(state)
    q = out["question"]
    assert "宁德润能" in q, f"改写后丢了公司实体: {q}"
    assert len(q) >= 8, f"改写把问题截断了: {q}"


def test_rewrite_query_不拼回缺失数字():
    """FR-7：verdict.missing 里的数字正是校验判"不在证据里"的幻觉数字，
    拼回查询等于把编造的数字写进检索词。"""
    from app.rag.graph import rewrite_query

    state = {
        "question": "宁德润能2025年的营业收入是多少？",
        "original_question": "宁德润能2025年的营业收入是多少？",
        "retry_count": 1,
        "verdicts": [{"missing": ["999.99亿元", "5%", "内容词命中不足(0/5)"]}],
    }
    q = rewrite_query(state)["question"]
    assert "999.99" not in q and "5%" not in q
    assert "宁德润能" in q


def test_rewrite_query_用指标同义词扩展():
    """改写只允许用可回溯的指标词扩展：问"总收入"时补注册说法"营业收入"。"""
    from app.rag.graph import rewrite_query

    state = {
        "question": "宁德润能2025年的总收入是多少？",
        "original_question": "宁德润能2025年的总收入是多少？",
        "retry_count": 1,
        "verdicts": [],
    }
    q = rewrite_query(state)["question"]
    assert "营业收入" in q, f"改写没有做指标同义扩展: {q}"


def test_rag_graph_是进程级单例():
    """FR-5：/ask 每请求 build_rag_graph() 是纯浪费；单例必须稳定复用。"""
    from app.rag.graph import get_rag_graph

    assert get_rag_graph() is get_rag_graph()


def test_空检索时verify判insufficient而不是崩溃():
    """检索为空 -> verify 守卫直接判 insufficient（最终审查修复）。

    早期版空检索会让 generate 拿到空上下文、LLM 输出空答案，verify 再对
    None 取下标直接 KeyError——一条正常链路（问了个语料外的问题）把整个
    SSE 连接打成 500。守卫回归测试：无 chunk 进入 verify 必须落
    insufficient，不得抛异常。
    """
    from app.rag.graph import verify

    out = asyncio.run(verify({"question": "语料外的问题", "retrieved": []}))
    assert out["status"] == "insufficient"
    assert out["refusal_reason"] == "retrieval_empty"

async def _reset_and_seed(n: int, seed: int):
    pool = store.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE rag.documents CASCADE")
    await store.reset_lexical_index()
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
            for cid in v["citation_id"].split("+"):
                assert cid in cids, "引用编号必须可回溯到具体 chunk"
        # 答案忠实度：标准答案里的数字应出现在生成答案中
        import re

        want = re.findall(r"\d+(?:\.\d+)?", fact.answer)
        got = final["answer"]
        assert any(w in got for w in want), f"答案应包含事实数字 {want}: {got[:80]}"

    _run(body)


def test_幻觉答案被引用校验拦截():
    """编造的数字必须被引用校验拦住。

    FR-2 后"预测/未来三年"这类题在 check_sufficiency 就被拒了（生成前），
    走不到 verify——所以这里直接驱动 verify 节点：先取一份真实检索证据，
    再用 mock 的幻觉文案替换答案，验证 verify 仍会把"无据数字"判成 insufficient。
    """
    async def body():
        await _reset_and_seed(10, 42)
        seeded = await ask("宁德润能2025年的营业收入是多少")
        chunks = seeded["retrieved"]
        assert chunks, "先要有真实检索证据"
        out = await verify({
            "question": "宁德润能2025年的营业收入是多少",
            "retrieved": chunks,
            "answer": "根据研报分析，预期值为 999.99 亿元，远超行业平均水平。[c1]",
        })
        assert out["status"] == "insufficient", "编造的数字必须被拦截"
        assert any(not v["supported"] for v in out.get("verdicts") or [])

    _run(body)


def test_拒答时检索链路仍然完整():
    """拒答不是异常——trace 应记录完整节点路径，便于复盘。"""
    async def body():
        await _reset_and_seed(10, 42)
        final = await ask("预测宁德润能未来三年营收")
        nodes = [t["node"] for t in final.get("trace") or []]
        # FR-2 起未来预测题在 check_sufficiency 就拒答，不再进入 verify
        assert "retrieve" in nodes and "check_sufficiency" in nodes

    _run(body)


def test_sufficiency_refusal_skips_generate():
    """充分性守卫必须在**生成之前**——命中拒答时 generate 一次都不许跑。

    事故记录（D13 后续修正）：守卫原来挂在 verify 节点入口，而 generate 在
    verify 之前，所以"生成前拒答"只是文档里的说法：实测错年份问题会
    `retrieve -> generate -> verify 拒答 -> rewrite -> retrieve -> generate ...`
    烧掉 **3 次生成**（初次 + 2 次重试），"零 LLM 成本"并不成立。
    修正：守卫独立成 check_sufficiency 节点，插在 retrieve 与 generate 之间，
    命中即条件边直达 END；route_after_verify 同时不再对"有拒答原因"的题重试。

    本测试沿用 test_sse_single_run 的阵法：把 LLM 生成换成"一被调用就炸"的
    探针——守卫位置回退，这里立刻红。

    注意探针**必须挂在 LLM 客户端上**，不能挂在 graph.generate 这个模块属性上：
    ask() 内部缓存的是 build_rag_graph() 编译好的图，节点函数在 compile 时
    就已绑定，事后替换模块属性拦不住图里的节点（只会得到一个永远通过的假测试）。
    """
    async def body():
        await _reset_and_seed(10, 42)

        from app.rag import llm as llm_mod

        def _tripwire(prompt):  # LLM 真被调用即失败
            raise AssertionError("充分性守卫跑在 generate 之后了——零生成拒答已回退！")

        client = llm_mod.get_llm()
        original = client.generate
        client.generate = _tripwire
        try:
            # 守卫在位 -> 不生成 -> 探针不该被触发 -> ask() 正常返回拒答终态。
            # 守卫一旦被挪回"生成之后"，generate 会真的调 LLM，
            # 探针抛的 AssertionError 从这里冒出去，测试以明确信息失败（fail-loud）。
            final = await ask("宁德润能2024年的营业收入是多少？")  # 语料只有 2025
        finally:
            client.generate = original

        assert final["status"] == "insufficient"
        assert final["refusal_reason"] == "year_not_in_evidence"
        nodes = [t["node"] for t in final.get("trace") or []]
        assert "check_sufficiency" in nodes, nodes
        assert "generate" not in nodes, f"生成被跳过了，但 trace 里出现了 generate: {nodes}"
        assert final.get("retry_count", 0) == 0, "拒答不该重试（改写查询救不回来）"
        assert not final.get("answer"), "拒答不该产出答案文本"

    _run(body)


def test_拒答路由不重试():
    """route_after_verify 对"有拒答原因"的题必须直接结束，不许回到 rewrite_query。

    纯函数级测试：不依赖数据库，锁死"拒答 = 终态"这条语义。
    早期版只看 status == done，所以年份不存在的题也会被重试 2 轮（白烧生成）。
    """
    from langgraph.graph import END

    assert g_route({"status": "done"}) == END
    assert g_route({"status": "insufficient", "refusal_reason": "year_not_in_evidence"}) == END
    assert g_route({"status": "insufficient", "refusal_reason": "retrieval_empty"}) == END
    # 没有拒答原因的真·校验失败才允许重试（还有额度时）
    assert g_route({"status": "insufficient", "retry_count": 0}) == "rewrite_query"
    assert g_route({"status": "insufficient", "retry_count": 2}) == END


def test_错年份问题被充分性守卫拒答():
    """D13 R7：问 2019 年（语料 2021–2025，多表也只回溯到 2021）——不带触发词，
    旧实现会拿 2025 的数据硬答；守卫必须拒答。

    "在生成前"这一条由 test_sufficiency_refusal_skips_generate 单独守死，
    本测试只看行为结果（status + 归因码）。
    """
    async def body():
        reports = await _reset_and_seed(10, 42)
        company = reports[0].title.split("2025")[0]
        final = await ask(f"{company}2019年的营业收入是多少？")
        assert final["status"] == "insufficient"
        assert final["refusal_reason"] == "year_not_in_evidence"

    _run(body)


def test_未来预测问题被充分性守卫拒答():
    """问 2026 年且不含"预测"触发词——真实用户的时间错问形态。"""
    async def body():
        reports = await _reset_and_seed(10, 42)
        company = reports[0].title.split("2025")[0]
        final = await ask(f"{company}2026年的营业收入预计是多少？")
        assert final["status"] == "insufficient"
        assert final["refusal_reason"] == "year_not_in_evidence"

    _run(body)


def test_语料外指标被充分性守卫拒答():
    """问市值——语料不覆盖该指标，正确行为是拒答而不是拿营业收入充当。"""
    async def body():
        reports = await _reset_and_seed(10, 42)
        company = reports[0].title.split("2025")[0]
        final = await ask(f"{company}2025年的总市值是多少？")
        assert final["status"] == "insufficient"
        assert final["refusal_reason"] == "metric_not_in_evidence"

    _run(body)
