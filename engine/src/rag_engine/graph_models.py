"""图任务身份协议；控制字段不接受模型或浏览器覆盖。"""

from typing import Any, Literal

from pydantic import Field, model_validator

from rag_engine.models import Contract


class ActiveGraphSource(Contract):
    pointId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    documentId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    documentCode: str = Field(min_length=1, max_length=128)
    documentVersion: int = Field(ge=1, le=2_147_483_647)
    documentRevision: int = Field(ge=0, le=9_223_372_036_854_775_807)
    chunkId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    contentSha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class GraphSourcesRequest(Contract):
    knowledgeBase: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    activeSources: list[ActiveGraphSource] = Field(max_length=10000)

    @model_validator(mode="after")
    def unique_consistent_sources(self):
        points = set()
        chunks = set()
        documents = {}
        for source in self.activeSources:
            identity = (source.documentCode, source.documentVersion, source.documentRevision)
            if (
                source.pointId in points
                or (source.documentId, source.chunkId) in chunks
                or documents.get(source.documentId, identity) != identity
            ):
                raise ValueError("inconsistent source identities")
            points.add(source.pointId)
            chunks.add((source.documentId, source.chunkId))
            documents[source.documentId] = identity
        return self


class GraphRestoreFence(Contract):
    documentId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    barrierEpoch: int = Field(ge=1)
    documentRevision: int = Field(ge=0)


class GraphBuildRequest(GraphSourcesRequest):
    reconcile: bool = False
    restoreFence: GraphRestoreFence | None = None

    @model_validator(mode="after")
    def restore_is_one_target_document(self):
        if self.restoreFence and (
            self.reconcile
            or not self.activeSources
            or any(
                source.documentId != self.restoreFence.documentId
                or source.documentRevision != self.restoreFence.documentRevision
                for source in self.activeSources
            )
        ):
            raise ValueError("restore fence must match one source document")
        return self


class GraphQueryRequest(GraphSourcesRequest):
    searchText: str = Field(default="", max_length=256)
    nodeType: str | None = Field(default=None, max_length=32)
    viewMode: Literal["OVERVIEW", "DETAIL"] = "OVERVIEW"
    limit: int = Field(default=200, ge=1, le=500)


class GraphDeactivation(Contract):
    documentId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    documentRevision: int = Field(ge=0)


class GraphBuild(Contract):
    buildId: str
    knowledgeBase: str
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
    documentCount: int = Field(ge=0)
    processedDocumentCount: int = Field(ge=0)
    nodeCount: int = Field(ge=0)
    edgeCount: int = Field(ge=0)
    errorCode: str | None = None
    createdAt: str | None = None
    updatedAt: str | None = None


class GraphProvenance(ActiveGraphSource):
    sourceName: str | None = None
    sourceType: str | None = None
    sourceLocator: str | None = None
    chunkType: str | None = None
    sectionPath: str | None = None
    evidence: str | None = None
    evidenceTruncated: bool | None = None
    evidenceNormalized: bool | None = None
    sourceMetadata: dict[str, Any] | None = None
    duplicateLocators: list[str] | None = None
    duplicateSourceMetadata: list[dict[str, Any]] | None = None
    duplicateOccurrenceCount: int | None = None


class GraphProperties(Contract):
    sources: list[GraphProvenance] = Field(min_length=1, max_length=8)
    sourceCount: int = Field(ge=1)
    sourceTruncated: bool
    extractionMethod: str
    model: str
    models: list[str]


class GraphNode(Contract):
    id: str
    type: Literal["ENTITY"]
    name: str
    label: str
    documentId: str | None
    documentCode: str | None
    documentVersion: int | None
    sourceLocator: str | None
    properties: GraphProperties


class GraphEdge(Contract):
    id: str
    source: str
    target: str
    type: Literal["RELATED_TO"]
    label: str
    sourceLocator: str | None
    properties: GraphProperties


class GraphResponse(Contract):
    knowledgeBase: str
    viewMode: Literal["OVERVIEW", "DETAIL"]
    nodeCount: int = Field(ge=0, le=500)
    edgeCount: int = Field(ge=0, le=2000)
    truncated: bool
    nodeTypeCounts: dict[str, int]
    relationTypeCounts: dict[str, int]
    nodes: list[GraphNode] = Field(max_length=500)
    edges: list[GraphEdge] = Field(max_length=2000)
    backend: Literal["neo4j"]
    build: GraphBuild | None
