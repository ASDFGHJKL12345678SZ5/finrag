"""FinRAG 压测：并发打 /ask/sync，量 QPS 与分位延迟。

与 DataCrew 压测同构：httpx 异步并发 + 分位统计 + 失败分类。
不同点：FinRAG 每个请求要走完整状态机（检索+生成+校验），
且 mock 模式下没有 LLM 网络往返——压的是"状态机+检索"这条链路，
真实 LLM 接入后要重新标定（README 会写清这个边界）。
"""
from __future__ import annotations

import asyncio
import os
import statistics
import time

import httpx

BASE = os.environ.get("FINRAG_BASE", "http://127.0.0.1:8001")

# 20 个不同问题轮询：避免同问题命中任何潜在缓存，也让检索分布真实
QUESTIONS = [
    "宁德润能2025年的营业收入是多少？",
    "华曜新能的归母净利润是多少？",
    "青蓝动力的研发费用率是多少？",
    "芯澈微电2025年营收同比增长多少？",
    "康泓生物的毛利率是多少？",
    "禾裕食品的经营性现金流是多少？",
    "恒通银行的资产负债率是多少？",
    "方衡半导体的归母净利润是多少？",
    "晶石科技的营业收入是多少？",
    "博济医疗的研发费用率是多少？",
    "春山乳业2025年营收同比增长多少？",
    "醴泉酒业的归母净利润是多少？",
    "民熙银行的毛利率是多少？",
    "浦泰银行的营业收入是多少？",
    "安平制药的经营性现金流是多少？",
    "恒曜锂能的研发费用率是多少？",
    "晟科新源的归母净利润是多少？",
    "蓝弦能源的营业收入是多少？",
    "璟泰新材2025年营收同比增长多少？",
    "曜景光伏的毛利率是多少？",
]


async def _one(client: httpx.AsyncClient, q: str, sem: asyncio.Semaphore) -> tuple[float, bool]:
    async with sem:
        started = time.perf_counter()
        try:
            r = await client.post(
                BASE + "/ask/sync",
                json={"question": q, "session_id": "loadtest"},
                timeout=60,
            )
            ok = r.status_code == 200 and r.json().get("status") in ("done", "insufficient")
        except Exception:  # noqa: BLE001 - 压测里任何异常都算失败，不能中断整轮
            ok = False
        return (time.perf_counter() - started) * 1000, ok


async def run(concurrency: int, total: int) -> dict:
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        tasks = [
            _one(client, QUESTIONS[i % len(QUESTIONS)], sem) for i in range(total)
        ]
        results = await asyncio.gather(*tasks)
    lat = sorted(r[0] for r in results)
    ok_n = sum(1 for r in results if r[1])
    return {
        "concurrency": concurrency,
        "total": total,
        "ok": ok_n,
        "failed": total - ok_n,
        "qps": round(total / (sum(lat) / 1000 / concurrency), 1) if lat else 0,
        "p50_ms": round(statistics.median(lat), 1),
        "p95_ms": round(lat[int(len(lat) * 0.95)], 1),
        "max_ms": round(lat[-1], 1),
    }


def main() -> None:
    total = int(os.environ.get("FINRAG_LOAD_TOTAL", "100"))
    print(f" FinRAG 压测（base={BASE}, 每档 {total} 请求）")
    for c in (1, 5, 10, 20):
        row = asyncio.run(run(c, total))
        print(
            f"  并发 {row['concurrency']:>2}: QPS {row['qps']:>6} | "
            f"P50 {row['p50_ms']:>7}ms | P95 {row['p95_ms']:>7}ms | "
            f"Max {row['max_ms']:>7}ms | 失败 {row['failed']}"
        )


if __name__ == "__main__":
    main()
