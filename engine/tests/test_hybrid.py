import asyncio
import json
import math

import httpx
import pytest
from conftest import auth, parse_payload
from fastapi.testclient import TestClient
from test_api import hit, retrieval_body

from rag_engine.app import create_app
from rag_engine.errors import EngineError
from rag_engine.lexical import SPARSE_MODEL, encode_sparse
from rag_engine.models import ParseRequest
from rag_engine.parsing import ocr_blocks, split_blocks
from rag_engine.sparse_index import SparseIndex


def schema(sparse=True):
    return {
        "result": {
            "config": {
                "params": {
                    "vectors": {"size": 3, "distance": "Cosine"},
                    "sparse_vectors": {SPARSE_MODEL: {"modifier": "idf"}} if sparse else {},
                }
            }
        }
    }


def hybrid_hit(point_id, content, score=0.8, tenant="tenant-a"):
    value = hit(tenant)
    value["id"] = point_id
    value["score"] = score
    value["payload"].update(content=content, sparse_model=SPARSE_MODEL)
    value["payload"].pop("content_sha256", None)
    return value


def test_sparse_encoding_multilingual_and_normalization():
    assert encode_sparse("ＡＢＣ 中医 ABC") == encode_sparse("abc 中医 abc")
    vector = encode_sparse("abc 中医 abc")
    assert len(vector["indices"]) == 4  # w:abc / c:中 / c:医 / b:中医
    assert vector["indices"] == sorted(set(vector["indices"]))
    assert all(0 <= value < 2**32 for value in vector["indices"])
    assert sum(value**2 for value in vector["values"]) == pytest.approx(1)
    assert encode_sparse("🚀!? ") == {"indices": [], "values": []}


def hybrid_upstream(settings, *, sparse_hits=None, missing=0, no_schema=False, rerank=True):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=schema(not no_schema))
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [1, 0, 0]}]})
        if request.url.path.endswith("/count"):
            assert body["exact"] is True
            assert {"has_vector": SPARSE_MODEL} in body["filter"]["must_not"][0]["must"]
            return httpx.Response(200, json={"result": {"count": missing}})
        if request.url.path.endswith("/search"):
            assert body["filter"]["must"][0] == {"key": "tenant_id", "match": {"value": "tenant-a"}}
            if isinstance(body["vector"], dict):
                assert body["vector"]["name"] == SPARSE_MODEL
                assert body["vector"]["vector"] == encode_sparse("synthetic question")
                assert "score_threshold" not in body
                points = (
                    sparse_hits
                    if sparse_hits is not None
                    else [
                        hybrid_hit("sparse-only", "精确词 sparse only", 3),
                        hybrid_hit("both", "共同命中 both", 2),
                    ]
                )
            else:
                points = [
                    hybrid_hit("dense-only", "语义命中 dense only", 0.9),
                    hybrid_hit("both", "共同命中 both", 0.8),
                ]
            return httpx.Response(200, json={"result": points})
        if not rerank:
            return httpx.Response(503, text="private upstream message")
        assert body["documents"][0] == "共同命中 both"
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]})

    return handler


def test_hybrid_rrf_includes_sparse_only_candidates_and_keeps_rrf_on_rerank_failure(settings):
    settings.hybrid_enabled = True
    body = retrieval_body() | {"topK": 3}
    with TestClient(
        create_app(settings, httpx.MockTransport(hybrid_upstream(settings, rerank=False)))
    ) as client:
        response = client.post("/v1/retrievals", json=body, headers=auth(settings))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["mode"] == "hybrid" and result["degraded"] is True
    assert [item["id"] for item in result["hits"]] == ["both", "dense-only", "sparse-only"]
    assert result["hits"][0]["score"] == pytest.approx(2 / 62)


def test_hybrid_rerank_runs_after_fusion(settings):
    settings.hybrid_enabled = True
    with TestClient(create_app(settings, httpx.MockTransport(hybrid_upstream(settings)))) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
    assert response.status_code == 200, response.text
    assert response.json()["hits"][0]["id"] == "both"
    assert response.json()["mode"] == "hybrid" and response.json()["degraded"] is False


@pytest.mark.parametrize(
    "options,code",
    [
        ({"missing": 1}, "SPARSE_INDEX_INCOMPLETE"),
        ({"no_schema": True}, "SPARSE_SCHEMA_NOT_READY"),
    ],
)
def test_hybrid_never_claims_incomplete_collection_is_ready(settings, options, code):
    settings.hybrid_enabled = True
    with TestClient(
        create_app(settings, httpx.MockTransport(hybrid_upstream(settings, **options)))
    ) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
    assert response.status_code == 503
    assert response.json() == {"error": {"code": code}}


def test_sparse_leg_rechecks_tenant_even_when_dense_leg_is_valid(settings):
    settings.hybrid_enabled = True
    foreign = hybrid_hit("foreign", "private document", tenant="tenant-b")
    with TestClient(
        create_app(settings, httpx.MockTransport(hybrid_upstream(settings, sparse_hits=[foreign])))
    ) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
    assert response.status_code == 502
    assert response.json() == {"error": {"code": "TENANT_ISOLATION_VIOLATION"}}
    assert "private" not in response.text


@pytest.mark.asyncio
async def test_fusion_cancels_other_retriever_on_failure():
    from llama_index.core.base.base_retriever import BaseRetriever

    from rag_engine.retrievers import ReciprocalRankRetriever

    cancelled = asyncio.Event()

    class Retriever(BaseRetriever):
        def __init__(self, fail):
            super().__init__()
            self.fail = fail

        def _retrieve(self, _query):
            raise AssertionError("must be async")

        async def _aretrieve(self, query_bundle):
            if self.fail:
                await asyncio.sleep(0.01)
                raise EngineError("TENANT_ISOLATION_VIOLATION")
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

    with pytest.raises(EngineError, match="TENANT_ISOLATION_VIOLATION"):
        await ReciprocalRankRetriever([Retriever(True), Retriever(False)], 10).aretrieve("safe query")
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_backfill_consumes_every_page_and_preserves_dense(settings):
    pages = 0
    writes = []

    def handler(request):
        nonlocal pages
        if request.method == "GET":
            return httpx.Response(200, json=schema())
        body = json.loads(request.content)
        if request.url.path.endswith("/scroll"):
            start = body.get("offset", 0)
            points = []
            for index in range(start, min(start + 64, 150)):
                point = hybrid_hit(index, f"合成内容 synthetic {index}")
                point["vector"] = {"": [1, 0, 0]}
                points.append(point)
            pages += 1
            return httpx.Response(
                200,
                json={
                    "result": {
                        "points": points,
                        "next_page_offset": start + 64 if start + 64 < 150 else None,
                    }
                },
            )
        if request.url.path.endswith("/vectors"):
            assert all(set(point["vector"]) == {SPARSE_MODEL} for point in body["points"])
            writes.extend(body["points"])
        elif request.url.path.endswith("/count"):
            count = 0 if "must_not" in body["filter"] else 150
            return httpx.Response(200, json={"result": {"count": count}})
        else:
            assert body["payload"] == {"sparse_model": SPARSE_MODEL}
        return httpx.Response(200, json={"result": {"status": "completed"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await SparseIndex(settings, client).backfill("tenant-a", "general", True)
    assert pages == 3 and len(writes) == 150
    assert result == {"inspected": 150, "written": 150, "totalActive": 150, "missing": 0, "complete": True}


@pytest.mark.asyncio
async def test_backfill_never_marks_truncated_scroll_complete(settings):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=schema())
        if request.url.path.endswith("/scroll"):
            return httpx.Response(200, json={"result": {"points": [], "next_page_offset": None}})
        return httpx.Response(200, json={"result": {"count": 5}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EngineError, match="SPARSE_BACKFILL_NOT_COMPLETE"):
            await SparseIndex(settings, client).backfill("tenant-a", "general", True)


def test_ocr_capabilities_survive_chunking(settings):
    capabilities = {"textRecognition": True, "layoutAnalysis": False, "tableStructure": False}
    blocks = ocr_blocks(
        {
            "result": {
                "capabilities": capabilities,
                "layoutParsingResults": [
                    {
                        "prunedResult": {
                            "page_index": 0,
                            "parsing_res_list": [{"block_content": "合同第1页", "block_label": "ocr-text"}],
                        }
                    }
                ],
            }
        },
        1,
    )
    chunks = split_blocks(blocks, ParseRequest(**parse_payload()), settings)
    assert chunks[0].metadata["ocr_capabilities"] == capabilities
    assert chunks[0].metadata["block_type"] == "ocr-text"
    assert math.isfinite(encode_sparse(chunks[0].content)["values"][0])


def test_sparse_encoding_matches_shared_java_contract_fixture():
    from pathlib import Path

    fixture = json.loads(Path(__file__).with_name("fixtures").joinpath("lexical-v1.json").read_text())
    assert fixture["model"] == SPARSE_MODEL
    for case in fixture["cases"]:
        actual = encode_sparse(case["text"])
        assert actual["indices"] == case["indices"]
        assert actual["values"] == pytest.approx(case["values"], abs=1e-12)


def test_hybrid_deadline_cancels_two_slow_legs(settings):
    settings.hybrid_enabled = True
    settings.retrieval_timeout_seconds = 0.1
    cancelled = []
    ordinary = hybrid_upstream(settings)

    async def upstream(request):
        if request.url.path.endswith("/search"):
            body = json.loads(request.content)
            try:
                await asyncio.sleep(1)
            finally:
                cancelled.append(isinstance(body["vector"], dict))
        return ordinary(request)

    with TestClient(create_app(settings, httpx.MockTransport(upstream))) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
    assert response.status_code == 504
    assert sorted(cancelled) == [False, True]


def test_hybrid_rejects_conflicting_same_point_payloads(settings):
    settings.hybrid_enabled = True
    sparse_hits = [hybrid_hit("both", "concurrently overwritten content", 1)]
    with TestClient(
        create_app(settings, httpx.MockTransport(hybrid_upstream(settings, sparse_hits=sparse_hits)))
    ) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
    assert response.status_code == 502
    assert response.json() == {"error": {"code": "VECTOR_INCONSISTENT_RESPONSE"}}


def test_no_lexical_query_terms_returns_explicit_dense_degradation(settings):
    settings.hybrid_enabled = True
    with TestClient(
        create_app(settings, httpx.MockTransport(hybrid_upstream(settings, rerank=False)))
    ) as client:
        response = client.post(
            "/v1/retrievals", json=retrieval_body() | {"query": "🚀"}, headers=auth(settings)
        )
    assert response.status_code == 200
    assert response.json()["mode"] == "dense" and response.json()["degraded"] is True


@pytest.mark.asyncio
async def test_backfill_apply_requires_explicit_paused_writer_acknowledgement(settings, monkeypatch):
    from argparse import Namespace

    from rag_engine import sparse_index

    monkeypatch.setattr(sparse_index, "Settings", lambda: settings)
    arguments = Namespace(
        command="backfill", apply=True, writers_paused=False, tenant="tenant-a", knowledge_base="general"
    )
    with pytest.raises(EngineError, match="WRITERS_PAUSED_CONFIRMATION_REQUIRED"):
        await sparse_index.run_cli(arguments)


@pytest.mark.asyncio
async def test_prepare_dry_run_never_mutates_schema(settings):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json=schema(False))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await SparseIndex(settings, client).prepare(False)
    assert calls == ["GET"]
    assert result == {"sparseModel": SPARSE_MODEL, "schemaReady": False, "applied": False}
