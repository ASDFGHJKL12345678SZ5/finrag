# FinRAG 真实 800 题（FR-10/11 后，最终）

- 语料/题库同 FR-1（1000 篇 / 8000 块 / 800 题），seed=42
- `EMBEDDING_MODE=local` + `LLM_MODE=real`（DeepSeek），hybrid 检索
- 命令：

```bash
LLM_MODE=real EMBEDDING_MODE=local python -m app.eval.runner \
  --companies 200 --strategies hybrid --end2end 0
```

## 结果

| 档 | 通过 | 通过率 |
|---|---|---|
| exact | 300/300 | 100% |
| abbr | 300/300 | 100% |
| calculation | 99/100 | **99%** |
| refusal | 100/100 | 100% |
| **总** | **799/800** | **99.9%** |

可答档 answer_accuracy = 699/700 = 99.86%；overall（含拒答）= 99.9%。

### calculation 细分

| 子类 | 通过 | 对比 FR-9（修复前） |
|---|---|---|
| yoy | 40/40 | 40/40 |
| yoy_diff | 20/20 | 20/20 |
| segment_share（表格题） | 19/20 | 14/20 |
| industry_max | **20/20** | **3/20** |

唯一失败：`和浦金融2022年金融市场业务收入占营业收入的比重是多少？`
答案"179.58亿元，占比23.45%"正确，第一句 supported，但第二句
"因此……比重是23.45%"被内容词门禁判无据（cited 块是分业务表，含数字但
不含"因此/比重/占营业收入"这些词）。这是**校验过严的边界**，不是数字幻觉；
下一轮可对"因此/综上"类结论句做"同答案内数字已被前句支持"的豁免。

### 其它

| 指标 | 值 |
|---|---|
| retrieval hybrid recall@5 | exact/abbr 100%、calculation 81%（聚合题不走向量检索） |
| 表格题 recall@5 | 100% |
| 全档 MRR | 0.914 |
| 守卫误拒 | 0% |
| 引用忠实度 | 99.9% |
| P50 / P95 | 613.5ms / 956.0ms |
| 建库 | 125.1s（含 facts 表） |

## 相比 FR-9 的提升归因

- **industry_max 3→20**：FR-10 跨文档聚合（`rag.facts` + SQL argmax + 图节点，
  零 LLM），见 `fr10_aggregate.md`；
- **segment_share 14→19**：FR-11 引用自动修复 + 指标词典补"收入"（守卫不再
  把分业务表"收入"当成缺少营业收入），见 `fr11_citation_repair.md`；
- 总通过率 97.1% → **99.9%**，P95 995→956ms（聚合路径没有 LLM 调用，反而更快）。
