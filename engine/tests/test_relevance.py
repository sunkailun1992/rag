"""无答案问题不能因为向量有近邻就固定返回 topK；成功重排与故障降级分开验收。"""

import json

import httpx
import pytest
from conftest import auth
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_api import hit, retrieval_body
from test_hybrid import hybrid_upstream

from rag_engine.app import create_app
from rag_engine.config import Settings


def dense_upstream(scores):
    def handler(request):
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0, 0]}]})
        if request.url.path.endswith("/search"):
            points = []
            for index in range(len(scores)):
                point = hit()
                point["id"] = str(index)
                point["payload"]["content"] = f"合成的糖尿病血糖监测指南第{index}节"
                point["payload"].pop("content_sha256", None)
                points.append(point)
            return httpx.Response(200, json={"result": points})
        body = json.loads(request.content)
        assert body["query"] == "月球基地的年度租赁费用是多少？"
        return httpx.Response(
            200,
            json={
                "results": [{"index": index, "relevance_score": score} for index, score in enumerate(scores)]
            },
        )

    return handler


def test_unanswerable_question_returns_no_hits_when_reranker_rejects_all_nearest_neighbors(settings):
    body = retrieval_body() | {"query": "月球基地的年度租赁费用是多少？", "topK": 3}
    with TestClient(create_app(settings, httpx.MockTransport(dense_upstream([0.01, 0.28, 0.48])))) as client:
        response = client.post("/v1/retrievals", json=body, headers=auth(settings))
    assert response.status_code == 200, response.text
    assert response.json() == {"hits": [], "mode": "dense", "degraded": False}


def test_rerank_filter_preserves_confident_and_exact_threshold_hits_without_topk_padding(settings):
    settings.rerank_score_threshold = 0.6
    body = retrieval_body() | {"query": "月球基地的年度租赁费用是多少？", "topK": 3}
    with TestClient(create_app(settings, httpx.MockTransport(dense_upstream([0.599, 0.6, 0.95])))) as client:
        response = client.post("/v1/retrievals", json=body, headers=auth(settings))
    assert response.status_code == 200, response.text
    assert [(value["id"], value["score"]) for value in response.json()["hits"]] == [("2", 0.95), ("1", 0.6)]
    assert response.json()["degraded"] is False


def test_hybrid_low_rerank_score_is_rejected_even_when_rrf_candidate_was_first(settings):
    settings.hybrid_enabled = True
    ordinary = hybrid_upstream(settings)

    def handler(request):
        if request.url.path.endswith("/reranks"):
            return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.03}]})
        return ordinary(request)

    with TestClient(create_app(settings, httpx.MockTransport(handler))) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
    assert response.status_code == 200
    assert response.json() == {"hits": [], "mode": "hybrid", "degraded": False}


def test_hybrid_rerank_failure_keeps_rrf_candidates_without_applying_relevance_threshold(settings):
    settings.hybrid_enabled = True
    settings.rerank_score_threshold = 0.95
    with TestClient(
        create_app(settings, httpx.MockTransport(hybrid_upstream(settings, rerank=False)))
    ) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "hybrid" and body["degraded"] is True
    assert body["hits"][0]["id"] == "both"
    assert body["hits"][0]["score"] == pytest.approx(2 / 62)
    assert body["hits"][0]["score"] < settings.rerank_score_threshold


@pytest.mark.parametrize("bad_score", [-0.1, 1.0001, True])
def test_invalid_relevance_score_is_protocol_failure_not_successful_relevance_filter(settings, bad_score):
    body = retrieval_body() | {"query": "月球基地的年度租赁费用是多少？"}
    with TestClient(create_app(settings, httpx.MockTransport(dense_upstream([bad_score])))) as client:
        response = client.post("/v1/retrievals", json=body, headers=auth(settings))
    assert response.status_code == 200
    assert response.json()["degraded"] is True
    assert response.json()["hits"][0]["score"] == 0.8


@pytest.mark.parametrize("threshold", [-0.01, 1.01, float("inf"), float("nan")])
def test_relevance_threshold_rejects_invalid_configuration(settings, threshold):
    with pytest.raises(ValidationError):
        Settings(api_key=settings.api_key, rerank_score_threshold=threshold)


def test_default_calibrated_threshold_rejects_negative_048_and_keeps_positive_0836(settings):
    # 来自真实千问重排对合成题库的校准分布；协议回归不代替业务质量验收。
    assert settings.rerank_score_threshold == 0.5
    body = retrieval_body() | {"query": "月球基地的年度租赁费用是多少？", "topK": 2}
    transport = httpx.MockTransport(dense_upstream([0.48, 0.836]))
    with TestClient(create_app(settings, transport)) as client:
        response = client.post("/v1/retrievals", json=body, headers=auth(settings))
    assert response.status_code == 200
    assert [(hit["id"], hit["score"]) for hit in response.json()["hits"]] == [("1", 0.836)]
    assert response.json()["degraded"] is False
