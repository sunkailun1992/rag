"""图只扩展受控来源，最终上下文必须重新回读活动 Qdrant 原文。"""

import asyncio
import json
import time

import httpx
import pytest

from rag_engine.graph_models import ActiveGraphSource
from rag_engine.graph_sources import content_fingerprint, source_key
from rag_engine.lexical import SPARSE_MODEL
from rag_engine.models import RetrievalRequest
from rag_engine.retrieval import retrieve


def point(identity, content, **updates):
    payload = {
        "tenant_id": "tenant-a",
        "knowledge_base": "kb-a",
        "document_status": "ACTIVE",
        "document_id": "doc-" + identity,
        "document_code": "code-" + identity,
        "document_version": 1,
        "document_revision": 100,
        "chunk_id": "chunk-" + identity,
        "content_sha256": content_fingerprint(content),
        "content": content,
        "document_title": "Synthetic",
        "source_name": "fixture.pdf",
        "embedding_model": "text-embedding-v4",
        "embedding_dimension": 3,
    }
    payload.update(updates)
    return {"id": identity, "score": 0.9, "payload": payload}


def identity(value):
    payload = value["payload"]
    return ActiveGraphSource(
        pointId=value["id"],
        documentId=payload["document_id"],
        documentCode=payload["document_code"],
        documentVersion=payload["document_version"],
        documentRevision=payload["document_revision"],
        chunkId=payload["chunk_id"],
        contentSha256=payload["content_sha256"],
    )


def row(value):
    source = identity(value)
    return {
        "pointId": value["id"],
        "sourceKey": source_key(source),
        "sourceJson": json.dumps(source.model_dump() | {"evidence": "MODEL TRIPLE MUST NOT BE CONTEXT"}),
        "tenant": "tenant-a",
        "knowledgeBase": "kb-a",
        "generation": "published-build-1",
    }


class Repository:
    def __init__(self, rows=None, delay=0, fail=False):
        self.values = rows or []
        self.delay = delay
        self.fail = fail
        self.calls = []
        self.cancelled = False

    async def rows(self, query, **params):
        self.calls.append((query, params))
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail:
                raise RuntimeError("private Neo4j connection details")
            return self.values
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def upstream(settings, seeds, targets=(), rerank=True, score=0.9):
    calls = {"reread": [], "rerank": []}

    def handler(request):
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [1, 0, 0]}]})
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"result": seeds})
        if request.url.path.endswith("/points"):
            calls["reread"].append(body)
            return httpx.Response(200, json={"result": list(targets)})
        if request.url.path.endswith("/reranks"):
            calls["rerank"].append(body)
            if not rerank:
                return httpx.Response(503)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": index, "relevance_score": score}
                        for index in range(min(body["top_n"], len(body["documents"])))
                    ]
                },
            )
        raise AssertionError(request.url)

    settings.graph_retrieval_enabled = True
    return calls, httpx.MockTransport(handler)


async def run(settings, repository, seeds=None, targets=(), *, request=None, **options):
    if seeds is None:
        seeds = [point("seed", "Seed original evidence")]
    calls, transport = upstream(settings, seeds, targets, **options)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await retrieve(
            request or RetrievalRequest(query="synthetic query", knowledgeBase="kb-a", topK=5),
            "tenant-a",
            settings,
            client,
            graph_repository=repository,
        )
    return response, calls


@pytest.mark.asyncio
async def test_published_shared_entity_adds_only_reread_original_evidence(settings):
    target = point("neighbor", "Neighbor original evidence")
    repository = Repository([row(target)])
    response, calls = await run(settings, repository, targets=[target])
    assert {hit.id for hit in response.hits} == {"seed", "neighbor"}
    assert response.graphAugmented is True and response.graphDegraded is False
    assert response.mode == "dense" and response.degraded is False
    assert set(calls["rerank"][0]["documents"]) == {"Seed original evidence", "Neighbor original evidence"}
    assert "MODEL TRIPLE" not in response.model_dump_json()
    assert calls["reread"] == [{"ids": ["neighbor"], "with_payload": True, "with_vector": False}]
    query, params = repository.calls[0]
    assert params["tenant"] == "tenant-a" and params["kb"] == "kb-a" and params["limit"] == 20
    assert "activeGeneration" in query and "source_key" in query and "canonical_name" in query
    assert "tenant_id:$tenant" in query and "knowledge_base:$kb" in query


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {"tenant_id": "tenant-b"},
        {"knowledge_base": "kb-b"},
        {"document_status": "DELETED"},
        {"document_status": "SUPERSEDED"},
        {"document_revision": 101},
        {"document_version": 2},
        {"embedding_model": "wrong-model"},
        {"embedding_dimension": "3"},
        {"content": "Changed body with old hash"},
        {"content_sha256": "b" * 64},
    ],
)
async def test_cross_scope_deleted_and_stale_graph_sources_never_reach_context(settings, updates):
    target = point("neighbor", "Neighbor original evidence")
    graph_row = row(target)
    target["payload"].update(updates)
    response, calls = await run(settings, Repository([graph_row]), targets=[target])
    assert [hit.id for hit in response.hits] == ["seed"]
    assert response.graphAugmented is False and response.graphDegraded is True
    assert calls["rerank"][0]["documents"] == ["Seed original evidence"]
    assert response.degraded is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value", [("tenant", "tenant-b"), ("knowledgeBase", "kb-b"), ("sourceKey", "bad")]
)
async def test_graph_rows_recheck_scope_and_identity_before_qdrant(settings, field, value):
    target = point("neighbor", "Neighbor original evidence")
    graph_row = row(target)
    graph_row[field] = value
    response, calls = await run(settings, Repository([graph_row]), targets=[target])
    assert [hit.id for hit in response.hits] == ["seed"] and response.graphDegraded is True
    assert calls["reread"] == []


@pytest.mark.asyncio
async def test_missing_qdrant_document_is_graph_degraded_without_rerank_degradation(settings):
    response, _ = await run(settings, Repository([row(point("neighbor", "Gone evidence"))]))
    assert [hit.id for hit in response.hits] == ["seed"]
    assert response.graphDegraded is True and response.degraded is False


@pytest.mark.asyncio
async def test_graph_delete_barrier_during_qdrant_reread_cannot_extend_retired_source(settings):
    target = point("neighbor", "Retired graph while vector still ACTIVE")

    class RetiringRepository(Repository):
        async def rows(self, query, **params):
            values = await super().rows(query, **params)
            return values if len(self.calls) == 1 else []

    response, calls = await run(settings, RetiringRepository([row(target)]), targets=[target])
    assert [hit.id for hit in response.hits] == ["seed"]
    assert response.graphDegraded is True
    assert calls["rerank"][0]["documents"] == ["Seed original evidence"]


@pytest.mark.asyncio
async def test_empty_vector_results_do_not_trigger_global_graph_walk(settings):
    repository = Repository([row(point("neighbor", "Unrelated"))])
    response, calls = await run(settings, repository, seeds=[])
    assert response.hits == [] and repository.calls == []
    assert calls["reread"] == [] and calls["rerank"] == []
    assert response.graphAugmented is False and response.graphDegraded is False


@pytest.mark.asyncio
async def test_graph_budget_is_independent_bounded_and_cancels_pending_query(settings):
    repository = Repository(delay=2)
    started = time.monotonic()
    response, calls = await run(settings, repository)
    assert time.monotonic() - started < 0.7
    assert repository.cancelled is True
    assert [hit.id for hit in response.hits] == ["seed"] and response.graphDegraded is True
    assert calls["rerank"][0]["documents"] == ["Seed original evidence"]


@pytest.mark.asyncio
async def test_outer_request_cancellation_is_not_swallowed_by_optional_graph(settings):
    repository = Repository(delay=2)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await run(settings, repository)
    assert repository.cancelled is True


@pytest.mark.asyncio
async def test_graph_unavailable_keeps_original_candidates_and_reports_graph_only_degradation(settings):
    for repository in (None, Repository(fail=True)):
        response, _ = await run(settings, repository)
        assert [hit.id for hit in response.hits] == ["seed"]
        assert response.graphDegraded is True and response.degraded is False


@pytest.mark.asyncio
async def test_graph_candidates_share_rrf_budget_and_original_rerank_threshold(settings):
    targets = [point("neighbor-" + str(index), "Neighbor " + str(index)) for index in range(20)]
    repository = Repository([row(target) for target in targets])
    request = RetrievalRequest(query="synthetic query", knowledgeBase="kb-a", topK=3, candidateCount=3)
    response, calls = await run(settings, repository, targets=targets, request=request, rerank=False)
    assert len(calls["rerank"][0]["documents"]) == 3
    assert len(response.hits) == 3 and response.graphAugmented is True
    assert response.hits[0].score == pytest.approx(1 / 61)
    response, _ = await run(settings, repository, targets=targets, request=request, score=0.1)
    assert response.hits == [] and response.degraded is False


@pytest.mark.asyncio
async def test_disabled_flag_keeps_existing_retrieval_without_graph_requests(settings):
    calls, transport = upstream(settings, [point("seed", "Seed original evidence")])
    settings.graph_retrieval_enabled = False
    repository = Repository(fail=True)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await retrieve(
            RetrievalRequest(query="synthetic query", knowledgeBase="kb-a"),
            "tenant-a",
            settings,
            client,
            graph_repository=repository,
        )
    assert [hit.id for hit in response.hits] == ["seed"] and repository.calls == []
    assert response.graphAugmented is False and response.graphDegraded is False
    assert calls["reread"] == []


@pytest.mark.asyncio
async def test_graph_extension_runs_after_dense_sparse_rrf_and_before_rerank(settings):
    from test_hybrid import schema

    settings.hybrid_enabled = True
    settings.graph_retrieval_enabled = True
    seed = point("seed", "Hybrid original evidence", sparse_model=SPARSE_MODEL)
    target = point("neighbor", "Graph related original evidence", sparse_model=SPARSE_MODEL)
    documents = []
    search_modes = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=schema())
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [1, 0, 0]}]})
        if request.url.path.endswith("/count"):
            return httpx.Response(200, json={"result": {"count": 0}})
        if request.url.path.endswith("/search"):
            search_modes.append("sparse" if isinstance(body["vector"], dict) else "dense")
            return httpx.Response(200, json={"result": [seed]})
        if request.url.path.endswith("/points"):
            return httpx.Response(200, json={"result": [target]})
        if request.url.path.endswith("/reranks"):
            documents.extend(body["documents"])
            return httpx.Response(
                200, json={"results": [{"index": index, "relevance_score": 0.9} for index in range(2)]}
            )
        raise AssertionError(request.url)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await retrieve(
            RetrievalRequest(query="synthetic query", knowledgeBase="kb-a", topK=2),
            "tenant-a",
            settings,
            client,
            graph_repository=Repository([row(target)]),
        )
    assert set(search_modes) == {"dense", "sparse"}
    assert set(documents) == {"Hybrid original evidence", "Graph related original evidence"}
    assert response.mode == "hybrid" and response.graphAugmented is True
    assert response.graphDegraded is False and response.degraded is False
