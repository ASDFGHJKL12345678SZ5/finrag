# FR-10 跨文档聚合：行业极值不再靠检索撞运气

## 问题

FR-9 真实 800 题里 calculation 的 `industry_max` 只有 **3/20**：问"食品饮料行业
2024 年营业收入最高的公司是哪家？"，单文档 RAG 只能捞到个别公司，模型没有
"全部 40 家"的候选集，argmax 本来就答不了。20 道题把 calculation 从 97%
拖到 77%，是当时最大的单点损失。

## 改法

1. **结构化事实表** `rag.facts(fact_id, source, year, metric, segment, value, unit, answer)`：
   入库时把 `Report.facts` 的数值解析成行（`ingest._fact_rows`），一篇文档重建时
   先删后插；`metric` 保留 canonical（营业收入/归母净利润/…），分业务事实带 segment。
2. **聚合查询** `store.aggregate_facts(metric, year, industry)`：一条 SQL 取
   `segment IS NULL` 的整公司口径事实，按 value 排序取第一。
3. **图节点** `aggregate`：`understand_query` 识别"行业 + 最高/最大 + 年份 + 指标"
   后走条件边 `aggregate`（跳过 retrieve/sufficiency/generate），SQL 取赢家，
   取赢家文档中含答案数字的块做 `c1` 引用，再走原有 `verify` 校验。
   **零 LLM 调用**；拿不到数据判 `insufficient(aggregate_empty)`，不拿别家凑。
4. 识别范围：`新能源/半导体/医药生物/食品饮料/银行` × `营业收入/归母净利润/
   毛利率/研发费用率/经营性现金流/同比增长`。

## 效果（mock 800 题，seed=42）

| 指标 | FR-10 前 | FR-10 后 |
|---|---|---|
| industry_max | 0/20 | **20/20** |
| calculation answer | 79/100 = 79% | **99/100 = 99%** |
| answer（可答档） | 96.1% | **99.0%** |
| overall（含拒答） | 96.6% | **99.1%** |
| P95 | 74ms | 75.8ms |

真实 800 题复跑：industry_max **20/20**，calculation 99/100，总通过 **799/800
= 99.9%**（见 `real_eval_800_after_fr10.md`）。

## 测试（tests/test_aggregate.py，8 条）

- 识别：行业极值正例 / 非极值不误触发（参数化 4 例）
- SQL：8 家公司里取营收最高，answer 与 source 公司正确
- 图路径：把 `get_llm` 换成"一调用就炸"，聚合问题仍 done，trace 无 generate
- 无数据：问一个没有事实的年份 → `insufficient(aggregate_empty)`

## 边界

- 只支持整公司口径；分业务口径的 argmax 不在本轮（segment 事实已入库，
  后续可扩展"某品类收入最高的公司"）。
- 历史库需重灌才会补 `rag.facts`；新库 `init_store` 自动建表。
