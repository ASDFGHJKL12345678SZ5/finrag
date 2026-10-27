# 检索融合实验：min-max / RRF / RRF + bge-reranker（FR-6）

> 语料：FR-1 难基准 200 公司 × 5 年 = 1000 篇 / 8000 块；题库 800 题；
> embedding = 本地 bge-small-zh-v1.5（local），检索 top-5；seed=42。
> 只跑检索（--end2end -1），端到端见 FR-9 真实评测。
>
> **2026-10-27 更新（tf 修复后）**：入库 `chunk_terms.tf` 现在写真实词频
> （先去重再 Counter 的 bug 已修，查询侧未改）。本轮只复测了
> `EMBEDDING_MODE=mock` 下的关键词路（mock 模式向量权重为 0，实际是
> keyword-only），见下方「修复后 keyword-only 回归」。**local bge 的
> 四策略表仍是修复前历史基线，修复后尚未重跑，不能和本轮 keyword-only
> 数字直接横向比较。**

## 修复后 keyword-only 回归（2026-10-27）

> 命令：`EMBEDDING_MODE=mock LLM_MODE=mock python -m app.eval.runner
> --companies 200 --strategies hybrid --end2end -1`
> 结果：`finrag_eval.eval.runs.run_id=154`；1000 篇 / 8000 块 / 800 题。
> mock 模式下 `hybrid_search` 把向量权重设为 0、关键词权重设为 1，所以
> 这一轮验证的是 **BM25 关键词路**，不是 local 向量+关键词的 hybrid。

| 策略 | exact（n=300） | abbr（n=300） | calculation（n=100） | 表格题 segment_share（n=20） |
|---|---|---|---|---|
| keyword-only（mock） | **100% / 1.000** | **100% / 1.000** | **80% / 0.800** | **100% / 1.000** |

读法：

1. **修复没有破坏关键词路的关键档**：exact/abbr/表格 recall@5 都是 100%，
   MRR 都是 1.000。
2. **calculation 80% 不是退化**：该档 100 题里有 20 道行业极值题，必须走
   `rag.facts + SQL argmax` 的聚合旁路；单文档检索取不到全部候选，所以
   单文档检索在该档的上限约为 80%。历史表里的 81% 不应和本轮 80% 直接
   当成“掉了 1 个点”。
3. 本轮没有重跑 local bge 的 vector+keyword hybrid；下面表格是修复前的
   历史基线，保留用于解释当时的融合选型，不代表修复后的全量结果。

修复内容：

- `app/rag/store.py` 新增 `_tokenize_terms()`（保留重复）和
  `_term_frequencies()`（先 Counter，再去重/截断）；
- `_index_chunk_terms()` 用真实 tf 写 `chunk_terms`；
- `_extract_terms()` 未改，查询侧仍是原来的去重/12/36 上限；
- `tests/test_lexical_index.py` 新增“重复 3 次 tf=3”和“查询词仍去重”两条回归；
- 全量后端测试 **200 passed**。

## 修复前历史基线（local bge-small，2026-10-12）

| 策略 | exact | abbr | calculation | 表格题（segment_share） | 全档 MRR |
|---|---|---|---|---|---|
| vector_only | 83.3% / 0.740 | 85.3% / 0.751 | 70.0% / 0.673 | 60.0% / 0.542 | 0.735 |
| hybrid（min-max 0.6:0.4） | **100% / 0.956** | **100% / 0.914** | 81% / 0.779 | **100% / 0.942** | 0.913 |
| rrf（k=60） | 100% / 0.931 | 90.0% / 0.851 | **82% / 0.783** | **100% / 0.942** | 0.875 |
| rrf_rerank（bge-reranker-base） | **100% / 0.984** | **100% / 0.977** | 81% / 0.738 | 95% / 0.746 | **0.946** |

## 读法与选型

> 以下结论基于上面这张**修复前历史基线**；默认策略没有变，但修复后的
> local 四策略还没有重跑。

1. **RRF 单独用会掉召回**：abbr 100% → 90%。原因是 RRF 只看名次，关键词路
   在缩写/同义词上名次不稳定时，向量路（local embedding 对 3 字缩写并不敏感）
   被同等加权，把正确块挤出 top-5。RRF 的“分数无关”优势没有兑现成召回。
2. **重排把 abbr 召回救回来、全档 MRR 最高**（0.946），exact/abbr MRR 也最高
   （0.984/0.977）。代价是每次查询 +~140ms（20 候选 CPU 推理）与 1.04GB 模型。
3. **重排伤表格题**：segment_share recall 100% → 95%、MRR 0.942 → 0.746。
   cross-encoder 偏好连贯文本，表格行（`| 业务 | 收入 | 占比 |`）在它眼里
   不如一段现金流描述“像答案”。这是默认仍选 hybrid 的直接数据依据。
4. **hybrid 是 Pareto 选择**：exact/abbr/表格 recall 全 100%、MRR 0.913，
   零额外模型与延迟；与 rrf_rerank 的差距是全档 MRR +0.033，但表格题
   MRR 反而高 0.196（20 题表格档）。
5. **calculation 的瓶颈不是融合**：四种策略都卡在 80% 左右——20 道行业极值
   需要跨 40 家公司 argmax，单文档 RAG 无论怎么融合都取不到“全部候选”。
6. **yoy_diff 的证据命中 0% 是判据问题**：这类题需要跨块取两年数字，
   “同一块含全部数字”的 evidence_hit 对多跳题不适用；端到端真实 20/20，
   说明检索与生成没问题，报告中不把它当检索失败。

**默认策略**：`hybrid`。`rrf_rerank` 在 `FUSION_STRATEGY=rrf_rerank` 下启用，
供“纯文本问答、表格少”的场景；RRF 单独作为中间实验保留，不作默认。
这是“指标 + 延迟/依赖 + 表格题”的联合决策，不是唯 MRR 论。

## 复现

```bash
# 修复后的关键词路回归（mock 下向量权重为 0）
EMBEDDING_MODE=mock LLM_MODE=mock python -m app.eval.runner \
  --companies 200 --strategies hybrid --end2end -1

# 修复前的 local 四策略基线
EMBEDDING_MODE=local python -m app.eval.runner \
  --companies 200 --strategies vector_only,hybrid,rrf,rrf_rerank --end2end -1
```

（reranker 首次运行会下载 1.04GB；`app/rag/rerank.py` 在模型缺失/推理失败时
自动降级为原顺序，不阻塞问答。）

## 注意

- 修复只改变入库 `tf`，查询侧 `_extract_terms()` 未改；
- 本轮只验证了 keyword-only，local hybrid 四策略需要数据库与本地 embedding
  模型恢复后再复测，才能更新整张融合对比表；
- 修复后 200 个后端测试全过；新增 tf 回归测试见
  `tests/test_lexical_index.py`。
