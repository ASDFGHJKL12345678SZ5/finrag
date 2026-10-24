"""评测库隔离（FR-2）：评测用独立数据库，绝不 TRUNCATE 开发库。

事故：旧评测 runner 在开发库上 `TRUNCATE rag.documents CASCADE` 再灌语料——
开发数据被清空，评测与手工问答共库。评测是"可反复重建的实验场"，开发库是
"有状态的工作区"，两者必须物理隔离（独立 DB，而不是同库不同 schema：
trigram/HNSW 索引名、search_path、权限都按库隔离最干净）。
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

import asyncpg

_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _strip_db(dsn: str, db: str = "postgres") -> str:
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(path="/" + db))


async def ensure_eval_database(admin_dsn: str, eval_dsn: str) -> str:
    """确保评测库存在（不存在则建），返回库名。

    连接 bootstrap 库（postgres）创建目标库——asyncpg 不能连一个不存在的库。
    库名先做标识符校验再进 DDL：CREATE DATABASE 不支持参数绑定。
    """
    eval_parts = urlsplit(eval_dsn)
    db = (eval_parts.path or "").lstrip("/")
    if not db or not _DB_NAME_RE.match(db):
        raise ValueError(f"非法评测库名: {db!r}（只允许字母/数字/下划线）")

    conn = await asyncpg.connect(_strip_db(admin_dsn))
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db}"')
    finally:
        await conn.close()
    return db
