"""Qwen embedding → LlamaIndex 隔离召回/RRF → Qwen rerank；检索不写库。"""

import asyncio
import math

import httpx

from rag_engine.config import Settings
from rag_engine.errors import EngineError
from rag_engine.graph_retrieval import augment_candidates
from rag_engine.http import post_json
from rag_engine.lexical import encode_sparse
from rag_engine.models import Hit, RetrievalRequest, RetrievalResponse
from rag_engine.observability import stage
from rag_engine.retrievers import ReciprocalRankRetriever, ScopedQdrantRetriever
from rag_engine.sparse_index import SparseIndex


async def retrieve(
    request: RetrievalRequest,
    tenant: str,
    settings: Settings,
    client: httpx.AsyncClient,
    graph_repository=None,
) -> RetrievalResponse:
    if not settings.qdrant_url or not settings.qwen_base_url or not settings.qwen_api_key.get_secret_value():
        raise EngineError("RETRIEVAL_NOT_CONFIGURED", 503)
    auth = {"Authorization": "Bearer " + settings.qwen_api_key.get_secret_value()}
    with stage("embedding"):
        embedding = await post_json(
            client,
            settings.qwen_base_url + "/embeddings",
            {
                "model": settings.embedding_model,
                "input": [request.query],
                "dimensions": settings.embedding_dimension,
                "encoding_format": "float",
            },
            auth,
            settings.max_upstream_bytes,
        )
        try:
            if len(embedding["data"]) != 1 or embedding["data"][0].get("index", 0) != 0:
                raise ValueError("embedding count/index")
            vector = embedding["data"][0]["embedding"]
            if len(vector) != settings.embedding_dimension or any(
                isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in vector
            ):
                raise ValueError("embedding dimension/value")
        except (KeyError, TypeError, IndexError, ValueError) as error:
            raise EngineError("EMBEDDING_INVALID_RESPONSE") from error
    mode = "dense"
    degraded = False
    dense = ScopedQdrantRetriever(request, tenant, settings, client, vector)
    retriever = dense
    if settings.hybrid_enabled:
        await SparseIndex(settings, client).require_ready(tenant, request.knowledgeBase)
        sparse_vector = encode_sparse(request.query)
        if sparse_vector["indices"]:
            sparse = ScopedQdrantRetriever(request, tenant, settings, client, sparse_vector, sparse=True)
            retriever = ReciprocalRankRetriever([dense, sparse], max(request.topK, request.candidateCount))
            mode = "hybrid"
        else:
            degraded = True
    with stage("recall"):
        nodes = await retriever.aretrieve(request.query)
    hits = []
    seen = set()
    for node in nodes:
        identity = node.node.metadata.get("content_sha256") or node.node.node_id
        if identity not in seen:
            hits.append(Hit(id=node.node.node_id, score=node.score, payload=node.node.metadata))
            seen.add(identity)
    if not hits:
        return RetrievalResponse(hits=[], mode=mode, degraded=degraded)
    with stage("graph_expand") as observed:
        graph = await augment_candidates(hits, request, tenant, settings, client, graph_repository)
        if graph.degraded:
            observed.result = "degraded"
    hits = graph.hits
    try:
        # 重排超时可回退已获得的向量排序，但整个请求仍受外层 3 秒总预算限制。
        with stage("rerank"):
            async with asyncio.timeout(settings.rerank_timeout_seconds):
                ranking = await post_json(
                    client,
                    settings.qwen_base_url.replace("/compatible-mode/", "/compatible-api/") + "/reranks",
                    {
                        "model": settings.rerank_model,
                        "query": request.query,
                        "documents": [hit.payload["content"] for hit in hits],
                        "top_n": request.topK,
                    },
                    auth,
                    settings.max_upstream_bytes,
                )
            ordered = []
            seen_indices = set()
            for item in ranking["results"]:
                index = item["index"]
                score = item["relevance_score"]
                if (
                    type(index) is not int
                    or not 0 <= index < len(hits)
                    or index in seen_indices
                    or type(score) not in {float, int}
                    or not math.isfinite(score)
                    or not 0 <= score <= 1
                ):
                    raise ValueError("invalid rerank")
                ordered.append(Hit(id=hits[index].id, score=float(score), payload=hits[index].payload))
                seen_indices.add(index)
            if len(ordered) != min(request.topK, len(hits)):
                raise ValueError("incomplete rerank")
            # 先校验完整 top_n 响应，再过滤相关性；过滤为空是有效的“无答案”，不是重排失败。
            # dense/TF-IDF/RRF 分数不在同一尺度，不能给降级候选套用此阈值。
            hits = [hit for hit in ordered if hit.score >= settings.rerank_score_threshold]
            hits.sort(key=lambda item: item.score, reverse=True)
    except (TimeoutError, httpx.HTTPError, EngineError, KeyError, TypeError, ValueError):
        degraded = True
        hits.sort(key=lambda item: item.score, reverse=True)
    return RetrievalResponse(
        hits=hits[: request.topK],
        mode=mode,
        degraded=degraded,
        graphAugmented=graph.augmented,
        graphDegraded=graph.degraded,
    )
