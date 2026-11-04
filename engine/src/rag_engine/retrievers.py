"""LlamaIndex 检索编排：隔离的 Qdrant 双召回与 RRF，不扫描/缓存截断语料。"""

import asyncio

import httpx
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode

from rag_engine.config import Settings
from rag_engine.errors import EngineError
from rag_engine.http import post_json
from rag_engine.lexical import SPARSE_MODEL
from rag_engine.models import Hit, RetrievalRequest
from rag_engine.sparse_index import scoped_filter


class ScopedQdrantRetriever(BaseRetriever):
    """一个 Retriever 对应一种真实向量索引，返回经过隔离复核的 LlamaIndex Node。"""

    def __init__(
        self,
        request: RetrievalRequest,
        tenant: str,
        settings: Settings,
        client: httpx.AsyncClient,
        vector: list | dict,
        sparse: bool = False,
    ):
        super().__init__()
        self.request = request
        self.tenant = tenant
        self.settings = settings
        self.client = client
        self.vector = vector
        self.sparse = sparse

    def _retrieve(self, query_bundle: QueryBundle):
        raise RuntimeError("async retrieval is required")

    async def _aretrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        headers = {}
        if self.settings.qdrant_api_key.get_secret_value():
            headers["api-key"] = self.settings.qdrant_api_key.get_secret_value()
        body = {
            "vector": {"name": SPARSE_MODEL, "vector": self.vector} if self.sparse else self.vector,
            "filter": scoped_filter(self.tenant, self.request.knowledgeBase),
            "limit": max(self.request.topK, self.request.candidateCount),
            "with_payload": True,
            "with_vector": False,
        }
        if not self.sparse:
            body["score_threshold"] = self.request.similarityThreshold
        response = await post_json(
            self.client,
            f"{self.settings.qdrant_url}/collections/{self.settings.collection}/points/search",
            body,
            headers,
            self.settings.max_upstream_bytes,
        )
        try:
            raw_hits = response["result"]
            if not isinstance(raw_hits, list) or len(raw_hits) > body["limit"]:
                raise ValueError("hit limit")
            nodes = []
            seen = set()
            expected = {
                "tenant_id": self.tenant,
                "knowledge_base": self.request.knowledgeBase,
                "document_status": "ACTIVE",
            }
            for value in raw_hits:
                payload = value["payload"]
                if any(payload.get(key) != expected_value for key, expected_value in expected.items()):
                    raise EngineError("TENANT_ISOLATION_VIOLATION")
                if (
                    payload.get("embedding_model") != self.settings.embedding_model
                    or payload.get("embedding_dimension") != self.settings.embedding_dimension
                ):
                    raise EngineError("EMBEDDING_METADATA_MISMATCH")
                if self.settings.hybrid_enabled and payload.get("sparse_model") != SPARSE_MODEL:
                    raise EngineError("SPARSE_INDEX_INCOMPLETE", 503)
                content = payload.get("content")
                if not isinstance(content, str) or not content or len(content) > 6000:
                    raise ValueError("content")
                hit = Hit(id=str(value["id"]), score=value["score"], payload=payload)
                if hit.id in seen:
                    raise ValueError("duplicate point")
                seen.add(hit.id)
                nodes.append(
                    NodeWithScore(node=TextNode(id_=hit.id, text=content, metadata=payload), score=hit.score)
                )
            return nodes
        except (KeyError, TypeError, AttributeError, ValueError) as error:
            raise EngineError("VECTOR_INVALID_RESPONSE") from error


class ReciprocalRankRetriever(BaseRetriever):
    """通过 LlamaIndex 的异步 Retriever 管线组合双路结果；RRF k=60，名次从 1 起。"""

    def __init__(self, retrievers: list[BaseRetriever], limit: int):
        super().__init__()
        self.retrievers = retrievers
        self.limit = limit

    def _retrieve(self, query_bundle: QueryBundle):
        raise RuntimeError("async retrieval is required")

    async def _aretrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        tasks = [asyncio.create_task(retriever.aretrieve(query_bundle)) for retriever in self.retrievers]
        try:
            rankings = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        scores = {}
        nodes = {}
        for ranking in rankings:
            for rank, node in enumerate(sorted(ranking, key=lambda value: value.score, reverse=True), 1):
                point_id = node.node.node_id
                if point_id in nodes and (
                    nodes[point_id].text != node.node.text or nodes[point_id].metadata != node.node.metadata
                ):
                    raise EngineError("VECTOR_INCONSISTENT_RESPONSE")
                scores[point_id] = scores.get(point_id, 0) + 1 / (60 + rank)
                nodes[point_id] = node.node
        ordered = sorted(scores, key=lambda point_id: (-scores[point_id], point_id))
        return [
            NodeWithScore(node=nodes[point_id], score=scores[point_id]) for point_id in ordered[: self.limit]
        ]
