# FinRAG 真实 800 题（Q2–Q4 / v3 修复后，2026-10-25）

- 命令：`LLM_MODE=real EMBEDDING_MODE=local python -m app.eval.runner`
- commit：`7747131`；语料 seed **20260929**；1000 篇 / 8000 块；seed=42
- 结果：**800/800 = 100.0%**
  - exact 300/300、abbr 300/300、calculation 100/100、refusal 100/100
  - guard_false_reject 0；citation_faithfulness 100%（修复率 0）
- 检索（hybrid）：exact/abbr recall@5 99.7%/100%、calculation 79%；
  全档 MRR 0.889；表格题 recall 100%
- 说明：这是**模板语料**上的数字；真实 PDF/HTML 的版面/抽取仍是已知边界。
  RW-5 历史数字 797/800=99.6% 见 `reports/real_eval_800_rw5.md`。
- 逐题指标：`reports/real_eval_800_v3_metrics.json`（eval.runs run 135）。
