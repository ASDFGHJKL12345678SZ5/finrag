"""混合检索：向量召回 + 关键词召回 -> 并集 -> 融合重排。

设计决策（ADR-04 展开）：为什么重排用分数融合 + 来源去重而不是 cross-encoder：
- cross-encoder 要额外下载模型、每次查询多一次推理，万级块下收益不确定；
- 分数融合（min-max 归一后加权）完全透明可测——每个 chunk 的得分
  怎么来的一句话说清，出问题能定位；
- 来源去重（同文档最多留 2 块）防止"一篇研报霸榜"——混合检索的
  经典失效模式：某篇文档用词碰巧全中，前十名全是它的段落，
  其他研报的证据被挤出上下文。这个规则直接写进重排，不依赖调参。

留有余地：rerank 函数是独立纯函数，评测数据说话后要上 cross-encoder
只换这个函数，检索与生成的接口不动。
"""
from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger
from app.rag import store

log = get_logger("retrieve")

# 实体锚定加权（D13）：问题里点到名的公司，其文档的块分数乘这个系数。
# 为什么需要：易混公司对（"华曜新能" vs "华曜新源"）共享前缀，trigram
# 二元组大面积重合——纯词面打分会把两家的块混在一起排名。完整实体名
# 是唯一可靠的判别依据，这是生产 RAG 的标准做法（entity-aware
# retrieval：先链接实体，再给该实体的文档加权），不是为评测开的门。
COMPANY_ANCHOR_BOOST = 2.0


def _min_max(scores: list[float]) -> list[float]:
    """min-max 归一化。全相等时退化为 1.0（避免除 0，也避免虚高）。"""
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-9:
        return [1.0] * len(scores)
    return [(s - lo) / (hi - lo) for s in scores]


def detect_anchor(question: str, companies: list[str]) -> str | None:
    """从问题里识别被点到名的公司（最长匹配优先）。

    最长匹配优先：公司名互为前缀时（"华曜" 与 "华曜新能"），短名会
    把长名的问题错锚到短名文档上——取最长命中避免这种错位。
    问题不含任何已知公司名时返回 None（锚定不生效，回退纯语义排序）。
    """
    hits = [c for c in companies if c and c in question]
    if not hits:
        return None
    return max(hits, key=len)


def fuse(
    vector_rows: list[dict],
    keyword_rows: list[dict],
    vector_weight: float = 0.6,
    keyword_weight: float = 0.4,
    max_per_source: int = 2,
    anchor: str | None = None,
) -> list[dict]:
    """两路召回融合重排：min-max 归一后加权求和，实体锚定，同文档限流，降序输出。

    权重 0.6/0.4 的来历：向量路管"语义相关"，关键词路管"实体精确"，
    金融问答两者都重要；初始取偏语义的 6:4，评测后按数据调（ADR-04）。

    anchor（D13）：被点到名的公司 source，其块在融合后乘
    COMPANY_ANCHOR_BOOST——放在排序与限流之前，让"问 A 公司"时
    A 的块稳定压过同行业易混公司的块。
    """
    v_scores = _min_max([r["score"] for r in vector_rows])
    k_scores = _min_max([r["score"] for r in keyword_rows])

    merged: dict[tuple[str, int], dict] = {}
    routes = (
        ("vector", vector_rows, v_scores, vector_weight),
        ("keyword", keyword_rows, k_scores, keyword_weight),
    )
    for route, rows, norm, weight in routes:
        for row, s in zip(rows, norm, strict=False):
            key = (row["source"], row["chunk_index"])
            if key in merged:
                merged[key]["score"] += weight * s
                merged[key]["via"].append(route)
            else:
                item = dict(row)
                item["score"] = weight * s
                item["via"] = [route]
                merged[key] = item

    if anchor:
        # source 形如 "{industry}-{idx:03d}-{company}"，anchor 是公司名——
        # 用包含判断（公司名全局唯一且互不为子串，由语料不变量测试看守，
        # 不存在"A 的公司名是 B 的公司名子串"导致误锚的情况）
        for item in merged.values():
            if anchor in item["source"]:
                item["score"] *= COMPANY_ANCHOR_BOOST

    ranked = sorted(merged.values(), key=lambda r: r["score"], reverse=True)

    # 来源去重：同一文档最多 max_per_source 块，防单篇霸榜
    per_source: dict[str, int] = {}
    out: list[dict] = []
    for item in ranked:
        n = per_source.get(item["source"], 0)
        if n >= max_per_source:
            continue
        per_source[item["source"]] = n + 1
        out.append(item)
    return out


async def keyword_weights(question: str) -> dict[str, float]:
    """关键词路的领域权重（FR-4 起 IDF 由 SQL 里的 BM25 直接算）。

    为什么不在这里算 IDF：df 已经入库预计算，BM25 在一条 SQL 里用 df 打分；
    这里只保留"指标词加权"这一层领域先验（与检索策略无关）。早期版在这里
    每次查询 `count(*)` + 全表 df，是关键词路最贵的两次 O(N) 扫描。
    """
    from app.rag.store import _extract_terms, boost_metric_terms

    terms = _extract_terms(question)[:12]
    return boost_metric_terms(question, dict.fromkeys(terms, 1.0))


async def hybrid_search(
    question: str, industry: str | None = None, top_k: int | None = None
) -> list[dict]:
    """向量 + 关键词并集召回后融合重排，返回带引用编号的 chunk 列表。"""
    from app.rag.ingest import embed_query, embed_query_async

    settings = get_settings()
    top_k = top_k or settings.retrieve_top_k
    # FR-5：ONNX 推理放线程池，不阻塞事件循环（同步版在并发下会把 worker 卡住）。
    # embed_in_thread=False 保留旧行为，供压测 A/B 对照。
    qv = (
        await embed_query_async(question)
        if settings.embed_in_thread
        else embed_query(question)
    )
    vector_rows = await store.vector_search(qv, top_k=settings.retrieve_top_k, industry=industry)
    # IDF 加权：稀有词（指标名）比常见词（公司名）更能定位到正确块——
    # 评测实测：没有 IDF 时问"归母净利润"捞回的是只写营业收入的概况块，
    # answer_accuracy 卡在 63%（权重逻辑见 keyword_weights）

    weights = await keyword_weights(question)
    keyword_rows = await store.keyword_search(
        question, top_k=settings.retrieve_keyword_k, industry=industry, weights=weights
    )
    # mock 模式：哈希向量不携带语义，把它混进排序只会用噪声稀释真实命中，
    # 所以向量权重归零、走纯关键词路——mock 冒烟因此能验证真实召回逻辑。
    # real 模式才按配置权重融合（默认 0.6/0.4，D6 评测后调）。
    vw = 0.0 if settings.embedding_mode == "mock" else settings.vector_weight
    kw = 1.0 if settings.embedding_mode == "mock" else settings.keyword_weight
    # 实体锚定（D13）：问题点到哪家公司，就给该公司的块加分——
    # 易混公司对共享前缀，纯词面打分会混排；完整实体名是可靠判别依据
    anchor = detect_anchor(question, await store.company_names())
    ranked = fuse(vector_rows, keyword_rows, vector_weight=vw, keyword_weight=kw, anchor=anchor)

    # 引用编号 c1..cN：生成阶段强制带编号，校验阶段按编号回溯证据
    for i, item in enumerate(ranked[:top_k]):
        item["citation_id"] = f"c{i + 1}"
    log.info(
        "retrieve.done",
        extra={"context": {
            "vector": len(vector_rows), "keyword": len(keyword_rows),
            "fused": len(ranked[:top_k]), "industry": industry,
        }},
    )
    return ranked[:top_k]
