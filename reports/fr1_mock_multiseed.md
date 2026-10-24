# FR-1 mock 基准多种子稳定性（seed 42/43/44）

语料与题库同源换 seed（每次重建 1000 篇 / 800 题），hybrid 检索，mock LLM，
每 seed 跑完整 800 题端到端。命令：

```bash
python -m app.eval.runner --companies 200 --seeds 42,43,44 \
  --strategies hybrid --end2end 0
```

| 指标 | seed=42 | seed=43 | seed=44 | 均值 | 极差 |
|---|---|---|---|---|---|
| exact answer | 100% | 100% | 100% | 100% | 0 |
| abbr answer | 98.0% | 98.3% | 96.0% | **97.4%** | **2.3%** |
| calculation answer | 79.0% | 79.0% | 79.0% | 79.0% | 0 |
| answer（可答档） | 96.1% | 96.3% | 95.3% | 95.9% | 1.0% |
| refusal miss | 0% | 0% | 0% | 0% | 0 |
| **overall** | **96.6%** | **96.8%** | **95.9%** | **96.4%** | **0.9%** |
| retrieval exact/abbr/calc recall@5 | 100/100/80 | 100/100/80 | 100/100/80 | 100/100/80 | 0 |
| hybrid MRR | 0.876 | 0.872 | 0.876 | 0.875 | 0.004 |
| P95 | 76.0ms | 79.2ms | 71.3ms | 75.5ms | 7.9ms |

读法：

- **exact / calculation / refusal 对 seed 不敏感**（极差 0）：题库按指标分层抽样，
  计算题的 ground truth 由财务序列直接推导，换 seed 不改变难度结构。
- **abbr 有 2.3% 波动**：同义说法/缩写在 BM25 里的名次对语料文本长度与排序敏感，
  这是唯一需要多种子报区间的档位；97.4% ± 2.3 比单次 98.0% 更诚实。
- 真实 800 题（FR-9）用 seed=42 单次，97.1%；与 mock 3 seed 均值 96.4% 同区间，
  差异主要来自真实模型会做同义换算（abbr 100%）与引用校验更严格
  （calculation 77%）。
