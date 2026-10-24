# 检索融合实验：min-max / RRF / RRF + bge-reranker（FR-6）

> 语料：FR-1 难基准 200 公司 × 5 年 = 1000 篇 / 8000 块；题库 800 题；
> embedding = 本地 bge-small-zh-v1.5（local），检索 top-5；seed=42。
> 只跑检索（--end2end -1），端到端见 FR-9 真实评测。本轮为**指标扩词/加权
> 合入后的重跑**，数字以本表为准。

## 结果（recall@5 / MRR）

| 策略 | exact | abbr | calculation | 表格题（segment_share） | 全档 MRR |
|---|---|---|---|---|---|
| vector_only | 83.3% / 0.740 | 85.3% / 0.751 | 70.0% / 0.673 | 60.0% / 0.542 | 0.735 |
| hybrid（min-max 0.6:0.4） | **100% / 0.956** | **100% / 0.914** | 81% / 0.779 | **100% / 0.942** | 0.913 |
| rrf（k=60） | 100% / 0.931 | 90.0% / 0.851 | **82% / 0.783** | **100% / 0.942** | 0.875 |
| rrf_rerank（bge-reranker-base） | **100% / 0.984** | **100% / 0.977** | 81% / 0.738 | 95% / 0.746 | **0.946** |

## 读法与选型

1. **RRF 单独用会掉召回**：abbr 100% → 90%。原因是 RRF 只看名次，关键词路
   在缩写/同义词上名次不稳定时，向量路（local embedding 对 3 字缩写并不敏感）
   被同等加权，把正确块挤出 top-5。RRF 的"分数无关"优势没有兑现成召回。
2. **重排把 abbr 召回救回来、全档 MRR 最高**（0.946），exact/abbr MRR 也最高
   （0.984/0.977）。代价是每次查询 +~140ms（20 候选 CPU 推理）与 1.04GB 模型。
3. **重排伤表格题**：segment_share recall 100% → 95%、MRR 0.942 → 0.746。
   cross-encoder 偏好连贯文本，表格行（`| 业务 | 收入 | 占比 |`）在它眼里
   不如一段现金流描述"像答案"。这是默认仍选 hybrid 的直接数据依据。
4. **hybrid 是 Pareto 选择**：exact/abbr/表格 recall 全 100%、MRR 0.913，
   零额外模型与延迟；与 rrf_rerank 的差距是全档 MRR +0.033，但表格题
   MRR 反而高 0.196（20 题表格档）。
5. **calculation 的瓶颈不是融合**：四种策略都卡在 80% 左右——20 道行业极值
   需要跨 40 家公司 argmax，单文档 RAG 无论怎么融合都取不到"全部候选"。
6. **yoy_diff 的证据命中 0% 是判据问题**：这类题需要跨块取两年数字，
   "同一块含全部数字"的 evidence_hit 对多跳题不适用；端到端真实 20/20，
   说明检索与生成没问题，报告中不把它当检索失败。

**默认策略**：`hybrid`。`rrf_rerank` 在 `FUSION_STRATEGY=rrf_rerank` 下启用，
供"纯文本问答、表格少"的场景；RRF 单独作为中间实验保留，不作默认。
这是"指标 + 延迟/依赖 + 表格题"的联合决策，不是唯 MRR 论。

## 复现

```bash
EMBEDDING_MODE=local python -m app.eval.runner \
  --companies 200 --strategies vector_only,hybrid,rrf,rrf_rerank --end2end -1
```

（reranker 首次运行会下载 1.04GB；`app/rag/rerank.py` 在模型缺失/推理失败时
自动降级为原顺序，不阻塞问答。）
