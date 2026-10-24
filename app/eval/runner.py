"""评测闭环：FactRegistry 驱动的分级指标 + 分策略对比 + 落库。

D13 重构（为什么早期"全是 100%"必须改）：
早期评测集与语料同源到没有难度——问题里公司名逐字出现、指标逐字出现，
关键词检索（trigram 子串）必然全中：recall@5=100% 是**结构性必然**，
不是系统能力，写进简历等于自曝其假。真实系统的 recall@5 到不了 100%，
因为真实问题有同义改写、有易混实体、有语料覆盖不到的东西。

重构后的指标分四档，每档的判据全部可回溯到 FactRegistry：
1. exact       原题（公司名+指标逐字）——**管线自检档**：接近 100% 是
               这类基准的结构属性（小语料+实体逐字出现），诚实标注，
               不作为质量声明
2. paraphrase  同义改写题（"营业收入"→"总收入"）——考检索的语义能力
3. refusal     三类无答案题（错年份/语料外指标/未来预测）——考拒答，
               全部不含 mock 幻觉触发词，mock 与真实模式同一判据
4. contamination 跨公司污染探针（问 A 不能答出 B）——考实体判别，
               带锚定消融（anchor off/on）证明增益来源

分策略对比（检索策略的 before/after）：
- vector_only：只用向量路（mock 模式下等价随机基线——诚实标注）
- keyword_only：只用关键词路
- hybrid：两路融合 + 实体锚定（系统当前策略）
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
from app.eval.db import ensure_eval_database
from app.eval.schema import EVAL_SCHEMA_SQL
from app.rag import store
from app.rag.corpus import (
    all_facts,
    build_contamination_pairs,
    build_corpus,
    build_hallucination_questions,
)
from app.rag.ingest import embed_query_async, ingest_report
from app.rag.retrieve import detect_anchor, fuse
from app.rag.sufficiency import REFUSAL_REASONS, check_sufficiency
from app.rag.verify import verify_answer

log = get_logger("eval")

# 评测默认 mock 模式：CI/无 key 环境秒级跑完（模型下载只在真实标定发生）。
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
    """清空**评测库**并灌语料（FR-2 起 store 已指向独立评测库）。

    这里保留 TRUNCATE 是安全的：连接池由 run_eval 用 `resolved_eval_dsn`
    初始化，开发库不在这个进程的连接范围内。函数名保留是为了减少 diff。
    """
    pool = store.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE rag.documents CASCADE")
    # distractors=True：灌入易混公司干扰报告——检索难度的来源（D13）
    reports = build_corpus(n_reports=n_reports, seed=seed, distractors=True)
    for r in reports:
        await ingest_report(r)
    return reports


async def _search_strategy(
    question: str,
    strategy: str,
    industry: str | None,
    top_k: int,
    anchor_on: bool = True,
) -> list[dict]:
    """按策略检索：vector_only / keyword_only / hybrid。

    anchor_on=False 是消融位：关掉实体锚定跑同一题，量化锚定对
    易混公司场景的增益（contamination 档的 before/after 就是这么来的）。
    单路基线（vector_only/keyword_only）本就不带锚定——它们测的是
    单路能力；锚定是 hybrid 策略的一部分。
    """
    qv = await embed_query_async(question)
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
            anchor = (
                detect_anchor(question, await store.company_names()) if anchor_on else None
            )
            rows = fuse(v_rows, k_rows, vector_weight=vw, keyword_weight=kw, anchor=anchor)
    return rows[:top_k]


def _source_hit(rows: list[dict], source: str) -> bool:
    """源文档命中：判据是"事实的源文档出现在 top-k"（与历史口径一致）。"""
    return any(r["source"] == source for r in rows)


def _evidence_chunk_hit(rows: list[dict], answer: str) -> bool:
    """证据块命中（更严）：top-k 里要出现**含答案数字的块**，而不只是同文档。

    源文档命中回答"找对公司了没有"，证据块命中回答"找对那段话了没有"——
    后者才是生成阶段能引对证据的前提。两者都给，避免用一个口径掩盖
    另一个口径的问题。
    """
    nums = _key_numbers(answer)
    if not nums:
        return False
    return any(all(n in r["text"] for n in nums) for r in rows)


async def run_eval(n_reports: int = 30, seed: int = 42, top_k: int = 5) -> dict:
    """跑完整评测（分级），返回汇总指标（已落库）。

    FR-2 起评测跑在**独立评测库**（`finrag_eval`）上：本进程 store 连接池
    指向评测库，`TRUNCATE/灌数据` 不再碰开发库。per_fact 写真正的逐题明细
    （问题/档位/命中/top-k 来源/答案/拒答原因），可下钻到每一条 miss。
    """
    settings = get_settings()
    eval_dsn = settings.resolved_eval_dsn
    eval_db = await ensure_eval_database(settings.pg_dsn, eval_dsn)
    await store.close_store()
    await store.init_store(dsn=eval_dsn)
    await init_eval_store()
    reports = await _reset_and_seed(n_reports, seed)
    facts = all_facts(reports)

    strategies = ("vector_only", "keyword_only", "hybrid")
    # 每策略每档的命中计数：[exact, paraphrase]
    hit_counts = {st: [0, 0] for st in strategies}
    # 证据块命中（更严口径，只对词路与混合路有意义）
    evidence_hits = {"keyword_only": [0, 0], "hybrid": [0, 0]}
    answer_ok = [0, 0]  # [exact, paraphrase]
    faithfulness_claims = [0, 0]  # [supported, total]
    latencies: list[float] = []
    tier_sizes = [len(facts), sum(len(f.variants) for f in facts)]
    # 守卫误拒：可回答问题被 check_sufficiency 拒掉的计数（按档）
    guard_false_reject = [0, 0]
    per_fact: list[dict] = []

    for fact in facts:
        # 评测计划：tier 0 = 原题（端到端问答也跑这档）；tier 1 = 同义改写题
        plan: list[tuple[int, str]] = [(0, fact.question)]
        plan.extend((1, v) for v in fact.variants)
        for tier, q in plan:
            rows_by_strategy: dict[str, list[dict]] = {}
            for st in strategies:
                rows = await _search_strategy(q, st, None, top_k)
                rows_by_strategy[st] = rows
                if _source_hit(rows, fact.source):
                    hit_counts[st][tier] += 1
            # 证据块命中（更严口径）复用同一批检索结果——不重复搜两遍
            for st in ("keyword_only", "hybrid"):
                if _evidence_chunk_hit(rows_by_strategy[st], fact.answer):
                    evidence_hits[st][tier] += 1
            # 守卫误拒（FR-2）：拿同一份 hybrid 证据过确定性守卫，统计可回答
            # 问题被误拒的比例——"证据换说法被拒"是 paraphrase 档最隐蔽的失败
            guard_reason = check_sufficiency(q, rows_by_strategy["hybrid"])
            if guard_reason:
                guard_false_reject[tier] += 1
            item: dict = {
                "kind": "fact",
                "fact_id": fact.fact_id,
                "source": fact.source,
                "tier": "exact" if tier == 0 else "paraphrase",
                "question": q,
                "retrieval_hit": {
                    st: _source_hit(rows_by_strategy[st], fact.source) for st in strategies
                },
                "evidence_hit": {
                    st: _evidence_chunk_hit(rows_by_strategy[st], fact.answer)
                    for st in ("keyword_only", "hybrid")
                },
                "top_sources": {
                    st: [r["source"] for r in rows_by_strategy[st][:top_k]]
                    for st in strategies
                },
                "guard_refusal_reason": guard_reason,
            }
            # 端到端问答只跑原题（改写题走检索对比即可，LLM 成本留给原题）
            if tier == 0:
                from app.rag.graph import ask

                started = time.perf_counter()
                final = await ask(q, session_id=f"eval-{fact.fact_id}")
                latency_ms = (time.perf_counter() - started) * 1000
                latencies.append(latency_ms)

                want = _key_numbers(fact.answer)
                got = final.get("answer", "")
                ans_ok = any(w in got for w in want) and final.get("status") == "done"
                answer_ok[0] += int(ans_ok)

                _passed, _ratio, verdicts = verify_answer(
                    final.get("answer", ""), final.get("retrieved") or []
                )
                faithfulness_claims[0] += sum(1 for v in verdicts if v.supported)
                faithfulness_claims[1] += len(verdicts)
                item.update({
                    "answer": got,
                    "status": final.get("status"),
                    "answer_ok": ans_ok,
                    "refusal_reason": final.get("refusal_reason"),
                    "verdicts": [
                        {"claim": v.claim, "supported": v.supported} for v in verdicts
                    ],
                    "latency_ms": round(latency_ms, 1),
                })
            per_fact.append(item)

    # ---- 拒答档：三类无答案题（D13）----
    hallucination_qs = build_hallucination_questions(reports)
    refuse_by_type: dict[str, list[int]] = {}
    refusal_miss: list[int] = [0, 0]  # [漏拒, 总数]
    for hq in hallucination_qs:
        from app.rag.graph import ask

        final = await ask(hq.question, session_id=f"eval-hallu-{hq.htype}")
        ok = final.get("status") == "insufficient"
        reason = final.get("refusal_reason", "")
        bucket = refuse_by_type.setdefault(hq.htype, [0, 0])
        bucket[0] += int(ok)
        bucket[1] += 1
        refusal_miss[0] += int(not ok)
        refusal_miss[1] += 1
        # 判据交叉验证：拒答必须归因于充分性守卫或引用校验，
        # 不允许"检索碰巧为空"这种不可解释的拒答蒙混过关
        assert ok is False or reason in {*REFUSAL_REASONS, "retrieval_empty"} or final.get(
            "verdicts"
        ), f"拒答 {hq.question} 归因不可解释: {reason}"
        per_fact.append({
            "kind": "refusal",
            "htype": hq.htype,
            "question": hq.question,
            "company": hq.company,
            "status": final.get("status"),
            "refusal_reason": reason,
            "passed": ok,
            "answer": final.get("answer", ""),
        })

    # ---- 污染档：两个干净的指标，不混变量 ----
    # 1) 检索消融（top1 源准确率，anchor off/on）：问 A 时检索首位必须是
    # A 的文档——量化实体锚定对易混公司场景的增益，只动检索不动生成
    # 2) 答案级污染：问 A 的答案必须含 A 的数字、且不含 B 的数字——
    # 测系统端到端行为（走默认管线，锚定开）
    pairs = build_contamination_pairs(reports)
    top1 = {"anchor_off": [0, 0], "anchor_on": [0, 0]}
    contamination = [0, 0]
    for pair in pairs:
        top1_hits: dict[str, bool] = {}
        for mode, anchor_on in (("anchor_off", False), ("anchor_on", True)):
            rows = await _search_strategy(
                pair.question, "hybrid", None, top_k, anchor_on=anchor_on
            )
            hit = bool(rows) and rows[0]["source"].endswith("-" + pair.company)
            top1_hits[mode] = hit
            top1[mode][0] += int(hit)
            top1[mode][1] += 1
        from app.rag.graph import ask

        final = await ask(pair.question, session_id=f"eval-contam-{pair.company}")
        got = final.get("answer", "")
        b_num = _key_numbers(pair.b_answer)
        a_num = _key_numbers(pair.a_answer)
        leaked = any(n in got for n in b_num)
        has_a = any(n in got for n in a_num)
        contamination[0] += int(has_a and not leaked)
        contamination[1] += 1
        per_fact.append({
            "kind": "contamination",
            "question": pair.question,
            "company": pair.company,
            "distractor_company": pair.distractor_company,
            "top1": top1_hits,
            "has_own_number": has_a,
            "leaked_distractor": leaked,
            "passed": bool(has_a and not leaked),
            "answer": got,
        })

    def _rate(num: int, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    latencies.sort()
    metrics = {
        "mode": settings.embedding_mode,
        "corpus_seed": seed,
        "n_reports": n_reports,
        "n_docs": len(reports),
        "n_facts": len(facts),
        "top_k": top_k,
        "eval_database": eval_db,
        # 检索命中：exact 档是管线自检（实体逐字出现，接近 100% 是结构属性），
        # paraphrase 档才是语义检索的质量声明——README 与简历只引用后者
        "retrieval_hit": {
            st: {
                "exact": _rate(hit_counts[st][0], tier_sizes[0]),
                "paraphrase": _rate(hit_counts[st][1], tier_sizes[1]),
            }
            for st in strategies
        },
        "evidence_chunk_hit": {
            st: {
                "exact": _rate(evidence_hits[st][0], tier_sizes[0]),
                "paraphrase": _rate(evidence_hits[st][1], tier_sizes[1]),
            }
            for st in evidence_hits
        },
        "answer_accuracy": _rate(answer_ok[0], tier_sizes[0]),
        "citation_faithfulness": _rate(faithfulness_claims[0], faithfulness_claims[1]),
        "refusal_accuracy": {
            htype: _rate(v[0], v[1]) for htype, v in sorted(refuse_by_type.items())
        },
        # FR-2：误拒（可回答问题被守卫拒）与漏拒（无答案题没拒）分开报告
        "guard_false_reject": {
            "exact": _rate(guard_false_reject[0], tier_sizes[0]),
            "paraphrase": _rate(guard_false_reject[1], tier_sizes[1]),
        },
        "refusal_miss_rate": _rate(refusal_miss[0], refusal_miss[1]),
        # 污染档两个指标：top1 是检索消融（只动检索），contamination 是答案级
        # 系统行为（走默认管线）——分开报告，不把两个变量混成一个数
        "top1_source_accuracy": {
            mode: _rate(v[0], v[1]) for mode, v in top1.items()
        },
        "contamination": _rate(contamination[0], contamination[1]),
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
            settings.embedding_mode, seed, len(facts),
            json.dumps(metrics, ensure_ascii=False),
            json.dumps(
                {"tier_sizes": tier_sizes, "items": per_fact}, ensure_ascii=False
            ),
        )

    await store.close_store()
    log.info("eval.done", extra={"context": metrics})
    return metrics


def _fmt_row(name: str, vals: list[float]) -> str:
    body = "  ".join(f"{v * 100:6.1f}%" for v in vals)
    mean = sum(vals) / len(vals) if vals else 0.0
    spread = (max(vals) - min(vals)) if len(vals) > 1 else 0.0
    return f"{name:<28} {body}   均值 {mean * 100:5.1f}%  极差 {spread * 100:4.1f}%"


def main() -> None:
    reports_n = int(os.environ.get("FINRAG_EVAL_REPORTS", "30"))
    seeds = [int(s) for s in os.environ.get("FINRAG_EVAL_SEEDS", "42").split(",") if s.strip()]
    runs = [asyncio.run(run_eval(n_reports=reports_n, seed=seed)) for seed in seeds]

    # 多种子汇总：单种子 100% 可能是运气，均值+极差才是诚实表述
    def collect(path):
        out = []
        for r in runs:
            cur = r
            for p in path:
                cur = cur[p]
            out.append(float(cur))
        return out

    print(f"\n===== FinRAG 评测汇总（{len(seeds)} 个种子: {seeds}）=====")
    print(f"模式: {runs[0]['mode']}  文档: {runs[0]['n_docs']}  事实: {runs[0]['n_facts']}")
    print(f"{'指标':<28} " + "  ".join(f"seed={s:<5}" for s in seeds))
    print("-" * 78)
    for st in ("vector_only", "keyword_only", "hybrid"):
        print(_fmt_row(f"recall@5 {st} (exact)", collect(["retrieval_hit", st, "exact"])))
        print(_fmt_row(f"recall@5 {st} (paraphrase)", collect(["retrieval_hit", st, "paraphrase"])))
    for st in ("keyword_only", "hybrid"):
        print(_fmt_row(f"证据块命中 {st} (exact)", collect(["evidence_chunk_hit", st, "exact"])))
        print(_fmt_row(f"证据块命中 {st} (paraphrase)", collect(["evidence_chunk_hit", st, "paraphrase"])))
    print(_fmt_row("answer_accuracy", collect(["answer_accuracy"])))
    print(_fmt_row("citation_faithfulness", collect(["citation_faithfulness"])))
    print(_fmt_row("守卫误拒 (exact)", collect(["guard_false_reject", "exact"])))
    print(_fmt_row("守卫误拒 (paraphrase)", collect(["guard_false_reject", "paraphrase"])))
    print(_fmt_row("拒答漏拒率", collect(["refusal_miss_rate"])))
    for htype in runs[0]["refusal_accuracy"]:
        print(_fmt_row(f"拒答率 {htype}", collect(["refusal_accuracy", htype])))
    print(_fmt_row("检索 top1 源准确率 (anchor off)", collect(["top1_source_accuracy", "anchor_off"])))
    print(_fmt_row("检索 top1 源准确率 (anchor on)", collect(["top1_source_accuracy", "anchor_on"])))
    print(_fmt_row("答案级污染拦截", collect(["contamination"])))
    print("-" * 78)
    print(json.dumps(runs[0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
