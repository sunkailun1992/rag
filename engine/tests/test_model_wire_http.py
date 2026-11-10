"""真实回环 HTTP 合同；全部文本/凭据为测试替身，不连接供应商或运行库。"""

import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import auth
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_api import hit, retrieval_body

from rag_engine.app import create_app
from rag_engine.config import Settings


@pytest.fixture
def upstream_http():
    class State:
        calls = []
        embedding = {"data": [{"index": 0, "embedding": [1, 2, 3]}]}
        ranking = {"results": [{"index": 0, "relevance_score": 0.9}]}
        ranking_status = 200

    state = State()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.calls.append((self.command, self.path, body))
            status = 200
            if self.path.endswith("/embeddings"):
                response = state.embedding
            elif self.path.endswith("/search"):
                response = {"result": [hit()]}
            else:
                response, status = state.ranking, state.ranking_status
            encoded = json.dumps(response).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def configured(settings, upstream, **extra):
    return Settings(
        **{
            **settings.model_dump(),
            "qwen_base_url": upstream.base + "/compatible-mode/v1",
            "qdrant_url": upstream.base,
            **extra,
        }
    )


def test_default_dashscope_wire_keeps_the_existing_path_and_request(settings, upstream_http):
    configured_settings = configured(settings, upstream_http)
    with TestClient(create_app(configured_settings)) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
    assert response.status_code == 200 and response.json()["degraded"] is False
    assert response.json()["hits"][0]["score"] == 0.9
    assert [call[1] for call in upstream_http.calls] == [
        "/compatible-mode/v1/embeddings",
        "/collections/knowledge-vectors/points/search",
        "/compatible-api/v1/reranks",
    ]
    assert upstream_http.calls[-1][2] == {
        "model": "qwen3-rerank",
        "query": "synthetic question",
        "documents": ["Synthetic knowledge"],
        "top_n": 1,
    }


def test_cohere_wire_uses_the_complete_endpoint_and_maps_results_without_echoing_documents(
    settings, upstream_http
):
    configured_settings = configured(
        settings, upstream_http, rerank_wire="cohere", rerank_endpoint=upstream_http.base + "/proxy/v1/rerank"
    )
    with TestClient(create_app(configured_settings)) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
    assert response.status_code == 200 and response.json()["degraded"] is False
    assert response.json()["hits"][0]["score"] == 0.9
    assert upstream_http.calls[-1][1] == "/proxy/v1/rerank"
    assert upstream_http.calls[-1][2] == {
        "model": "qwen3-rerank",
        "query": "synthetic question",
        "documents": ["Synthetic knowledge"],
        "top_n": 1,
        "return_documents": False,
    }
    assert len(upstream_http.calls) == 3


@pytest.mark.parametrize("status", [401, 429, 503])
def test_proxy_failure_is_typed_degraded_with_no_automatic_retry_or_secret_echo(
    settings, upstream_http, status, caplog
):
    upstream_http.ranking_status = status
    upstream_http.ranking = {"secret": "SYNTHETIC_RAW_RESPONSE_MUST_NOT_ESCAPE"}
    configured_settings = configured(
        settings, upstream_http, rerank_wire="cohere", rerank_endpoint=upstream_http.base + "/v1/rerank"
    )
    with TestClient(create_app(configured_settings)) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
    assert response.status_code == 200 and response.json()["degraded"] is True
    assert len(upstream_http.calls) == 3
    assert "SYNTHETIC_RAW_RESPONSE_MUST_NOT_ESCAPE" not in response.text + caplog.text
    assert upstream_http.base not in caplog.text


@pytest.mark.parametrize(
    "embedding",
    [
        {"data": [{"embedding": [1, 2, 3]}]},
        {"data": [{"index": False, "embedding": [1, 2, 3]}]},
        {"data": [{"index": 0.0, "embedding": [1, 2, 3]}]},
        {"data": [{"index": "0", "embedding": [1, 2, 3]}]},
        {"data": [{"index": -1, "embedding": [1, 2, 3]}]},
        {"data": [{"index": 1, "embedding": [1, 2, 3]}]},
        {"data": [{"index": 0, "embedding": [1, 2, 3]}, {"index": 0, "embedding": [1, 2, 3]}]},
        {"data": []},
        {"data": [{"index": 0, "embedding": [1, 2]}]},
        {"data": [{"index": 0, "embedding": [False, 2, 3]}]},
        {"data": [{"index": 0, "embedding": ["1", 2, 3]}]},
        {"data": [{"index": 0, "embedding": [math.nan, 2, 3]}]},
        {"data": [{"index": 0, "embedding": [math.inf, 2, 3]}]},
        {"data": [{"index": 0, "embedding": [1e308, 2, 3]}]},
    ],
)
def test_bad_embedding_stops_before_recall_or_any_vector_write(settings, upstream_http, embedding):
    upstream_http.embedding = embedding
    configured_settings = configured(settings, upstream_http)
    with TestClient(create_app(configured_settings)) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
    assert response.status_code == 502
    assert response.json() == {"error": {"code": "EMBEDDING_INVALID_RESPONSE"}}
    assert len(upstream_http.calls) == 1
    assert upstream_http.calls[0][1] == "/compatible-mode/v1/embeddings"


@pytest.mark.parametrize(
    "change",
    [
        {"rerank_wire": "unknown"},
        {"rerank_wire": "cohere"},
        {"rerank_endpoint": "http://user:synthetic-secret@model.example.invalid/v1/rerank"},
        {"rerank_endpoint": "https://model.example.invalid/v1/rerank?secret=synthetic-secret"},
    ],
)
def test_rerank_configuration_rejects_ambiguous_or_secret_bearing_targets(settings, change):
    with pytest.raises(ValidationError) as captured:
        Settings(**{**settings.model_dump(), **change})
    assert "synthetic-secret" not in str(captured.value)
