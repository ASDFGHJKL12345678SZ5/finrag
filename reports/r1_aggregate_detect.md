# R1 聚合识别去掉“行业”字面依赖（2026-10-25）

## 问题

`app/rag/aggregate.py` 的识别条件第一行是：

```python
if "行业" not in question:
    return None
```

留出集 v1 的 6 道 industry_max 全问成“银行2022年营收居首的公司是哪家”，
不含“行业”二字，聚合节点不触发，退回单文档 RAG——20 家公司里挑不出 argmax，
v1 calculation 因此只有 8/20。

同时行业词表是本模块手写的 `("新能源", "半导体", "医药生物", "食品饮料", "银行")`，
与语料生成器各维护一份，换行业会漂移；`graph.py` 里还有第三份同样的手写表。

## 改法

1. `aggregate.industry_words()` 只从 `corpus.INDUSTRIES`（即 `Report.industry`
   的取值集合）派生，本模块不再出现行业字面量。
2. `detect_aggregate` 判据改为四项同时命中：**行业词 + 极值词 + 年份 + 指标**；
   “行业”二字降为可选加强词，不再是必要条件。
3. `graph.understand_query` 的行业过滤词表改为复用 `industry_words()`，
   删掉第三份手写表（行为不变，单一事实源）。

## 测试（tests/test_aggregate.py，18 条）

- 6 种不带“行业”二字的问法必须触发（居首/最高/第一/榜首/最大 + 5 个行业）；
- 反向：`银行业2024年营收是多少？`、`半导体行业2024年毛利率是多少？` 等
  有行业词/有“行业”但无极值语义的问题**不得**触发；
- 词表派生测试：monkeypatch `corpus.INDUSTRIES` 加入“白酒/乳业”后立即能识别
  （手写常量做不到），元信息里没有的“教育”不能凭空识别；
- 原有 SQL argmax / 图路径零 LLM / 无数据拒答测试全部保留。

## 训练集 calculation 复跑（真实 DeepSeek，seed=42，100 题）

| 档 | 复跑前（RW-5 真实 800 题） | **R1 后（100 题）** |
|---|---|---|
| yoy | 40/40 | **40/40** |
| yoy_diff | 20/20 | **20/20** |
| segment_share | 19/20 | **20/20** |
| industry_max | 20/20 | **20/20** |
| **calculation** | **99/100 = 99%** | **100/100 = 100%** |

- 引用忠实度 100%（修复前 100%、修复率 10%）；P95 926.9ms；建库 118.2s。
- 逐题：`reports/r1_calc100_items.jsonl`；指标：`reports/r1_calc100_metrics.json`
  （落 `finrag_eval` `eval.runs` run_id=77）。
- 成本：约 ¥2（按方案口径；DeepSeek 实际调用 100 次 + 少量重试）。

## 边界

- 训练集没有“不带行业二字”的题，所以这次复跑只能证明**不掉分**；
  泛化效果由 R2 的 holdout v2（calculation 含不带“行业”的极值问法）验证。
- “白酒/乳业”这类行业词只有进了语料 `industry` 元信息才会被识别；
  语料里只有 5 个行业，未手写任何额外别名。
