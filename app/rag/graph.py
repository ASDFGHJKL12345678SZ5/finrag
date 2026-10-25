"""FinRAG 问答状态机（LangGraph）。

流程：
    understand(查询理解) -> retrieve(混合检索) -> check_sufficiency(充分性守卫)
        -> check_sufficiency --证据不足--> insufficient(拒答，不生成)
        -> check_sufficiency --充分--> generate(带引用生成)
        -> verify(引用校验) --不过且可重试--> rewrite_query(改写查询) -> retrieve
        -> verify --不过且重试耗尽--> insufficient(拒答)
        -> verify --通过--> done

    check_sufficiency 是 D13 修复后**移到生成之前**的节点（回归测试
    test_sufficiency_refusal_skips_generate 守死这一位置）：它命中拒答时
    generate 一次都不执行——确定性判据、零 LLM 成本。早期版把它放在 verify
    节点入口，而 generate 在 verify 之前，所以"拒答"实际发生在生成之后，
    错年份问题仍会烧掉 3 次生成（初次 + 2 次重试）。

设计决策：
- 为什么用 LangGraph 而不是顺序函数：verify 不过要带着改写后的查询回到
  retrieve，这是天然的环；状态机表达重试路径比函数里写 while 清晰，
  且每个节点的入出状态可单测（D6 评测按节点打分）。
- 为什么重试上限 2 次：研报复查收益递减，且每次重试都是真实成本
  （embedding + SQL + LLM）。2 次是"给改写一次机会"与"不烧钱"的平衡。
- 拒答是一等公民：status=insufficient 是正常终态不是异常——金融场景
  "说不知道"比"编一个"正确一千倍。
"""
from __future__ import annotations

import asyncio
import operator
import re
import time
from typing import Annotated, Any, Literal

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from app.core.logging import get_logger
from app.rag.aggregate import (
    AggregateSpec,
    answer_aggregate,
    detect_aggregate,
    industry_words,
)
from app.rag.llm import get_llm
from app.rag.retrieve import hybrid_search
from app.rag.sufficiency import check_sufficiency
from app.rag.verify import repair_citations, verify_answer

log = get_logger("graph")

MAX_RETRIES = 2


class RAGState(TypedDict, total=False):
    question: str
    session_id: str
    original_question: str  # 首轮原始问题：rewrite_query 改写时依托它重建，
    industry: str | None
    aggregate: dict | None     # 行业极值查询的解析结果（FR-10）；None 走普通 RAG
    retrieved: list[dict]
    answer: str
    verdicts: list[dict]
    status: str                 # running | insufficient | done | failed
    refusal_reason: str         # 拒答归因（retrieval_empty/year_not_in_evidence/
    #                            metric_not_in_evidence）——必须声明进 schema，
    #                            否则 LangGraph 会当未知键过滤掉，调用方拿不到
    retry_count: int
    # Annotated + operator.add：trace 是累积 channel，否则每个节点的
    # trace 增量会互相覆盖，最终只剩最后一步（排查询链路时抓瞎）
    trace: Annotated[list[dict], operator.add]


def _step(node: str, started: float, **detail: Any) -> dict:
    return {
        "node": node,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "detail": detail,
    }


async def understand_query(state: RAGState) -> dict:
    """查询理解：识别行业过滤条件 + 抽取关键实体词 + 聚合意图。"""
    started = time.perf_counter()
    q = state["question"]
    industry = next((w for w in industry_words() if w in q), None)
    entities = [t for t in re.findall(r"[一-龥A-Za-z0-9]{2,}", q)][:6]
    spec = detect_aggregate(q)
    log.info(
        "graph.understand",
        extra={"context": {"industry": industry, "aggregate": bool(spec)}},
    )
    return {
        "industry": industry,
        "aggregate": spec.__dict__ if spec else None,
        "trace": [_step("understand_query", started, industry=industry,
                        entities=entities, aggregate=bool(spec))],
    }


async def aggregate_node(state: RAGState) -> dict:
    """行业极值：SQL 聚合取赢家，零 LLM 调用（FR-10）。

    这类题问的是"40 家公司里哪家最高"，检索只能捞到个别文档，argmax
    本来就该走结构化事实表。拿不到数据就拒答，不拿别家数字凑。
    """
    started = time.perf_counter()
    raw = state.get("aggregate") or {}
    spec = AggregateSpec(
        industry=raw["industry"], year=raw["year"], metric=raw["metric"]
    )
    result = await answer_aggregate(spec)
    if result is None:
        return {
            "verdicts": [],
            "status": "insufficient",
            "refusal_reason": "aggregate_empty",
            "trace": [_step("aggregate", started, found=False)],
        }
    return {
        "answer": result["answer"],
        "retrieved": result["retrieved"],
        "trace": [_step("aggregate", started, found=True,
                        company=result["company"], value=result["value"])],
    }


async def retrieve(state: RAGState) -> dict:
    """混合检索：向量 + 关键词并集，融合重排，挂引用编号。"""
    started = time.perf_counter()
    rows = await hybrid_search(state["question"], industry=state.get("industry"))
    return {
        "retrieved": rows,
        "trace": [_step("retrieve", started, n=len(rows))],
    }


async def check_sufficiency_node(state: RAGState) -> dict:
    """证据充分性守卫（D13）：问题问到的年份/指标在证据里不存在 -> 拒绝生成。

    为什么独立成节点、且放在 generate **之前**：真实模式实测，问"预测未来三年营收"
    这类问题时模型嘴上说"无法预测"但 status=done，拒答率掉到 0.1（1/10）——
    **有据可依的胡说，引用校验拦不住**（它引用的是真 chunk、数字也是真的，
    只是做了语料里没有的推断）。守卫补上这一层。

    位置的价值（早期版放在 verify 入口的代价）：那时 generate 在 verify 之前，
    "拒答"发生在生成之后，错年份问题仍会烧 3 次生成（初次 + 2 次重试）。
    挪到检索之后，命中即拒**零 LLM 调用**——
    tests/test_graph_integration.py::test_sufficiency_refusal_skips_generate 守死这一点。
    """
    started = time.perf_counter()
    chunks = state.get("retrieved") or []
    # 空检索守卫：一条证据都没有时直接判 insufficient。此前这段逻辑在 verify 里，
    # 会导致 generate 拿空上下文、LLM 输出空答案——现在不生成。verify 侧保留
    # 同样的判断作为单节点调用的兜底（两处判据一致，都由测试覆盖）。
    if not chunks:
        return {
            "verdicts": [],
            "status": "insufficient",
            "refusal_reason": "retrieval_empty",
            "trace": [_step("check_sufficiency", started, reason="retrieval_empty")],
        }
    reason = check_sufficiency(state.get("question", ""), chunks)
    if reason:
        log.info(
            "graph.sufficiency_refusal",
            extra={"context": {"reason": reason, "question": state.get("question", "")[:50]}},
        )
        return {
            "verdicts": [],
            "status": "insufficient",
            "refusal_reason": reason,
            "trace": [_step("check_sufficiency", started, passed=False, reason=reason)],
        }
    return {
        "trace": [_step("check_sufficiency", started, passed=True, n=len(chunks))],
    }


def route_after_sufficiency(state: RAGState) -> Literal["generate", "__end__"]:
    """证据不足 -> 直接结束（拒答是终态，不生成、不重试）。"""
    return END if state.get("status") == "insufficient" else "generate"


def route_after_understand(state: RAGState) -> Literal["aggregate", "retrieve"]:
    """行业极值走聚合节点；其余走普通检索。"""
    return "aggregate" if state.get("aggregate") else "retrieve"


def route_after_aggregate(state: RAGState) -> Literal["verify", "__end__"]:
    """聚合无数据 -> 直接拒答；有答案 -> 复用 verify 校验引用。"""
    return END if state.get("status") == "insufficient" else "verify"


async def generate(state: RAGState) -> dict:
    """带引用生成：证据全部进 prompt，要求答案带 [cN] 引用编号。"""
    started = time.perf_counter()
    chunks = state.get("retrieved") or []
    prompt = _build_prompt(state["question"], chunks)
    # asyncio.to_thread：OpenAI SDK 是同步阻塞调用，直接 await 会冻结
    # 整个事件循环（真实模式下一次挂起 = 所有并发请求排队至多 60s）。
    answer = await asyncio.to_thread(get_llm().generate, prompt)
    return {
        "answer": answer,
        "trace": [_step("generate", started, answer_len=len(answer))],
    }


async def verify(state: RAGState) -> dict:
    """引用校验：数字论断必须能在被引 chunk 里找到依据。"""
    started = time.perf_counter()
    chunks = state.get("retrieved") or []
    # 空检索守卫（幂等，与 check_sufficiency 节点同判据）：本节点被单独调用
    # （单测/外部复用）时也要能给出正确结果，不能依赖调用方先跑守卫节点。
    # 之前 verify_answer 对"检索结果中没有相关资料，无法回答。"这种纯定性
    # 兜底文案会放行（无数字论断 -> passed），空库部署下全部问答 status=done，
    # "拒答"只剩话术没有终态。注意：qualitative hallucination（有检索证据但
    # 答案是定性猜测）仍会放行——那是 verify.py 文档化的已知能力边界。
    if not chunks:
        return {
            "verdicts": [],
            "status": "insufficient",
            "refusal_reason": "retrieval_empty",
            "trace": [_step("verify", started, passed=False, reason="retrieval_empty")],
        }
    # 证据充分性守卫（D13）已上移到 check_sufficiency 节点（生成之前）——
    # 见 graph 模块 docstring 与 test_sufficiency_refusal_skips_generate。
    # 本节点只负责"答案里的数字有没有编造"。
    # 引用自动修复（FR-10 / RW-5）：数字在别的 retrieved 块里有据、但模型引错块时，
    # 给该句补上正确引用；只补引用不改数字，修不好仍按原判据拒答。
    # RW-5：只允许修到“同公司 + 同指标”的块，避免数字巧合跨公司自证。
    raw_answer = state.get("answer", "")
    companies = {
        c["source"].split("-")[2] for c in chunks
        if c.get("source") and c["source"].count("-") >= 2
    }
    entities = {company for company in companies if company in raw_answer}
    answer = repair_citations(raw_answer, chunks, entities=entities)
    _pre_passed, pre_ratio, _pre_verdicts = verify_answer(raw_answer, chunks)
    passed, ratio, verdicts = verify_answer(answer, chunks)
    verdict_dicts = [
        {"claim": v.claim, "citation_id": v.citation_id,
         "supported": v.supported, "missing": v.missing}
        for v in verdicts
    ]
    status = "done" if passed else "insufficient"
    log.info(
        "graph.verify",
        extra={"context": {
            "passed": passed, "ratio": round(ratio, 3), "claims": len(verdict_dicts),
        }},
    )
    return {
        "answer": answer,
        "verdicts": verdict_dicts,
        "status": status,
        "trace": [_step(
            "verify", started,
            passed=passed, ratio=round(ratio, 3), claims=len(verdict_dicts),
            pre_ratio=round(pre_ratio, 3),
            citation_repaired=answer != raw_answer,
            repair_entities=sorted(entities),
        )],
    }


def route_after_verify(state: RAGState) -> Literal["rewrite_query", "__end__"]:
    """校验不过且还有重试额度 -> 改写查询重检索；否则结束（含拒答）。

    拒答原因存在时**不重试**：这类拒答（年份/指标不在证据中）改写查询也救不回来，
    重试只会白烧 2 轮生成——实测 early 版对错年份问题烧了 3 次 generate。
    """
    if state.get("status") == "done":
        return END
    if state.get("refusal_reason"):
        return END
    if state.get("retry_count", 0) < MAX_RETRIES:
        return "rewrite_query"
    return END


def rewrite_query(state: RAGState) -> dict:
    """重试前改写查询：只用原问题的实体与指标同义词扩展。

    FR-7 修复：旧版把 verdict 的 `missing` 里的**数字**拼回查询——那些数字
    正是校验判定"不在证据里"的幻觉数字，拼回去等于把编造的数字写进检索词，
    下一轮检索要么空手而归，要么被这些数字带到无关文档（"5"还会命中"56.4"）。
    现在只用两样可回溯的东西：原问题（含公司实体）+ 指标同义词典里该问题
    命中的说法（如"总收入"扩展出注册说法"营业收入"），不引入任何新数字。
    """
    from app.rag.metric_lexicon import METRIC_GROUPS, metric_terms_in

    base = state.get("original_question") or state["question"]
    extras: list[str] = []
    for canonical, _found in metric_terms_in(base):
        for term in (canonical, *METRIC_GROUPS[canonical]):
            if term not in base and term not in extras:
                extras.append(term)
    extras = extras[:3]
    rewritten = base + (" " + " ".join(extras) if extras else "")
    return {
        "question": rewritten,
        "retry_count": state.get("retry_count", 0) + 1,
        "trace": [_step("rewrite_query", time.perf_counter(), rewritten=rewritten)],
    }


def _build_prompt(question: str, chunks: list[dict]) -> str:
    """构造带引用的生成 prompt。证据区用 [cN] 编号，与答案引用约定一致。"""
    lines = [f"问题：{question}", "", "证据（回答必须引用编号，禁止使用证据之外的数字）："]
    for c in chunks:
        lines.append(f"[{c['citation_id']}] {c['source']} / {c['section_path']}")
        lines.append(c["text"])
        lines.append("")
    return chr(10).join(lines)


def build_rag_graph():
    """组装问答状态机。"""
    builder = StateGraph(RAGState)
    builder.add_node("understand_query", understand_query)
    builder.add_node("aggregate", aggregate_node)
    builder.add_node("retrieve", retrieve)
    builder.add_node("check_sufficiency", check_sufficiency_node)
    builder.add_node("generate", generate)
    builder.add_node("verify", verify)
    builder.add_node("rewrite_query", rewrite_query)

    builder.add_edge(START, "understand_query")
    builder.add_conditional_edges(
        "understand_query", route_after_understand, ["aggregate", "retrieve"]
    )
    builder.add_conditional_edges("aggregate", route_after_aggregate, ["verify", END])
    builder.add_edge("retrieve", "check_sufficiency")
    builder.add_conditional_edges(
        "check_sufficiency", route_after_sufficiency, ["generate", END]
    )
    builder.add_edge("generate", "verify")
    builder.add_conditional_edges("verify", route_after_verify, ["rewrite_query", END])
    builder.add_edge("rewrite_query", "retrieve")
    return builder.compile()


_graph = None


def get_rag_graph():
    """进程级单例图（FR-5）：/ask 每个请求 build 一次是纯浪费——
    构图（add_node/add_edge/compile）虽不慢，但并发下每请求一份对象 +
    每次 compile 都重新绑定节点，没有任何收益。图无状态（无 checkpointer），
    单例复用是安全的；测试要新图时直接调 build_rag_graph()。
    """
    global _graph
    if _graph is None:
        _graph = build_rag_graph()
    return _graph


async def ask(question: str, session_id: str = "default") -> dict:
    """问一次：驱动状态机跑到底，返回最终状态（含拒答）。"""
    graph = get_rag_graph()
    final = await graph.ainvoke(
        {
            "question": question,
            "session_id": session_id,
            "original_question": question,
            "status": "running",
            "retry_count": 0,
        }
    )
    return final
