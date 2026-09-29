"""评测结果存储：eval.runs 一张表记每次评测的汇总指标。

设计决策：为什么落库而不是只写文件——简历上的数字要可复现，落库的
每次 run 带时间戳/模式/语料种子/样本量，面试官问"这数字哪来的"能
当场翻出来；文件会被覆盖，库不会。
"""
from __future__ import annotations

EVAL_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS eval;

CREATE TABLE IF NOT EXISTS eval.runs (
    run_id        BIGSERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    mode          TEXT NOT NULL,           -- mock / real
    corpus_seed   INT NOT NULL,
    n_facts       INT NOT NULL,
    metrics       JSONB NOT NULL,          -- 全部指标（含分策略对比）
    per_fact      JSONB NOT NULL           -- 逐事实明细（可下钻复盘）
);

CREATE INDEX IF NOT EXISTS runs_created_idx ON eval.runs (created_at DESC);
"""
