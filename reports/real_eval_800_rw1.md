# RW-1 后真实 800 题复跑（文本抽取事实）

- 时间：2026-10-25
- 语料/题库：1000 篇 / 8000 块 / 800 题，seed=42
- 模式：local embedding + DeepSeek，hybrid 检索
- 事实表来源：`app/rag/fact_extract.py` 从正文/表格解析（**不再读 report.facts**）
- 命令：

```bash
LLM_MODE=real EMBEDDING_MODE=local python -m app.eval.runner \
  --companies 200 --strategies hybrid --end2end 0
```

## 结果

| 档 | 通过 |
|---|---|
| exact | 300/300 = 100% |
| abbr | 300/300 = 100% |
| calculation | 99/100 = 99% |
| refusal | 100/100 = 100% |
| **总** | **799/800 = 99.9%** |

calculation 细分：yoy 40/40、yoy_diff 20/20、segment_share 19/20、
industry_max **20/20**（聚合改为读文本抽取的 `rag.facts`，仍全对）。

引用忠实度 99.9%、守卫误拒 0%、P95 909.9ms、建库 118.0s。
抽取 P/R（9000 条事实）：**P=1.000 / R=1.000**。

## 结论

RW-1 关闭：行业极值 20/20 **不再依赖 ground truth 表**。改坏表格单元格的
复现测试证明聚合值随文本变化；AST 不变量测试保证生产路径无 `.facts` /
`all_facts`。最终总分与 FR-10 版一致（99.9%），未掉分。

证据：`real_eval_800_rw1_metrics.json` + `real_eval_800_rw1_items.jsonl`；
抽取与不变量见 `tests/test_fact_extract.py` 与 `reports/fr10_aggregate.md`
“RW-1 返工”节。
