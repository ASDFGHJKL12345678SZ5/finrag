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


def _min_max(scores: list[float]) -> list[float]:
    """min-max 归一化。全相等时退化为 1.0（避免除 0，也避免虚高）。"""
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-9:
        return [1.0] * len(scores)
    return [(s - lo) / (hi - lo) for s in scores]


def fuse(
    vector_rows: list[dict],
    keyword_rows: list[dict],
    vector_weight: float = 0.6,
    keyword_weight: float = 0.4,
    max_per_source: int = 2,
) -> list[dict]:
    """两路召回融合重排：min-max 归一后加权求和，同文档限流，降序输出。

    权重 0.6/0.4 的来历：向量路管"语义相关"，关键词路管"实体精确"，
    金融问答两者都重要；初始取偏语义的 6:4，评测后按数据调（ADR-04）。
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


async def hybrid_search(
    question: str, industry: str | None = None, top_k: int | None = None
) -> list[dict]:
    """向量 + 关键词并集召回后融合重排，返回带引用编号的 chunk 列表。"""
    from app.rag.ingest import embed_query

    settings = get_settings()
    top_k = top_k or settings.retrieve_top_k
    qv = embed_query(question)
    vector_rows = await store.vector_search(qv, top_k=settings.retrieve_top_k, industry=industry)
    keyword_rows = await store.keyword_search(
        question, top_k=settings.retrieve_keyword_k, industry=industry
    )
    # mock 模式：哈希向量不携带语义，把它混进排序只会用噪声稀释真实命中，
    # 所以向量权重归零、走纯关键词路——mock 冒烟因此能验证真实召回逻辑。
    # real 模式才按配置权重融合（默认 0.6/0.4，D6 评测后调）。
    vw = 0.0 if settings.embedding_mode == "mock" else settings.vector_weight
    kw = 1.0 if settings.embedding_mode == "mock" else settings.keyword_weight
    ranked = fuse(vector_rows, keyword_rows, vector_weight=vw, keyword_weight=kw)

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
