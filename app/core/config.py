"""FinRAG 配置。

设计说明：
- 所有配置走环境变量（pydantic-settings，大小写不敏感），.env 仅本地开发用
- embedding 维度是运行期常量：换模型必须同步改 dim，否则 pgvector 建列会错。
  用 dim_from_model 在启动时校验，避免"模型换了维度没换"的静默错误
- pg_dsn 默认指向 localhost 开发实例；容器内部署由 compose 注入服务名
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    log_level: str = "INFO"

    # ---- PostgreSQL（pgvector）----
    pg_dsn: str = Field(
        default="postgresql://postgres:postgres@localhost:5433/finrag"
    )
    pg_pool_min: int = 2
    pg_pool_max: int = 10

    # ---- Embedding ----
    # bge-small-zh-v1.5：本地 ONNX 推理，中文金融语料够用，维度 512
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dim: int = 512
    # mock 模式：确定性哈希向量，CI 不依赖模型下载，向量不可复现换真模型
    embedding_mode: str = "local"  # local | mock

    # ---- 分块 ----
    chunk_max_chars: int = 600     # 单块字符上限（中文约 300-400 字）
    chunk_overlap_chars: int = 60  # 相邻块重叠，缓解边界切断

    # ---- 检索 ----
    retrieve_top_k: int = 8
    # 融合权重：向量路管语义相关，关键词路管实体精确。初始 6:4，
    # D6 评测数据说话后调（mock 模式向量是噪声，自动降为 0——见 retrieve.py）
    vector_weight: float = 0.6
    keyword_weight: float = 0.4        # 关键词路权重（向量 0.6 : 0.4）
    retrieve_keyword_k: int = 4    # 关键词召回条数（并数去重后重排）

    # ---- 引用校验（D5）----

    # ---- LLM ----
    # mock：规则假模型（CI/无 key 可复现，含幻觉型威胁模拟）
    # real：OpenAI 兼容协议，DeepSeek/Qwen 换 base_url + key 即切
    llm_mode: str = "mock"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"


@lru_cache
def get_settings() -> Settings:
    return Settings()
