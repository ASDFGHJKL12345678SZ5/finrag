# RW-6 按路径拆分的真实压测（2026-10-25）

## 问题

旧报告只给混合 P95：真实模式 c10 P95 1.9s、c20 P95 1.1s，**并发更高反而更低**。
怀疑混合了零 LLM 路径（拒答/聚合）——c20 抽到更多快请求，把整体 P95 拉低。

## 改法

`scripts/load_test.py` 按响应分类：

- `refused`：`status=insufficient`（守卫/年份/指标拒答，零 LLM 生成）
- `aggregate`：trace 含 `aggregate` 节点（SQL argparse，零 LLM 生成）
- `llm_generate`：其余（真实 LLM 生成）
- `error`

分别统计 n / P50 / P95 / 成功数，不再只看混合值。

## 结果（真实 LLM，50 请求/档，20 个问题轮询）

**首轮（冷启动）**：

| 并发 | QPS | 混合 P50 | 混合 P95 | llm_generate n / P95 | refused n / P95 | 错误率 |
|---|---|---|---|---|---|---|
| 10 | 10.0 | 805.6ms | 1712.0ms | 45 / 1712.0ms | 5 / 943.3ms | 0% |
| 20 | 22.2 | 743.6ms | **995.9ms** | 45 / 993.3ms | 5 / 995.9ms | 0% |

**预热后复跑**（同一服务，先跑一轮 50 请求）：

| 并发 | QPS | 混合 P50 | 混合 P95 | llm_generate n / P95 | refused n / P95 | 错误率 |
|---|---|---|---|---|---|---|
| 10 | 13.4 | 670.9ms | **939.4ms** | 46 / 933.8ms | 4 / 939.4ms | 0% |
| 20 | 19.2 | 779.1ms | **1145.7ms** | 44 / 1145.7ms | 6 / 919.6ms | 0% |

## 解释

- 首轮 c20 低于 c10 **不是路径混合**：两档 `llm_generate` 都是 45/50，比例相同；
  差异来自**冷启动**（首个 c10 批次含 DB 连接池/embedding/HNSW/LLM 连接预热，
  尾部被冷请求拉长）。预热后 c20 P95 1145.7ms > c10 939.4ms，符合并发升高预期。
- 按路径看，`refused` 明显更快（P50 33–710ms，取决于是否命中缓存/守卫），
  但它只占 8–12%，不足以解释首轮 716ms 的 P95 差。
- 结论：README/简历的延迟数字只引用 `llm_generate` 路径（或明确标注“混合”），
  不再把 c20 的低 P95 当作容量证据。

## 复现

```bash
python -m app.main   # 另开：LLM_MODE=real EMBEDDING_MODE=local RATE_LIMIT_PER_MIN=10000
FINRAG_LOAD_LEVELS=10,20 FINRAG_LOAD_TOTAL=50 python scripts/load_test.py
```

原始输出：`_tmp/load_rw6.log`（冷）、`_tmp/load_rw6_warm.log`（预热）。
