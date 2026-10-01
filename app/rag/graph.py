"""FinRAG 问答状态机（LangGraph）。

流程：
    understand(查询理解) -> retrieve(混合检索) -> generate(带引用生成)
        -> verify(引用校验) --不过且可重试--> retrieve(改写查询后重检索)
        -> verify --不过且重试耗尽--> insufficient(拒答)
        -> verify --通过--> done

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
from app.rag.llm import get_llm
from app.rag.retrieve import hybrid_search
from app.rag.verify import verify_answer

log = get_logger("graph")

MAX_RETRIES = 2

# 行业词表：查询理解只做"从问题里识别行业过滤条件"这一件事
_INDUSTRY_WORDS = ("新能源", "半导体", "医药生物", "食品饮料", "银行")


class RAGState(TypedDict, total=False):
    question: str
    session_id: str
    original_question: str  # 首轮原始问题：rewrite_query 改写时依托它重建，
    industry: str | None
    retrieved: list[dict]
    answer: str
    verdicts: list[dict]
    status: str                 # running | insufficient | done | failed
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
    """查询理解：识别行业过滤条件 + 抽取关键实体词。"""
    started = time.perf_counter()
    q = state["question"]
    industry = next((w for w in _INDUSTRY_WORDS if w in q), None)
    entities = [t for t in re.findall(r"[一-龥A-Za-z0-9]{2,}", q)][:6]
    log.info("graph.understand", extra={"context": {"industry": industry}})
    return {
        "industry": industry,
        "trace": [_step("understand_query", started, industry=industry, entities=entities)],
    }


async def retrieve(state: RAGState) -> dict:
    """混合检索：向量 + 关键词并集，融合重排，挂引用编号。"""
    started = time.perf_counter()
    rows = await hybrid_search(state["question"], industry=state.get("industry"))
    return {
        "retrieved": rows,
        "trace": [_step("retrieve", started, n=len(rows))],
    }


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
    # 空检索守卫：一条证据都没有时直接判 insufficient（断言的拒答边界）。
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
    chunks = state.get("retrieved") or []
    passed, ratio, verdicts = verify_answer(state.get("answer", ""), chunks)
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
        "verdicts": verdict_dicts,
        "status": status,
        "trace": [_step(
            "verify", started,
            passed=passed, ratio=round(ratio, 3), claims=len(verdict_dicts),
        )],
    }


def route_after_verify(state: RAGState) -> Literal["rewrite_query", "__end__"]:
    """校验不过且还有重试额度 -> 改写查询重检索；否则结束（含拒答）。"""
    if state.get("status") == "done":
        return END
    if state.get("retry_count", 0) < MAX_RETRIES:
        return "rewrite_query"
    return END


def rewrite_query(state: RAGState) -> dict:
    """重试前改写查询：把缺失的证据词并回问题，提高下一轮召回命中率。

    只拼真实证据词（数字/中文实词）——verdict 里的状态词（如"无对应
    引用"）拼进问题会污染查询，让下一轮检索更偏。
    """
    missing: list[str] = []
    for v in state.get("verdicts") or []:
        for term in v.get("missing") or []:
            if _NUM_RE.match(term) or re.fullmatch(r"[一-龥]{2,}", term):
                missing.append(term)
    # 上一轮改写过的查询只取主干（第一个空格前的原始问题）
    # 依托 original_question 重建：早期版取 question.split(" ")[0] 会把
    # "What is 宁德润能 2025 revenue?" 截成 "What"——中文无空格所以
    # 36 个测试和全中文评测都没暴露，中英混合提问重试必残。
    base = state.get("original_question") or state["question"]
    rewritten = (
        base + " " + " ".join(dict.fromkeys(missing)) if missing else base + " 详细数据"
    )
    return {
        "question": rewritten,
        "retry_count": state.get("retry_count", 0) + 1,
        "trace": [_step("rewrite_query", time.perf_counter(), rewritten=rewritten)],
    }


_NUM_RE = re.compile(r"\d+(?:\.\d+)?%?$")


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
    builder.add_node("retrieve", retrieve)
    builder.add_node("generate", generate)
    builder.add_node("verify", verify)
    builder.add_node("rewrite_query", rewrite_query)

    builder.add_edge(START, "understand_query")
    builder.add_edge("understand_query", "retrieve")
    builder.add_edge("retrieve", "generate")
    builder.add_edge("generate", "verify")
    builder.add_conditional_edges("verify", route_after_verify, ["rewrite_query", END])
    builder.add_edge("rewrite_query", "retrieve")
    return builder.compile()


_graph = None


async def ask(question: str, session_id: str = "default") -> dict:
    """问一次：驱动状态机跑到底，返回最终状态（含拒答）。"""
    global _graph
    if _graph is None:
        _graph = build_rag_graph()
    final = await _graph.ainvoke(
        {
            "question": question,
            "session_id": session_id,
            "original_question": question,
            "status": "running",
            "retry_count": 0,
        }
    )
    return final
