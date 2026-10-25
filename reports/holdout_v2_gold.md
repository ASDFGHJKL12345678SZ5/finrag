# FinRAG 留出集 v2 金标执行记录（R2）

- 题目：`app/eval/holdout.py` + `app/eval/holdout_v2_spec.json`（commit `bdef714`，早于本文件）
- 出题：独立隔离会话（只给语料字段格式与题面规格）
- 语料：1000 篇（seed=20261111）
- 题数：200 = {'exact': 80, 'abbr': 60, 'calculation': 20, 'refusal': 40}
- 校验：每道可答题的 answer/evidence 必须出现在该篇研报正文或表格；industry_max 用同源 revenue 重算 argmax；错年份不在语料；指标不是登记事实。
- 错误：0

## 类别/子类

| 类别 | 子类 | 题数 |
|---|---|---|
| abbr | abbr | 60 |
| calculation | industry_max | 6 |
| calculation | segment_share | 6 |
| calculation | yoy | 8 |
| exact | 同比增长 | 14 |
| exact | 归母净利润 | 13 |
| exact | 毛利率 | 13 |
| exact | 研发费用率 | 13 |
| exact | 经营性现金流 | 13 |
| exact | 营业收入 | 14 |
| refusal | boundary_metric | 7 |
| refusal | future_escaped | 1 |
| refusal | future_marked | 9 |
| refusal | shaped_metric | 13 |
| refusal | wrong_year | 10 |

## 逐题明细（见 JSONL）

`reports/holdout_v2_items.jsonl`，200 行。

## 错误

无。
