# FinRAG 验收矩阵（双项目优化方案 FR-0…FR-11 + D7）

> 每一项都指到「代码 / 测试 / 数字证据 / 提交」。数字均为可复跑的命令产出；
> mock 与真实模式分开标注。最近更新：2026-10-25。

| 任务 | 交付 | 验证（测试/数字） | 提交 |
|---|---|---|---|
| FR-0 | `ask.py` 连不上 API 不抛栈；措辞与实现对齐 | `tests/test_ask_client.py` | `9a0595d` |
| FR-1 | 难基准：200 公司×5 年=1000 篇 / 8000 块 / 9000 事实 / 800 题；40 组易混 | `tests/test_corpus.py`；mock 3 seed 96.4%±0.9；真实 800 题 97.1%→见 FR-9 | `8d72103` |
| FR-2 | 守卫独立词典 + 评测隔离 `finrag_eval` + 逐题明细 + 误拒/漏拒 | `test_sufficiency.py`（AST 不变量）/`test_eval_isolation.py` | `74bb34e` |
| FR-3 | 表格感知分块：长表按行组切、每子块重复表头、kind/section_path | `test_chunker.py`（10 条） | `467cfff` |
| FR-4 | 倒排索引 + 预计算 df + BM25；1000 篇上 P50 183→47ms（3.9×）、df 184→1.48ms（124×） | `test_lexical_index.py`；`reports/fr4_bench_1000.md`、`fr4_explain.txt` | `c980dbd`、`e792855`、`186550c` |
| FR-5 | ONNX 推理 to_thread、进程级图单例、LLM 重试收窄、SSE 超时；local A/B QPS +8~11%、P95 -7~13% | `test_embeddings.py`/`test_graph_integration.py`/`test_llm_retry.py`/`test_sse_single_run.py` | `42c6d8c`、`e792855` |
| FR-6 | RRF + bge-reranker 可选；按档位 recall/MRR（含表格档）；默认 hybrid | `test_fusion.py`；`reports/fr6_fusion.md` | `fdb94d7`、`186550c` |
| FR-7 | 数字边界 + 单位归一 + 多引用；rewrite 只用原问题实体 | `test_verify.py`（15 条）/`test_graph_integration.py` | `74bb34e` |
| FR-8 | API Key + Redis Lua 桶 + CORS 收紧 + 错误编号脱敏 + Langfuse trace/span（真实 key 验证） | `test_api_security.py`（10 条）；`scripts/verify_langfuse.py` 查到 `finrag.qa`+5 span | `42c6d8c`、`186550c` |
| FR-9 | 真实 DeepSeek 全量 800 题；200 题代表性抽样（修正抽样偏差） | `reports/real_eval_800.md`、`real_eval_200.md`、JSONL/ metrics | `8d72103`、`186550c` |
| FR-10 | 跨文档聚合：`rag.facts` 结构化事实 + 行业极值 SQL argmax + 图节点（零 LLM） | `test_aggregate.py`（8 条）；真实 800 中 industry_max 3→**20/20**，calculation 77→99%，总 97.1→99.9% | 本轮 |
| FR-11 | 引用自动修复：论断引错块但数字在别的 retrieved 块时补引用；修不好仍拒答 | `test_verify.py` 新增 2 条；真实 share 14→**19/20** | 本轮 |
| D7 | CI 双 job + Redis service + badge；架构图；简历数字核验；本矩阵 | `.github/workflows/ci.yml`；README §1/§4.7；pytest 116+ | `d6c7a0d`、本轮 |

## 当前门禁

- `ruff check app tests scripts` 全绿
- `pytest tests/ -q`：**198 passed**
- 真实 800 题（当前代码）：**800/800 = 100.0%**（RW-5 时 797/800=99.6%，见 `reports/real_eval_800_rw5.md`）；留出集 v2 **195/200 = 97.5%**（`reports/holdout_v2.md`；v1 168/200=84.0% 为 RW-3 前代码，仅存档）
- 两仓库 `git status` clean

## 已知边界（不藏）

- 行业极值只支持整公司口径指标；分业务口径不支持。
- 引用修复只补引用、不改答案文字；找不到含该数字的块仍按原判据拒答。
- 唯一失败 share 题的"因此……"结论句被内容词门禁判无据（数字正确），
  属于校验过严边界；下一轮可对同答案内已支持结论做豁免。
- calculation 的行业极值依赖 `rag.facts` 完整入库；历史库需重灌才会补事实表。
