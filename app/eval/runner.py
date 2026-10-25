"""评测 runner（FR-1 难基准）：800 题 × 多检索策略 + 端到端 + 独立评测库。

评测跑在独立库 `finrag_eval`（`app/eval/db.py`），不再 TRUNCATE 开发库。
题库由 `corpus.build_benchmark` 从 FactRegistry 同源派生：
    exact 300 / abbr 300 / calculation 100 / refusal 100

检索策略（FR-6）：vector_only / keyword_only / hybrid(min-max) / rrf /
rrf_rerank（本地 bge-reranker-base，模型缺失自动降级）。指标按题档报告
recall@5、证据块命中、MRR；端到端按题档报告 answer_accuracy 与拒答率，
守卫误拒与漏拒分开报告。逐题明细进 `per_fact.items`，可下钻到每条 miss。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

os.environ.setdefault("EMBEDDING_MODE", "mock")

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.winloop import ensure_selector_loop
from app.eval.db import ensure_eval_database
from app.eval.holdout import (
    HOLDOUT_CORPUS_SEED,
    HOLDOUT_V2_CORPUS_SEED,
    build_holdout,
    build_holdout_v2,
)
from app.eval.schema import EVAL_SCHEMA_SQL
from app.rag import store
from app.rag.corpus import build_benchmark, build_corpus
from app.rag.ingest import embed_query_async, ingest_report
from app.rag.retrieve import detect_anchor, fuse, keyword_weights, rrf_fuse
from app.rag.sufficiency import check_sufficiency
from app.rag.verify import _parse_numbers, _same_number, verify_answer

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

log = get_logger("eval")

ANSWER_CATEGORIES = ("exact", "abbr", "calculation")
ALL_CATEGORIES = (*ANSWER_CATEGORIES, "refusal")
DEFAULT_STRATEGIES = ("vector_only", "keyword_only", "hybrid", "rrf")
NUM_RE = re.compile(r"\d")


async def init_eval_store() -> None:
    pool = store.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(EVAL_SCHEMA_SQL)


async def seed_eval_corpus(
    n_companies: int, years: tuple[int, ...], seed: int
) -> tuple[list, str]:
    """在独立评测库灌语料，返回 (reports, eval_db)。"""
    settings = get_settings()
    eval_dsn = settings.resolved_eval_dsn
    eval_db = await ensure_eval_database(settings.pg_dsn, eval_dsn)
    await store.close_store()
    await store.init_store(dsn=eval_dsn)
    await init_eval_store()
    pool = store.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE rag.documents CASCADE")
    await store.reset_lexical_index()
    reports = build_corpus(n_companies=n_companies, years=years, seed=seed)
    for report in reports:
        await ingest_report(report)
    return reports, eval_db


def answer_matches(expected: str | None, got: str) -> bool:
    """答案判定：有数字按边界/单位比较；无数字（如公司名）按包含匹配。"""
    if not expected:
        return False
    expected_numbers = _parse_numbers(expected)
    if expected_numbers:
        got_numbers = _parse_numbers(got)
        return all(
            any(_same_number(value, kind, gv, gk) for _gr, gv, gk in got_numbers)
            for _raw, value, kind in expected_numbers
        )
    return expected.strip() in got


def evidence_hit(rows: list[dict], terms: list[str]) -> bool:
    """证据命中：top-k 里存在一块包含**证据值词**（带数字的词）。

    公司简称/指标词通常分散在不同块（财务段没有公司名），把全部词都要求
    在一块里会让所有题都 miss；检索质量用 source 命中判，证据命中只验证
    "答案数字有没有出现在被检索到的块里"。
    """
    if not rows or not terms:
        return False
    value_terms = [t for t in terms if NUM_RE.search(t)] or terms
    return any(all(term in row["text"] for term in value_terms) for row in rows)


def source_rank(rows: list[dict], source: str | None) -> int | None:
    if not source:
        return None
    for i, row in enumerate(rows, 1):
        if row["source"] == source:
            return i
    return None


async def candidate_rows(question: str, top_k: int) -> tuple[list[dict], list[dict], str | None]:
    qv = await embed_query_async(question)
    vector_rows = await store.vector_search(qv, top_k=top_k)
    weights = await keyword_weights(question)
    keyword_rows = await store.keyword_search(
        question, top_k=max(4, top_k), weights=weights
    )
    anchor = detect_anchor(question, await store.company_names())
    return vector_rows, keyword_rows, anchor


def rank_with_strategy(
    question: str,
    strategy: str,
    vector_rows: list[dict],
    keyword_rows: list[dict],
    anchor: str | None,
    top_k: int,
) -> list[dict]:
    settings = get_settings()
    if strategy == "vector_only":
        return vector_rows[:top_k]
    if strategy == "keyword_only":
        return keyword_rows[:top_k]
    # mock 模式向量是哈希噪声：融合权重归零走纯关键词（与生产 hybrid_search 一致）
    vw = 0.0 if settings.embedding_mode == "mock" else settings.vector_weight
    kw = 1.0 if settings.embedding_mode == "mock" else settings.keyword_weight
    if strategy == "hybrid":
        return fuse(vector_rows, keyword_rows, vector_weight=vw, keyword_weight=kw,
                    anchor=anchor)[:top_k]
    rrf = rrf_fuse(vector_rows, keyword_rows, vector_weight=vw, keyword_weight=kw,
                   anchor=anchor)
    if strategy == "rrf_rerank":
        from app.rag.rerank import rerank

        return rerank(question, rrf, top_n=top_k)
    return rrf[:top_k]


async def _ask(question: str, session_id: str, timeout_s: float) -> dict:
    from app.rag.graph import ask

    try:
        async with asyncio.timeout(timeout_s):
            return await ask(question, session_id=session_id)
    except TimeoutError:
        return {"status": "failed", "error": f"单题超时（>{int(timeout_s)}s）"}
    except Exception as e:  # noqa: BLE001 - runner 不因单题崩溃
        return {"status": "failed", "error": str(e)[:200]}


def _rate(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


async def _oracle_chunks(source: str | None, cache: dict[str, list[dict]]) -> list[dict]:
    """期望源的全文块（守卫误拒率的判据）。

    守卫误拒率要回答的是"证据充分时会不会被误拒"，不是"检索没捞到时会不会
    拒答"——后者是检索指标。用 gold source 的全部块做 oracle，才能把守卫
    本身的严格度与检索质量解耦。
    """
    if not source:
        return []
    if source in cache:
        return cache[source]
    pool = store.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT source, section_path, chunk_index, kind, text"
            " FROM rag.chunks WHERE source = $1 ORDER BY chunk_index",
            source,
        )
    cache[source] = [dict(r) for r in rows]
    return cache[source]


async def run_eval(
    n_companies: int = 200,
    years: tuple[int, ...] = (2021, 2022, 2023, 2024, 2025),
    seed: int = 42,
    strategies: tuple[str, ...] = DEFAULT_STRATEGIES,
    n_exact: int = 300,
    n_abbr: int = 300,
    n_calc: int = 100,
    n_refusal: int = 100,
    limit: int = 0,
    end2end_limit: int = 0,
    timeout_s: float = 120.0,
    holdout: int = 0,
) -> dict:
    settings = get_settings()
    t_seed = time.perf_counter()
    corpus_seed = {
        0: seed,
        1: HOLDOUT_CORPUS_SEED,
        2: HOLDOUT_V2_CORPUS_SEED,
    }[holdout]
    reports, eval_db = await seed_eval_corpus(n_companies, years, corpus_seed)
    seed_s = time.perf_counter() - t_seed
    n_chunks, _avg_len = await store.corpus_stats()
    if holdout == 2:
        benchmark = build_holdout_v2(reports)
    elif holdout == 1:
        benchmark = build_holdout(reports)
    else:
        benchmark = build_benchmark(
            reports, seed=seed,
            n_exact=n_exact, n_abbr=n_abbr, n_calc=n_calc, n_refusal=n_refusal,
        )
    if limit:
        benchmark = benchmark[:limit]

    # 端到端抽样：按题档分层取，避免"只抽到 exact"造成分档指标失真
    end2end_selected: set[int] | None = None
    if end2end_limit > 0:
        pools: dict[str, list[int]] = defaultdict(list)
        for i, item in enumerate(benchmark):
            if item.category in ANSWER_CATEGORIES:
                pools[item.category].append(i)
        per_cat = max(1, end2end_limit // len(ANSWER_CATEGORIES))
        end2end_selected = set()
        # 先打散：题库按子类顺序排列（yoy→yoy_diff→share→industry_max），
        # 直接取前 N 会只抽到 yoy，200 题样本看起来虚高（FR-9 实测踩坑）
        import random as _random

        pick_rng = _random.Random(f"end2end:{seed}")
        for cat, idxs in pools.items():
            shuffled = list(idxs)
            pick_rng.shuffle(shuffled)
            end2end_selected.update(shuffled[:per_cat])

    # 计数容器：retrieval[strategy][category] = {n, source_hits, evidence_hits, mrr}
    retrieval: dict[str, dict[str, dict[str, float]]] = {
        st: {cat: {"n": 0, "source": 0, "evidence": 0, "mrr": 0.0} for cat in ALL_CATEGORIES}
        for st in strategies
    }
    retrieval_sub: dict[str, dict[str, dict[str, float]]] = {
        st: defaultdict(lambda: {"n": 0, "source": 0, "evidence": 0, "mrr": 0.0})
        for st in strategies
    }
    answer: dict[str, dict[str, int]] = {
        cat: {"n": 0, "ok": 0} for cat in ANSWER_CATEGORIES
    }
    refusal: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "ok": 0})
    guard: dict[str, dict[str, int]] = {
        cat: {"n": 0, "refused": 0} for cat in ANSWER_CATEGORIES
    }
    faith = {"supported": 0, "total": 0}
    pre_faith: list[float] = []
    repaired = 0
    oracle_cache: dict[str, list[dict]] = {}
    latencies: list[float] = []
    records: list[dict] = []
    end2end_used = 0

    for idx, q in enumerate(benchmark, 1):
        record: dict[str, Any] = {
            "qid": q.qid,
            "category": q.category,
            "subtype": q.subtype,
            "question": q.question,
            "source": q.source,
            "expected_answer": q.answer,
            "expected": q.expected,
            "retrieval": {},
        }
        if q.category in ANSWER_CATEGORIES:
            v, k, anchor = await candidate_rows(q.question, top_k=5)
            for st in strategies:
                rows = rank_with_strategy(q.question, st, v, k, anchor, 5)
                rank = source_rank(rows, q.source)
                hit = rank is not None
                ev = evidence_hit(rows, q.evidence_terms)
                cell = retrieval[st][q.category]
                cell["n"] += 1
                cell["source"] += int(hit)
                cell["evidence"] += int(ev)
                cell["mrr"] += (1.0 / rank) if rank else 0.0
                sub = q.subtype or q.category
                sub_cell = retrieval_sub[st][sub]
                sub_cell["n"] += 1
                sub_cell["source"] += int(hit)
                sub_cell["evidence"] += int(ev)
                sub_cell["mrr"] += (1.0 / rank) if rank else 0.0
                record["retrieval"][st] = {
                    "rank": rank, "source_hit": hit, "evidence_hit": ev,
                    "top_sources": [r["source"] for r in rows[:5]],
                }
            reason = check_sufficiency(q.question, await _oracle_chunks(q.source, oracle_cache))
            guard[q.category]["n"] += 1
            guard[q.category]["refused"] += int(bool(reason))
            record["guard_refusal_reason"] = reason

            run_end2end = (
                end2end_limit == 0
                or (end2end_limit > 0 and end2end_selected is not None and idx - 1 in end2end_selected)
            )
            if run_end2end:
                end2end_used += 1
                t0 = time.perf_counter()
                final = await _ask(q.question, q.qid, timeout_s)
                latency = (time.perf_counter() - t0) * 1000
                latencies.append(latency)
                got = final.get("answer", "")
                ok = final.get("status") == "done" and answer_matches(q.answer, got)
                answer[q.category]["n"] += 1
                answer[q.category]["ok"] += int(ok)
                _passed, _ratio, verdicts = verify_answer(
                    got, final.get("retrieved") or []
                )
                faith["supported"] += sum(1 for v in verdicts if v.supported)
                faith["total"] += len(verdicts)
                verify_step = next(
                    (t for t in final.get("trace") or []
                     if t.get("node") == "verify"), {}
                )
                detail = verify_step.get("detail") or {}
                if "pre_ratio" in detail:
                    pre_faith.append(float(detail["pre_ratio"]))
                if detail.get("citation_repaired"):
                    repaired += 1
                record.update({
                    "answer": got,
                    "status": final.get("status"),
                    "answer_ok": ok,
                    "citation_pre_ratio": detail.get("pre_ratio"),
                    "citation_repaired": bool(detail.get("citation_repaired")),
                    "refusal_reason": final.get("refusal_reason"),
                    "verdicts": [
                        {"claim": v.claim, "supported": v.supported} for v in verdicts
                    ],
                    "latency_ms": round(latency, 1),
                })
            else:
                record.update({"answer": None, "status": "skipped", "answer_ok": None})
        else:
            t0 = time.perf_counter()
            final = await _ask(q.question, q.qid, timeout_s)
            latency = (time.perf_counter() - t0) * 1000
            latencies.append(latency)
            ok = final.get("status") == "insufficient"
            refusal[q.subtype]["n"] += 1
            refusal[q.subtype]["ok"] += int(ok)
            record.update({
                "answer": final.get("answer", ""),
                "status": final.get("status"),
                "refusal_reason": final.get("refusal_reason"),
                "refused": ok,
                "latency_ms": round(latency, 1),
            })
        records.append(record)
        if idx % 50 == 0:
            print(f"  ... {idx}/{len(benchmark)}", flush=True)

    retrieval_metrics: dict[str, dict[str, dict[str, float]]] = {}
    for st in strategies:
        retrieval_metrics[st] = {}
        for cat in ALL_CATEGORIES:
            cell = retrieval[st][cat]
            n = int(cell["n"])
            retrieval_metrics[st][cat] = {
                "n": n,
                "recall@5": _rate(int(cell["source"]), n),
                "evidence_hit": _rate(int(cell["evidence"]), n),
                "mrr": round(cell["mrr"] / n, 4) if n else 0.0,
            }

    # 按子类（表格题 segment_share / 行业极值等）单独报，FR-6 要求
    # "精确/简称/计算/表格"分档看得见
    retrieval_sub_metrics: dict[str, dict[str, dict[str, float]]] = {}
    for st in strategies:
        retrieval_sub_metrics[st] = {}
        for sub, cell in sorted(retrieval_sub[st].items()):
            n = int(cell["n"])
            retrieval_sub_metrics[st][sub] = {
                "n": n,
                "recall@5": _rate(int(cell["source"]), n),
                "evidence_hit": _rate(int(cell["evidence"]), n),
                "mrr": round(cell["mrr"] / n, 4) if n else 0.0,
            }

    answerable_n = sum(v["n"] for v in answer.values())
    answerable_ok = sum(v["ok"] for v in answer.values())
    refusal_n = sum(v["n"] for v in refusal.values())
    refusal_ok = sum(v["ok"] for v in refusal.values())
    total_eval = answerable_n + refusal_n
    overall = (
        _rate(answerable_ok + refusal_ok, total_eval)
        if answerable_n > 0 and refusal_n > 0 else None
    )

    guard_false_reject = {
        cat: _rate(guard[cat]["refused"], guard[cat]["n"]) for cat in ANSWER_CATEGORIES
    }
    refusal_accuracy = {
        subtype: _rate(v["ok"], v["n"]) for subtype, v in sorted(refusal.items())
    }
    refusal_miss = 1.0 - (refusal_ok / refusal_n) if refusal_n else 0.0

    latencies.sort()
    metrics: dict[str, Any] = {
        "eval_database": eval_db,
        "mode": settings.embedding_mode,
        "llm_mode": settings.llm_mode,
        "split": "train" if not holdout else f"holdout_v{holdout}",
        "corpus_seed": corpus_seed,
        "n_companies": n_companies,
        "years": list(years),
        "n_reports": len(reports),
        "n_chunks": n_chunks,
        "n_questions": len(benchmark),
        "end2end_sample": end2end_used,
        "seed_time_s": round(seed_s, 1),
        "retrieval": retrieval_metrics,
        "retrieval_by_subtype": retrieval_sub_metrics,
        "answer_accuracy": {
            cat: (_rate(v["ok"], v["n"]) if v["n"] else None)
            for cat, v in answer.items()
        },
        "answer_accuracy_overall": _rate(answerable_ok, answerable_n),
        "refusal_accuracy": refusal_accuracy,
        "refusal_miss_rate": round(refusal_miss, 4),
        "overall_accuracy": overall,
        "guard_false_reject": guard_false_reject,
        "citation_faithfulness": _rate(faith["supported"], faith["total"]),
        "citation_faithfulness_before": (
            round(sum(pre_faith) / len(pre_faith), 4) if pre_faith else 0.0
        ),
        "citation_repair_rate": _rate(repaired, answerable_n),
        "latency_ms": {
            "p50": round(statistics.median(latencies), 1) if latencies else 0.0,
            "p95": round(latencies[min(int(len(latencies) * 0.95), len(latencies) - 1)], 1)
            if latencies else 0.0,
        },
        "evaluated_at": datetime.now(UTC).isoformat(),
    }

    pool = store.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO eval.runs (mode, corpus_seed, n_facts, metrics, per_fact)"
            " VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)",
            settings.embedding_mode, seed, sum(len(r.facts) for r in reports),
            json.dumps(metrics, ensure_ascii=False),
            json.dumps({"n_reports": len(reports), "items": records}, ensure_ascii=False),
        )
    await store.close_store()
    log.info("eval.done", extra={"context": {"overall": overall, "n": len(benchmark)}})
    return metrics


# ---------------------------------------------------------------- 报告输出


def _collect(runs: list[dict], path: tuple[str, ...]) -> list[float]:
    vals: list[float] = []
    for run in runs:
        cur: Any = run
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                cur = None
                break
            cur = cur[key]
        if isinstance(cur, (int, float)):
            vals.append(float(cur))
    return vals


def _fmt_row(label: str, vals: list[float], seeds: list[int]) -> str:
    if not vals:
        return f"{label:<34} " + "  ".join("-" for _ in seeds)
    mean = sum(vals) / len(vals)
    spread = max(vals) - min(vals)
    cells = "  ".join(f"{v:>6.1%}" for v in vals)
    if len(vals) == 1:
        return f"{label:<34} {cells}"
    return f"{label:<34} {cells}  均值 {mean:>6.1%}  极差 {spread:>5.1%}"


def _fmt_raw(label: str, vals: list[float], seeds: list[int], unit: str = "") -> str:
    if not vals:
        return f"{label:<34} " + "  ".join("-" for _ in seeds)
    cells = "  ".join(f"{v:>7.3f}{unit}" for v in vals)
    if len(vals) == 1:
        return f"{label:<34} {cells}"
    mean = sum(vals) / len(vals)
    return f"{label:<34} {cells}  均值 {mean:.3f}{unit}"


def _mrr_all(run: dict, strategy: str) -> float:
    total = 0
    acc = 0.0
    for cat in ANSWER_CATEGORIES:
        cell = run["retrieval"][strategy][cat]
        total += cell["n"]
        acc += cell["mrr"] * cell["n"]
    return acc / total if total else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="FinRAG 难基准评测")
    parser.add_argument("--companies", type=int, default=200)
    parser.add_argument("--years", default="2021,2022,2023,2024,2025")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--seeds", default="", help="多种子逗号分隔（覆盖 --seed）")
    parser.add_argument("--strategies", default=",".join(DEFAULT_STRATEGIES))
    parser.add_argument("--exact", type=int, default=300)
    parser.add_argument("--abbr", type=int, default=300)
    parser.add_argument("--calc", type=int, default=100)
    parser.add_argument("--refusal", type=int, default=100)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--end2end", type=int, default=0,
                        help="端到端最多跑多少道可答题（0=全跑）")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--holdout", type=int, nargs="?", const=1, default=0,
                        choices=(0, 1, 2),
                        help="留出集版本：--holdout/1=v1（20261026），2=v2（20261111）")
    args = parser.parse_args()

    years = tuple(int(y) for y in args.years.split(",") if y.strip())
    seeds = (
        [int(s) for s in args.seeds.split(",") if s.strip()]
        if args.seeds.strip()
        else [args.seed]
    )
    strategies = tuple(s.strip() for s in args.strategies.split(",") if s.strip())
    defaults = {"n_companies": args.companies, "years": years, "strategies": strategies,
                "n_exact": args.exact, "n_abbr": args.abbr, "n_calc": args.calc,
                "n_refusal": args.refusal, "limit": args.limit,
                "end2end_limit": args.end2end, "timeout_s": args.timeout,
                "holdout": args.holdout}

    runs: list[dict] = []
    for seed in seeds:
        print(f"\n===== seed={seed} =====", flush=True)
        metrics = asyncio.run(run_eval(seed=seed, **defaults))
        runs.append(metrics)
        print(
            f"  文档 {metrics['n_reports']} / 块 {metrics['n_chunks']} / "
            f"题 {metrics['n_questions']} / 建库 {metrics['seed_time_s']}s"
        )

    print(f"\n===== FinRAG 难基准评测（{len(seeds)} 个种子: {seeds}）=====")
    print(f"模式: {runs[0]['mode']}  公司: {runs[0]['n_companies']} × "
          f"年份 {runs[0]['years']}  文档 {runs[0]['n_reports']}  块 {runs[0]['n_chunks']}")
    print(f"{'指标':<34} " + "  ".join(f"seed={s:<5}" for s in seeds))
    print("-" * 78)
    for st in strategies:
        for cat in ANSWER_CATEGORIES:
            print(_fmt_row(f"recall@5 {st} ({cat})",
                           _collect(runs, ("retrieval", st, cat, "recall@5")), seeds))
        print(_fmt_raw(f"mrr {st} (all answerable)",
                       [_mrr_all(r, st) for r in runs], seeds))
        print(_fmt_row(f"表格题 recall {st} (segment_share)",
                       _collect(runs, ("retrieval_by_subtype", st, "segment_share", "recall@5")), seeds))
        print(_fmt_raw(f"表格题 MRR {st}",
                       _collect(runs, ("retrieval_by_subtype", st, "segment_share", "mrr")), seeds))
    for cat in ANSWER_CATEGORIES:
        print(_fmt_row(f"answer_accuracy ({cat})",
                       _collect(runs, ("answer_accuracy", cat)), seeds))
    print(_fmt_row("answer_accuracy (overall)",
                   _collect(runs, ("answer_accuracy_overall",)), seeds))
    for subtype in runs[0]["refusal_accuracy"]:
        print(_fmt_row(f"refusal_accuracy ({subtype})",
                       _collect(runs, ("refusal_accuracy", subtype)), seeds))
    print(_fmt_row("refusal_miss_rate", _collect(runs, ("refusal_miss_rate",)), seeds))
    print(_fmt_row("guard_false_reject (exact)",
                   _collect(runs, ("guard_false_reject", "exact")), seeds))
    print(_fmt_row("guard_false_reject (abbr)",
                   _collect(runs, ("guard_false_reject", "abbr")), seeds))
    print(_fmt_row("guard_false_reject (calculation)",
                   _collect(runs, ("guard_false_reject", "calculation")), seeds))
    print(_fmt_row("citation_faithfulness",
                   _collect(runs, ("citation_faithfulness",)), seeds))
    print(_fmt_row("citation_faithfulness_before",
                   _collect(runs, ("citation_faithfulness_before",)), seeds))
    print(_fmt_row("citation_repair_rate",
                   _collect(runs, ("citation_repair_rate",)), seeds))
    print(_fmt_row("overall_accuracy", _collect(runs, ("overall_accuracy",)), seeds))
    print(_fmt_raw("P95 延迟", _collect(runs, ("latency_ms", "p95")), seeds, "ms"))
    print("-" * 78)
    for run in runs:
        print(json.dumps(run, ensure_ascii=False, indent=2)[:1200])


if __name__ == "__main__":
    ensure_selector_loop()
    main()
