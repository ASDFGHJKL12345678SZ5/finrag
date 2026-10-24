> 历史记录：本报告是 FR-9（FR-10/11 聚合/引用修复之前）的结果，97.1%。
> FR-10/11 后同一 800 题为 **99.9%**，见 [real_eval_800_after_fr10.md](real_eval_800_after_fr10.md)。

# FinRAG 真实模型 800 题评测（FR-9）

## 设置

- 语料：FR-1 合成难基准，200 公司 × 5 年 = **1000 篇 / 8000 块 / 9000 事实**，seed=42
- 题库：**800 题**（exact 300 / abbr 300 / calculation 100 / refusal 100）
- 模式：`EMBEDDING_MODE=local`（bge-small-zh-v1.5）+ `LLM_MODE=real`（DeepSeek deepseek-chat）
- 检索融合：hybrid（min-max 0.6:0.4）；评测库：`finrag_eval`（独立库）
- 命令：

```bash
LLM_MODE=real EMBEDDING_MODE=local python -m app.eval.runner \
  --companies 200 --strategies hybrid --end2end 0
```

建库 113.1s（local embedding 1000 篇）；逐题明细落 `eval.runs.per_fact.items`。

## 结果

| 档 | 通过 | 通过率 |
|---|---|---|
| exact | 300/300 | 100% |
| abbr | 300/300 | 100% |
| calculation | 77/100 | 77% |
| refusal | 100/100 | 100% |
| **总** | **777/800** | **97.1%** |

可答档 answer_accuracy = 677/700 = 96.7%；整体（含拒答）= 97.1%。

### 计算题细分

| 子类 | 通过 | 失败原因 |
|---|---|---|
| yoy（同比） | 40/40 | — |
| yoy_diff（两年差） | 20/20 | — |
| segment_share（分业务占比，**表格题**） | 14/20 | 答案算对但引用块里没有该百分比，校验判 insufficient；检索与证据命中均 20/20 |
| industry_max（行业最高营收） | 3/20 | 单文档 RAG 无法跨 40 家公司 argmax；3 题靠检索恰好捞到赢家 |

### 检索（hybrid，top-5）

| 档 | recall@5 | MRR |
|---|---|---|
| exact | 100% | 0.970 |
| abbr | 100% | 0.921 |
| calculation | 81% | 0.765 |
| 全档 | — | 0.919 |

### 其它质量项

| 指标 | 值 |
|---|---|
| 守卫误拒率（oracle 证据） | 0%（exact/abbr/calculation 全 0） |
| 拒答准确率 | 100%（错年份 40/40、缺指标 30/30、未来 30/30） |
| 引用忠实度 | 97.2% |
| P50 / P95 延迟 | 640.4ms / 994.7ms |

## 失败样例（逐题明细可下钻）

- `和浦金融2022年金融市场业务收入占营业收入的比重是多少？` → 答案
  "179.58亿元，占比23.45%"正确，但引用的是多年营收表 c6（不含 23.45%），
  verify 判 insufficient。**引用选择错误，不是算术错误**。
- `食品饮料行业2024年营业收入最高的公司是哪家？` → 答"春山食品"（错），
  它只看到个别公司；需要跨文档聚合才能答对。
- 6 道 share 失败中 4 道为空答案（模型被 verify 拒绝后未重试出可用引用）。

## 读法

1. **真实 97.1% 不是"题库简单"**：exact 档是管线自检，难度在 abbr 的同义说法
   与 calculation 的多跳/聚合；refusal 三类全 100% 说明守卫判据没有因为
   真实模型而失效。
2. **citation 97.2%** 是真实模型的保守代价：算对了但引用块不含结果数字时
   宁可拒答——这符合"抗幻觉优先"的设计目标，但会损失 6 道 share 题。
   下一步可以做"算术结果引用"（允许引用操作数并标注 derived），需要
   verify 的显式规则，不在本轮范围内。
3. **industry_max 是架构边界**：单文档 RAG 没有跨文档聚合节点；保留在
   calculation 档并在报告里单列，不为了分数删题。

## 与 mock 对照

| 档 | mock | real |
|---|---|---|
| exact | 100% | 100% |
| abbr | 98.0% | 100% |
| calculation | 79.0% | 77.0% |
| refusal | 100% | 100% |
| 总 | 96.6% | 97.1% |

mock 的 abbr 低 2pt 是哈希向量不携带语义、只能靠扩词；真实 embedding 把
同义缩写补回来。calculation 真实略低是引用校验更严格（mock 摘录模型只挑
证据原句，真实模型会组织语言）。
