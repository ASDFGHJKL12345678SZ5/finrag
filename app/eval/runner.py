"""评测闭环：FactRegistry 驱动的四类指标 + 分策略对比 + 落库。

指标定义（全部可回溯到具体事实，不搞人工印象分）：
1. retrieval_hit@k  ：事实的源文档出现在 top-k 检索结果里的比例
2. answer_accuracy  ：标准答案里的关键数字出现在生成答案里的比例
3. citation_faithfulness：数字论断被引用证据支持的比例
4. refusal_accuracy ：幻觉型问题被拒答的比例（应接近 100%）

分策略对比（检索策略的 before/after）：
- vector_only：只用向量路（mock 模式下等价随机基线——诚实标注）
- keyword_only：只用关键词路
- hybrid：两路融合（系统当前策略）
同一批事实、同一 top-k，三个策略各自的命中率并排给出。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.logging import get_logger
from app.eval.schema import EVAL_SCHEMA_SQL
from app.rag import store
from app.rag.corpus import all_facts, build_corpus
from app.rag.ingest import embed_query, ingest_report
from app.rag.retrieve import fuse
from app.rag.verify import verify_answer

log = get_logger("eval")

# 评测默认 mock 模式：CI/无 key 环境秒级跑完（模型下载只在真实标定时发生）。
# 必须在任何 get_settings() 调用之前设——settings 是 lru_cache 单件，
# 早期版把这行放在 _reset_and_seed 里（init_store 之后），缓存已定值，
# 设了也白设（死代码）；真实标定要显式 EMBEDDING_MODE=local。
os.environ.setdefault("EMBEDDING_MODE", "mock")

_HALLUCINATION_PREFIX = "预测"


def _key_numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text)


async def init_eval_store() -> None:
    pool = store.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(EVAL_SCHEMA_SQL)


async def _reset_and_seed(n_reports: int, seed: int):
    pool = store.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE rag.documents CASCADE")
    reports = build_corpus(n_reports=n_reports, seed=seed)
    for r in reports:
        await ingest_report(r)
    return reports


async def _search_strategy(
    question: str, strategy: str, industry: str | None, top_k: int
) -> list[dict]:
    """按策略检索：vector_only / keyword_only / hybrid。"""
    qv = embed_query(question)
    v_rows = await store.vector_search(qv, top_k=top_k, industry=industry)
    if strategy == "vector_only":
        rows = v_rows
    else:
        # keyword_only/hybrid 共用系统同一份 IDF+指标词加权（keyword_weights）：
        # 三策略对比只允许差"检索策略"，不允许差打分函数——早期版 keyword_only
        # 不传 weights，把关键词路加权的增益错算进了 hybrid 的账。
        from app.rag.retrieve import keyword_weights

        weights = await keyword_weights(question)
        k_rows = await store.keyword_search(
            question, top_k=top_k, industry=industry, weights=weights
        )
        if strategy == "keyword_only":
            rows = k_rows
        else:
            settings = get_settings()
            vw = 0.0 if settings.embedding_mode == "mock" else settings.vector_weight
            kw = 1.0 if settings.embedding_mode == "mock" else settings.keyword_weight
            rows = fuse(v_rows, k_rows, vector_weight=vw, keyword_weight=kw)
    return rows[:top_k]


async def run_eval(n_reports: int = 30, seed: int = 42, top_k: int = 5) -> dict:
    """跑完整评测，返回汇总指标（已落库）。"""
    await store.init_store()
    await init_eval_store()
    reports = await _reset_and_seed(n_reports, seed)
    facts = all_facts(reports)
    settings = get_settings()

    per_fact: list[dict] = []
    strategies = ("vector_only", "keyword_only", "hybrid")
    hit_counts = dict.fromkeys(strategies, 0)
    answer_ok = 0
    faithfulness_claims = [0, 0]  # [supported, total]
    refused = 0
    latencies: list[float] = []

    for fact in facts:
        row: dict = {"fact_id": fact.fact_id, "question": fact.question}
        for st in strategies:
            rows = await _search_strategy(fact.question, st, None, top_k)
            hit = any(r["source"] == fact.source for r in rows)
            row[f"hit_{st}"] = hit
            hit_counts[st] += int(hit)

        # 端到端问答（走完整状态机）
        from app.rag.graph import ask

        started = time.perf_counter()
        final = await ask(fact.question, session_id=f"eval-{fact.fact_id}")
        latencies.append((time.perf_counter() - started) * 1000)

        want = _key_numbers(fact.answer)
        got = final.get("answer", "")
        ans_ok = any(w in got for w in want) and final.get("status") == "done"
        row["answer_ok"] = ans_ok
        row["status"] = final.get("status")
        answer_ok += int(ans_ok)
        refused += int(final.get("status") == "insufficient")

        _passed, ratio, verdicts = verify_answer(final.get("answer", ""),
                                                final.get("retrieved") or [])
        faithfulness_claims[0] += sum(1 for v in verdicts if v.supported)
        faithfulness_claims[1] += len(verdicts)
        row["citation_ratio"] = round(ratio, 3)
        per_fact.append(row)

    # 幻觉型问题：拒答准确率
    hallucination_qs = [f"预测{f.source.split('-')[-1]}未来三年营收" for f in facts[:10]]
    refuse_ok = 0
    for q in hallucination_qs:
        final = await ask(q, session_id="eval-hallucination")
        refuse_ok += int(final.get("status") == "insufficient")

    n = len(facts)
    latencies.sort()
    metrics = {
        "mode": settings.embedding_mode,
        "corpus_seed": seed,
        "n_reports": n_reports,
        "n_facts": n,
        "top_k": top_k,
        "retrieval_hit": {
            st: round(hit_counts[st] / n, 4) for st in strategies
        },
        "answer_accuracy": round(answer_ok / n, 4),
        "citation_faithfulness": round(
            faithfulness_claims[0] / faithfulness_claims[1], 4
        ) if faithfulness_claims[1] else 1.0,
        "refusal_accuracy": round(refuse_ok / len(hallucination_qs), 4),
        "refused_count": refused,
        "latency_ms": {
            "p50": round(latencies[len(latencies) // 2], 1),
            "p95": round(latencies[int(len(latencies) * 0.95)], 1),
            "max": round(latencies[-1], 1),
        },
        "evaluated_at": datetime.now(UTC).isoformat(),
    }

    pool = store.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO eval.runs (mode, corpus_seed, n_facts, metrics, per_fact)"
            " VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)",
            settings.embedding_mode, seed, n,
            json.dumps(metrics, ensure_ascii=False),
            json.dumps(per_fact, ensure_ascii=False),
        )

    log.info("eval.done", extra={"context": metrics})
    return metrics


def main() -> None:
    reports_n = int(os.environ.get("FINRAG_EVAL_REPORTS", "30"))
    seed = int(os.environ.get("FINRAG_EVAL_SEED", "42"))
    result = asyncio.run(run_eval(n_reports=reports_n, seed=seed))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
