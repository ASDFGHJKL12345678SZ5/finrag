-- FinRAG 初始化：向量扩展。
-- rag schema 由应用层建（app/rag/store.py 的 SCHEMA_SQL），
-- 这里只负责实例级扩展——DDL 归属清晰：实例级归部署，业务级归代码。
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
