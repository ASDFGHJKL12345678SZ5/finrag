# 检索融合实验：min-max / RRF / RRF + bge-reranker（FR-6）

> 语料：FR-1 难基准 200 公司 × 5 年 = 1000 篇 / 8000 块；题库 800 题；
> embedding = 本地 bge-small-zh-v1.5（local），检索 top-5；seed=42。
> 只跑检索（--end2end -1），端到端见 FR-9 真实评测。

## 结果（recall@5 / MRR）

| 策略 | exact | abbr | calculation | 全档 MRR | 证据块命中（exact/abbr/calc） |
|---|---|---|---|---|---|
| vector_only | 90.0% / 0.800 | 87.7% / 0.779 | 74.0% / 0.694 | 0.776 | 89.7% / 87.7% / 41% |
| hybrid（min-max 0.6:0.4） | **100% / 0.968** | **100% / 0.924** | 81% / 0.772 | 0.921 | **100% / 100% / 61%** |
| rrf（k=60） | 100% / 0.930 | 91.7% / 0.868 | **83% / 0.780** | 0.882 | 100% / 91.7% / 52% |
| rrf_rerank（bge-reranker-base） | **100% / 0.986** | **100% / 0.975** | 82% / 0.748 | **0.947** | 100% / 100% / 56% |

## 读法与选型

1. **RRF 单独用会掉召回**：abbr 100% → 91.7%。原因是 RRF 只看名次，关键词路
   在缩写/同义词上名次不稳定时，向量路（local embedding 对 3 字缩写并不敏感）
   被同等加权，把正确块挤出 top-5。RRF 的"分数无关"优势没有兑现成召回。
2. **重排把 RRF 的召回救回来、MRR 最高**：abbr 100%、exact/abbr MRR 最高
   （0.986/0.975），全档 MRR 0.947。代价是每次查询 +~140ms（20 候选 CPU 推理）
   与 1.04GB 模型。
3. **hybrid 是 Pareto 选择**：recall 100/100/81、MRR 0.921，零额外模型与延迟；
   与 rrf_rerank 的差距是 calculation +1 题、全档 MRR +0.026（约 18 个名次点）。
4. **calculation 的瓶颈不是融合**：四种策略都卡在 80% 左右——20 道行业极值题
   需要跨 40 家公司做 argmax，单文档 RAG 无论怎么融合都取不到"全部候选"。
5. **表格题（分业务占比）**：真实 800 题里 20 道 segment_share，hybrid 检索
   top-5 命中 20/20、证据块 20/20；答案 14/20，6 道失败是"算对了但引用块
   不含百分比"被 verify 拒。融合策略不改变表格检索质量，瓶颈在引用选择与
   校验规则。

**默认策略**：`hybrid`。`rrf_rerank` 在 `FUSION_STRATEGY=rrf_rerank` 下启用，
供质量优先场景；RRF 单独作为中间实验保留，不作默认。这是"指标 + 延迟/依赖"
的联合决策，不是唯 MRR 论——README §4.7 同样写明。

## 复现

```bash
EMBEDDING_MODE=local python -m app.eval.runner \
  --companies 200 --strategies vector_only,hybrid,rrf,rrf_rerank --end2end -1
```

（reranker 首次运行会下载 1.04GB；`app/rag/rerank.py` 在模型缺失/推理失败时
自动降级为原顺序，不阻塞问答。）
