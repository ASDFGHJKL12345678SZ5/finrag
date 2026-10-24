"""FinRAG 压测：并发打 /ask/sync，量 QPS 与分位延迟。

与 DataCrew 压测同构：httpx 异步并发 + 分位统计 + 失败分类。
不同点：FinRAG 每个请求要走完整状态机（检索+生成+校验），
且 mock 模式下没有 LLM 网络往返——压的是"状态机+检索"这条链路，
真实 LLM 接入后要重新标定（README 会写清这个边界）。

FR-5 修正：旧版 QPS 用 `total / (sum(lat)/1000/concurrency)` 计算——这只在
"并发 worker 全程满载且延迟均匀"时近似成立，实际会随分位尾部指数高估。
现在用墙钟：QPS = total / wall_s。档位按方案：mock 1/10/50/100/200，
真实 10/20（可用 FINRAG_LOAD_LEVELS 覆盖）。
"""
from __future__ import annotations

import asyncio
import os
import statistics
import time

import httpx

BASE = os.environ.get("FINRAG_BASE", "http://127.0.0.1:8001")
# FR-8 起 /ask 需要 API Key；压测容量时用 RATE_LIMIT_PER_MIN 放大桶
# （否则测到的是限流器——这与"测流水线"的目标不同，报告里会标注）
API_KEY = os.environ.get("FINRAG_API_KEY", "dev-key-001")

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
                headers={"X-API-Key": API_KEY},
                timeout=120,
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
        started = time.perf_counter()
        results = await asyncio.gather(*tasks)
        wall_s = time.perf_counter() - started
    lat = sorted(r[0] for r in results)
    ok_n = sum(1 for r in results if r[1])
    return {
        "concurrency": concurrency,
        "total": total,
        "ok": ok_n,
        "failed": total - ok_n,
        "error_rate": round((total - ok_n) / total, 4) if total else 0,
        "wall_s": round(wall_s, 2),
        "qps": round(total / wall_s, 1) if wall_s else 0,
        "p50_ms": round(statistics.median(lat), 1),
        "p95_ms": round(lat[min(int(len(lat) * 0.95), len(lat) - 1)], 1),
        "max_ms": round(lat[-1], 1),
    }


def main() -> None:
    total = int(os.environ.get("FINRAG_LOAD_TOTAL", "100"))
    levels = [
        int(x) for x in os.environ.get("FINRAG_LOAD_LEVELS", "1,10,50,100,200").split(",")
        if x.strip()
    ]
    print(f" FinRAG 压测（base={BASE}, 每档 {total} 请求, 并发档 {levels}）")
    for c in levels:
        row = asyncio.run(run(c, total))
        print(
            f"  并发 {row['concurrency']:>3}: QPS {row['qps']:>6} | "
            f"P50 {row['p50_ms']:>8}ms | P95 {row['p95_ms']:>8}ms | "
            f"Max {row['max_ms']:>8}ms | 错误率 {row['error_rate']:.1%} "
            f"| 墙钟 {row['wall_s']}s"
        )


if __name__ == "__main__":
    main()
