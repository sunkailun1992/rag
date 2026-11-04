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
