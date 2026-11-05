"""仅从运行环境读取私有连接，配置值不回显到 API 或日志。"""

from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAG_ENGINE_", extra="ignore", hide_input_in_errors=True)

    api_key: SecretStr = Field(min_length=32)
    qdrant_url: str = ""
    qdrant_api_key: SecretStr = SecretStr("")
    collection: str = Field(default="knowledge-vectors", pattern=r"^[A-Za-z0-9_-]{1,128}$")
    qwen_base_url: str = ""
    qwen_api_key: SecretStr = SecretStr("")
    embedding_model: str = "text-embedding-v4"
    embedding_dimension: int = Field(default=1024, ge=1, le=8192)
    rerank_model: str = "qwen3-rerank"
    # 只作用于成功重排的 relevance_score；与 dense 相似度、RRF 名次分数不同。
    rerank_score_threshold: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    # 只有 schema、完整回填及 Java 双写都已验收后才能启用。
    hybrid_enabled: bool = False
    ocr_url: str = ""
    ocr_api_key: SecretStr = SecretStr("")
    max_file_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    max_pages: int = Field(default=100, ge=1, le=100)
    max_text_characters: int = Field(default=2_000_000, ge=1000, le=2_000_000)
    max_chunks: int = Field(default=1000, ge=1, le=1000)
    parse_timeout_seconds: float = Field(default=55, ge=1, le=180)
    parse_workers: int = Field(default=2, ge=1, le=4)
    retrieval_timeout_seconds: float = Field(default=3.0, ge=0.1, le=3.0)
    rerank_timeout_seconds: float = Field(default=0.8, ge=0.05, le=1.5)
    max_upstream_bytes: int = Field(default=16 * 1024 * 1024, ge=1024, le=16 * 1024 * 1024)
    graph_enabled: bool = False
    graph_retrieval_enabled: bool = False
    neo4j_url: str = ""
    neo4j_username: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")
    neo4j_database: str = Field(default="neo4j", pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    graph_model: str = Field(default="qwen-plus", min_length=1, max_length=128)
    graph_extraction_workers: int = Field(default=1, ge=1, le=4)
    graph_queue_size: int = Field(default=2, ge=1, le=8)
    graph_build_timeout_seconds: float = Field(default=300, ge=1, le=1800)
    graph_query_timeout_seconds: float = Field(default=3, ge=0.1, le=10)
    graph_llm_timeout_seconds: float = Field(default=20, ge=0.1, le=60)
    graph_max_triplets_per_chunk: int = Field(default=12, ge=1, le=32)
    graph_max_edges: int = Field(default=2000, ge=1, le=4000)
    graph_lease_seconds: int = Field(default=30, ge=10, le=120)

    @field_validator("neo4j_url")
    @classmethod
    def neo4j_service_url(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("invalid Neo4j service URL")
        return value.rstrip("/")

    @field_validator("qdrant_url", "qwen_base_url", "ocr_url")
    @classmethod
    def service_url(cls, value: str) -> str:
        """地址只能由运维配置；禁止凭据、查询串与跳转掩盖最终目标。"""
        if not value:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("service URL must be an absolute http(s) URL without credentials/query")
        return value.rstrip("/")
