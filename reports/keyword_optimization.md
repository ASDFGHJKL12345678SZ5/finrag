# 关键词路优化：倒排索引 + 预计算 df + BM25（FR-4）

> 基准环境：本机 PostgreSQL 16（Docker），8,000 块 / 200 篇合成文档 / 346,980
> 条倒排 posting；20 条查询取 P50/P95。脚本为一次性规模实验（`_tmp/bench_keyword.py`），
> 不入库；可复现部分（索引正确性）在 `tests/test_lexical_index.py`。

## 问题（改造前）

每次关键词/混合检索要做两次 O(N) 全表操作：

1. `term_df()`：对 `rag.chunks` 全表做 12 路 `ILIKE` 再逐列聚合算 df——
   8,000 块上实测 **136–164ms/次**；
2. `keyword_search()`：同样的 12 路 ILIKE 全表扫描 + `length()` 打分，
   实测 **134ms（P50）/ 187ms（P95）**；
3. 另外每次还要 `SELECT count(*) FROM rag.chunks` 一次。

pg_trgm 的 GIN 索引对 2 字符中文模式基本无效（trigram 需要 3 字符），所以
"有索引"也只是心理安慰。三项相加，关键词路单次 ~270ms，且随块数线性增长。

## 改法

- 入库时维护倒排索引：`rag.chunk_terms(chunk_id, term, tf)`（正文 + source
  元数据都进索引，避免财务块因正文无公司名而丢召回）+ `rag.term_df(term, df)`
  + 单行 `rag.corpus_stats(n_chunks, avg_len)`。
- 重灌同一篇时先按旧 posting 扣减 df，再插新 posting（否则 df 只增不减）。
- 查询改成一条 SQL：12 个词 `= ANY($1)` 查倒排，SQL 内用 BM25
  （k1=1.2, b=0.75）打分，N/平均块长读单行统计表——**查询期零 count(*)、
  不再做 12 路全表 ILIKE**；仍需把命中的 chunk_id join 回 chunks（8k 行约
  1ms），但扫描的是 posting 而不是逐行 ILIKE。
- 索引未建立时（旧库未重灌）自动退化到旧 ILIKE 实现，升级期不静默零召回。
- `keyword_weights` 不再算 IDF（交给 BM25），只保留指标词 ×2 的领域加权。
- 新增 `reset_lexical_index()`：评测/测试 TRUNCATE 文档后必须调用，否则 df 是脏的。

## 数字（8,000 块）

| 指标 | 改造前（ILIKE + 全表 df） | 改造后（倒排 + BM25） | 变化 |
|---|---|---|---|
| df 计算 | 136–164 ms/次 | **1.8–4.0 ms/次** | **~40–80×** |
| 关键词检索 P50 | 133.7 ms | **111.5 ms** | -17% |
| 关键词检索 P95 | 187.4 ms | **166.4 ms** | -11% |
| 单次关键词路合计（df+检索） | ~270 ms | **~113 ms** | **~2.4×** |
| 查询期全表 count(*) | 1 次/查询 | 0 | — |

**诚实边界**：检索本身只快了 11–17%，因为本基准块短、posting 密度低，
SQL 聚合仍是主要成本；**df 预计算才是大头（2.4× 合计加速）**。块数越大
（FR-1 的 1000 篇 / ~1 万块）全表项越吃亏，倒排随 posting 线性增长，
优势会扩大。recall 不下降这一条在 FR-1 大语料上按档位复测（本报告只测延迟）。

## EXPLAIN 对照（8,000 块，`reports/fr4_explain.txt`）

- **旧**：`Seq Scan on chunks` 8000 行 + 12 路 `ILIKE` Filter → Sort →
  Limit；Execution **89.1ms**。
- **新**：`Index Scan using chunk_terms_term_idx`（7 次，平均 6569 行/次）
  → Hash Join `chunks`（8000 行，1.2ms）→ HashAggregate → Sort；
  Execution **102ms**（这一次单跑；20 次查询中位数 111.5ms vs 旧 133.7ms）。

新计划把"逐行 12 路 ILIKE"换成了"倒排 posting 扫描 + 一次 hash join"。
注意：chunks 表仍被顺序扫描用于 join（8k 行成本 ~1ms），**这不是"完全无
全表扫描"**；真正的收益来自 df 预计算（136–164ms → 1.8–4ms）与避免 12 路
字符串匹配，而不是把 chunks 也变成索引-only。

## 正确性不变量（tests/test_lexical_index.py，6 条）

- 入库后 `chunk_terms`/`term_df`/`corpus_stats` 都被维护；
- BM25 命中正确文档，行业过滤生效；
- 同一 source 重灌 df 不翻倍；
- 删旧块会扣减 df（"营业"从 1 → 0，新词"员工"=1）；
- `company_names` 返回简称与 3 字缩写（缩写从 documents.meta.abbr 读，
  注意 asyncpg 把 jsonb 返回成 str，需要显式 json.loads）；
- `reset_lexical_index()` 清空三张表与统计。
