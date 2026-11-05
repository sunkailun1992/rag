"""Java 与 Python 间的版本化协议；拒绝额外字段以免租户参数被批量覆盖。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class ParseRequest(Contract):
    sourceName: str = Field(min_length=1, max_length=255)
    title: str = Field(default="", max_length=256)
    contentBase64: str = Field(min_length=4, max_length=27_962_028)
    maxChunkCharacters: int = Field(default=6000, ge=256, le=6000)

    @field_validator("sourceName")
    @classmethod
    def filename_only(cls, value: str) -> str:
        if value in {".", ".."} or any(c in value for c in "/\\\x00\r\n"):
            raise ValueError("sourceName must be a filename")
        return value


class Chunk(Contract):
    chunkType: str
    sectionPath: str
    locator: str
    content: str
    metadata: dict[str, Any]


class ParseResponse(Contract):
    title: str
    sourceName: str
    sourceType: str
    chunks: list[Chunk]


class RetrievalRequest(Contract):
    query: str = Field(min_length=1, max_length=4000)
    knowledgeBase: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    topK: int = Field(default=6, ge=1, le=20)
    candidateCount: int = Field(default=30, ge=1, le=100)
    similarityThreshold: float = Field(default=0.2, ge=0, le=1, allow_inf_nan=False)
    maxContextCharacters: int = Field(default=12000, ge=1000, le=30000)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query is blank")
        return value.strip()


class Hit(Contract):
    id: str
    score: float = Field(
        allow_inf_nan=False,
        description="成功重排为通过独立相关性门槛的[0,1]分数；重排降级时为原dense或RRF分数，不同尺度不可混用。",
    )
    payload: dict[str, Any]


class RetrievalResponse(Contract):
    hits: list[Hit]
    mode: Literal["dense", "hybrid"] = Field(
        default="dense", description="实际使用的召回模式；hybrid 为完整 sparse 索引与 dense 经 RRF 融合。"
    )
    degraded: bool = Field(
        default=False, description="重排失败或词法不可用时标记；重排失败的候选未通过独立相关性门槛。"
    )
    graphAugmented: bool = Field(
        default=False,
        exclude_if=lambda value: value is False,
        description="已发布图关联扩展且通过活动Qdrant原文复核的新候选参与本次重排；缺省false。",
    )
    graphDegraded: bool = Field(
        default=False,
        exclude_if=lambda value: value is False,
        description="可选图服务超时、不可用或来源已变化；保留原向量召回与相关性重排；缺省false。",
    )
