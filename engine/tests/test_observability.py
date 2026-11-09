"""观测边界只使用合成数据；并发、失败和凭据保护必须验证实际输出。"""

import asyncio
import json
import logging
import time

import httpx
import pytest
from conftest import auth
from fastapi.testclient import TestClient

from rag_engine.app import create_app


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_metrics_require_service_bearer_without_tenant(settings, headers):
    with TestClient(create_app(settings)) as client:
        assert client.get("/metrics", headers=headers).status_code == 401
        response = client.get("/metrics", headers={"Authorization": auth(settings)["Authorization"]})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "rag_engine_requests" in response.text


def test_metrics_reject_duplicate_bearer_and_use_only_bounded_labels(settings):
    with TestClient(create_app(settings)) as client:
        doubled = [("Authorization", auth(settings)["Authorization"])] * 2
        assert client.get("/metrics", headers=doubled).status_code == 401
        client.get(
            "/v1/graphs/builds/private-id", params={"knowledgeBase": "private-kb"}, headers=auth(settings)
        )
        client.get("/private-path-secret", headers=auth(settings))
        response = client.get("/metrics", headers=auth(settings))
    assert response.status_code == 200
    for secret in ("private-id", "private-kb", "private-path-secret", "tenant-a", "trace_id", "source_hash"):
        assert secret not in response.text
    assert "/v1/graphs/builds/{buildId}" in response.text


def memory_observability(settings):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from rag_engine.observability import Observability

    settings.observability_enabled = True
    settings.trace_sample_ratio = 1
    exporter = InMemorySpanExporter()
    return Observability(settings, exporter=exporter), exporter


async def test_concurrent_asgi_and_httpx_contexts_preserve_trace_and_reset(settings):
    from opentelemetry import trace

    obs, exporter = memory_observability(settings)
    upstream = []
    arrived = asyncio.Event()

    async def respond(request):
        upstream.append(request.headers.get("traceparent"))
        if len(upstream) == 2:
            arrived.set()
        await asyncio.wait_for(arrived.wait(), 1)
        return httpx.Response(200, json={"data": [{"embedding": [1, 2, 3]}]})

    app = create_app(settings, httpx.MockTransport(respond), observability=obs)
    traces = ["1" * 32, "2" * 32]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            from test_api import retrieval_body

            await asyncio.gather(
                *[
                    client.post(
                        "/v1/retrievals",
                        json=retrieval_body(),
                        headers={
                            **auth(settings),
                            "traceparent": f"00-{tid}-{'3' * 16}-01",
                            "baggage": "secret=private",
                        },
                    )
                    for tid in traces
                ]
            )
        assert not trace.get_current_span().get_span_context().is_valid
    assert {value.split("-")[1] for value in upstream} == set(traces)
    spans = exporter.get_finished_spans()
    server = [span for span in spans if span.name == "POST /v1/retrievals"]
    assert {f"{span.context.trace_id:032x}" for span in server} == set(traces)
    assert all(span.parent.span_id == int("3" * 16, 16) for span in server)
    assert all(not span.events for span in spans)


async def test_sensitive_exception_url_headers_and_payload_never_exported(settings, caplog):
    from rag_engine.observability import ObservedTransport

    obs, exporter = memory_observability(settings)
    private = "private-clinical-prompt-secret"

    def fail(request):
        assert "baggage" not in request.headers
        raise httpx.ConnectError(private, request=request)

    with caplog.at_level(logging.INFO, logger="rag_engine.events"):
        with obs.activate(), obs.operation("retrieval"):
            async with httpx.AsyncClient(
                transport=ObservedTransport(httpx.MockTransport(fail), obs)
            ) as client:
                with pytest.raises(httpx.ConnectError):
                    await client.post(
                        f"https://user:{private}@example.test/{private}?key={private}",
                        headers={"Authorization": private},
                        json={"prompt": private},
                    )
    obs.shutdown()
    spans = exporter.get_finished_spans()
    rendered = json.dumps(
        [
            {
                "name": span.name,
                "attributes": dict(span.attributes),
                "events": [str(event) for event in span.events],
                "status": span.status.description,
            }
            for span in spans
        ]
    )
    assert private not in rendered + caplog.text
    assert "example.test" not in rendered
    events = [
        json.loads(record.getMessage()) for record in caplog.records if record.name == "rag_engine.events"
    ]
    assert events and all("trace_id" in event and "span_id" in event for event in events)


async def test_unreachable_collector_does_not_block_requests(settings):
    from rag_engine.observability import Observability

    settings.observability_enabled = True
    settings.otlp_traces_endpoint = "http://127.0.0.1:1/v1/traces"
    settings.trace_sample_ratio = 1
    settings.otlp_timeout_seconds = 0.05
    obs = Observability(settings)
    app = create_app(settings, observability=obs)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            started = time.monotonic()
            for _ in range(20):
                assert (await client.get("/health")).status_code == 200
            assert time.monotonic() - started < 1


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "timeout", "success"])
async def test_parent_build_terminal_span_and_metric_with_submission_link(settings, monkeypatch, outcome):
    from opentelemetry import trace
    from prometheus_client import generate_latest

    from rag_engine import graph_jobs
    from rag_engine.errors import EngineError
    from rag_engine.graph_models import GraphBuildRequest

    obs, exporter = memory_observability(settings)
    job = {
        "buildId": "private-build",
        "tenant": "private-tenant",
        "knowledgeBase": "general",
        "requestJson": '{"knowledgeBase":"general","activeSources":[]}',
    }

    class Repository:
        claimed = False

        async def submit(self, *_args):
            from types import SimpleNamespace

            return SimpleNamespace(buildId="private-build", status="QUEUED")

        async def claim(self):
            if self.claimed:
                raise asyncio.CancelledError()
            self.claimed = True
            return {"job": job, "remaining": 1}

        async def publish(self, *_args):
            pass

        async def finish_failed(self, *_args, **_kwargs):
            raise RuntimeError("private-store-error")

        async def cleanup(self, *_args):
            pass

    async def worker(*_args):
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        if outcome == "timeout":
            raise TimeoutError("private-timeout")
        if outcome == "failed":
            raise EngineError("GRAPH_BUILD_FAILED")
        return {"processedDocumentCount": 1, "nodeCount": 2, "edgeCount": 1}

    async def sources(*_args):
        return []

    monkeypatch.setattr(graph_jobs, "run_worker", worker)
    monkeypatch.setattr(graph_jobs, "read_active_sources", sources)
    manager = graph_jobs.GraphManager(settings, None, Repository(), observability=obs)
    with obs.activate(), obs.operation("graph_submit"):
        submission = trace.get_current_span().get_span_context()
        await manager.submit("private-tenant", GraphBuildRequest(knowledgeBase="general", activeSources=[]))
    with pytest.raises(asyncio.CancelledError):
        await manager.pump()
    obs.shutdown()
    spans = [span for span in exporter.get_finished_spans() if span.name == "rag.graph_build"]
    assert len(spans) == 1
    assert spans[0].parent is None
    assert spans[0].context.trace_id != submission.trace_id
    assert spans[0].links[0].context == submission
    assert spans[0].attributes["rag.result"] == outcome
    metrics = generate_latest(obs.registry).decode()
    assert f'rag_engine_graph_builds_total{{result="{outcome}"}} 1.0' in metrics
    assert "private-" not in metrics + str(spans[0].attributes)


def test_runtime_diagnostics_are_json_and_unreviewed_messages_are_dropped(settings, caplog):
    app = create_app(settings)
    private = "private-exception-body"
    with caplog.at_level(logging.INFO):
        with TestClient(app) as client:
            assert (
                client.post(
                    "/v1/parses",
                    json={"sourceName": "a.pdf", "title": "T", "contentBase64": "!"},
                    headers=auth(settings),
                ).status_code
                == 422
            )
        logging.getLogger("rag_engine").warning("unreviewed %s", private, exc_info=RuntimeError(private))
    records = [record for record in caplog.records if record.name in {"rag_engine", "rag_engine.events"}]
    assert records
    assert all(isinstance(json.loads(record.getMessage()), dict) for record in records)
    assert private not in caplog.text


def test_http_200_degradation_and_empty_results_have_business_metrics(settings):
    from test_api import hit, retrieval_body

    def upstream(request):
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [1, 2, 3]}]})
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"result": [hit()]})
        return httpx.Response(503, text="private-response")

    with TestClient(create_app(settings, httpx.MockTransport(upstream))) as client:
        assert client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings)).json()["degraded"]
        metrics = client.get("/metrics", headers=auth(settings)).text
    assert 'rag_engine_requests_total{method="POST",result="degraded",route="/v1/retrievals"} 1.0' in metrics
    assert 'rag_engine_retrievals_total{mode="dense",result="degraded"} 1.0' in metrics
    assert 'stage="embedding"' in metrics and 'stage="recall"' in metrics and 'stage="rerank"' in metrics


async def test_disabled_sdk_preserves_w3c_propagation_and_does_not_replace_global_provider(settings):
    from opentelemetry import trace

    from rag_engine.observability import Observability, ObservedTransport

    before = trace.get_tracer_provider()
    obs = Observability(settings)
    assert obs.provider is None
    with obs.activate(), obs.operation("parse"):
        async with httpx.AsyncClient(
            transport=ObservedTransport(httpx.MockTransport(lambda _: httpx.Response(200)), obs)
        ) as client:
            await client.get("https://example.test")
    assert trace.get_tracer_provider() is before


async def test_asgi_failure_resets_context_and_exports_no_raw_exception(settings, caplog):
    from opentelemetry import trace

    from rag_engine.observability import ObservationBoundary

    obs, exporter = memory_observability(settings)

    async def fail(_scope, _receive, _send):
        raise ValueError("private-asgi-exception")

    middleware = ObservationBoundary(fail, obs)

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(_message):
        pass

    with pytest.raises(ValueError):
        await middleware(
            {
                "type": "http",
                "path": "/private-path",
                "method": "SECRET-METHOD",
                "headers": [(b"traceparent", b"invalid-secret"), (b"authorization", b"private-header")],
                "query_string": b"query=private-query",
            },
            receive,
            send,
        )
    assert not trace.get_current_span().get_span_context().is_valid
    obs.shutdown()
    spans = exporter.get_finished_spans()
    assert len(spans) == 1 and spans[0].name == "OTHER UNMATCHED"
    assert spans[0].events == ()
    assert spans[0].status.description is None
    assert "private" not in str(spans[0].attributes) + caplog.text


def test_dependency_debug_logs_do_not_include_urls_or_exception_text(settings, caplog):
    create_app(settings)
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("httpcore.connection").debug("connect to %s", "private-url-secret")
        logging.getLogger("urllib3.connectionpool").warning("private-collector-secret")
    assert "private-" not in caplog.text


def test_empty_otlp_endpoint_is_rejected_before_exporter_env_fallback(settings):
    from pydantic import ValidationError

    from rag_engine.config import Settings

    with pytest.raises(ValidationError):
        Settings(api_key=settings.api_key, otlp_traces_endpoint="")


def test_malformed_logging_call_cannot_expose_args_or_break_request(settings, caplog):
    create_app(settings)
    with caplog.at_level(logging.WARNING, logger="rag_engine"):
        logging.getLogger("rag_engine").warning("rag_engine_failed code=%s status=%d", "INVALID_REQUEST")
        logging.getLogger("rag_engine").warning(42, "private-log-value")
    assert all(json.loads(record.getMessage())["event"] == "rag_diagnostic" for record in caplog.records)
    assert "private-log-value" not in caplog.text


@pytest.mark.parametrize("committed", [False, True])
async def test_queue_expiry_reports_terminal_only_after_transaction_commit(settings, monkeypatch, committed):
    from contextlib import asynccontextmanager

    from rag_engine.graph_jobs import GraphManager
    from rag_engine.graph_repository import GraphRepository

    obs, exporter = memory_observability(settings)
    repository = object.__new__(GraphRepository)
    repository.owner = "private-owner"
    manager = GraphManager(settings, None, repository, observability=obs)
    with obs.operation("graph_submit"):
        from opentelemetry import trace

        manager.submission_contexts["private-expired"] = trace.get_current_span().get_span_context()

    class Result:
        async def consume(self):
            return None

        async def data(self):
            return [{"buildId": "private-expired"}]

        async def single(self):
            return None

    class Transaction:
        async def run(self, *_args, **_kwargs):
            return Result()

    @asynccontextmanager
    async def transaction():
        yield Transaction()
        if not committed:
            raise RuntimeError("private-commit-error")

    async def lock(_tx):
        pass

    monkeypatch.setattr(repository, "transaction", transaction)
    monkeypatch.setattr(repository, "lock", lock)
    if committed:
        assert await repository.claim() is None
    else:
        with pytest.raises(RuntimeError):
            await repository.claim()
    obs.shutdown()
    terminal = [span for span in exporter.get_finished_spans() if span.name == "rag.graph_build"]
    assert len(terminal) == int(committed)
    if committed:
        assert terminal[0].attributes["rag.result"] == "timeout"
        assert not manager.submission_contexts


def test_default_runtime_events_use_stderr_json_without_stdout_protocol_noise():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from rag_engine.config import Settings
from rag_engine.observability import Observability
obs = Observability(Settings(api_key="x"*32))
with obs.activate(), obs.operation("parse"):
    pass
obs.shutdown()
""",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert result.stdout == ""
    event = json.loads(result.stderr.strip())
    assert event["event"] == "rag_operation_finished" and event["result"] == "success"


def test_real_otlp_http_export_is_metadata_only_with_explicit_resources(settings, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    from rag_engine.observability import Observability

    captured = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            captured.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "prompt=private-resource-body")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        settings.observability_enabled = True
        settings.trace_sample_ratio = 1
        settings.otlp_traces_endpoint = f"http://127.0.0.1:{server.server_port}/v1/traces"
        obs = Observability(settings)
        with obs.activate(), obs.operation("parse"):
            pass
        assert obs.provider.force_flush(timeout_millis=1000)
        obs.shutdown()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
    assert len(captured) == 1
    assert b"private-resource-body" not in captured[0]
    message = ExportTraceServiceRequest.FromString(captured[0])
    resource = message.resource_spans[0]
    assert {attribute.key for attribute in resource.resource.attributes} == {
        "service.name",
        "service.version",
        "deployment.environment.name",
    }
    span = resource.scope_spans[0].spans[0]
    assert span.name == "rag.parse" and not span.events
    assert {attribute.key for attribute in span.attributes} == {"rag.stage", "rag.result"}


def test_invalid_rerank_response_is_a_failed_stage_and_degraded_business_result(settings):
    from test_api import hit, retrieval_body

    def upstream(request):
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [1, 2, 3]}]})
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"result": [hit()]})
        return httpx.Response(200, json={"results": [{"index": 999, "relevance_score": 0.9}]})

    with TestClient(create_app(settings, httpx.MockTransport(upstream))) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
        metrics = client.get("/metrics", headers=auth(settings)).text
    assert response.status_code == 200 and response.json()["degraded"]
    assert 'rag_engine_stage_duration_seconds_count{result="failed",stage="rerank"} 1.0' in metrics


def test_graph_degradation_is_observed_even_when_dense_retrieval_succeeds(settings, monkeypatch):
    from test_api import retrieval_body

    from rag_engine import app as app_module
    from rag_engine.models import RetrievalResponse

    async def retrieve(*_args, **_kwargs):
        return RetrievalResponse(hits=[], mode="dense", degraded=False, graphDegraded=True)

    monkeypatch.setattr(app_module, "retrieve", retrieve)
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
        metrics = client.get("/metrics", headers=auth(settings)).text
    assert response.status_code == 200
    assert 'rag_engine_requests_total{method="POST",result="degraded",route="/v1/retrievals"} 1.0' in metrics


async def test_sdk_shuts_down_when_graph_shutdown_fails(settings, monkeypatch):
    from pydantic import SecretStr

    from rag_engine import app as app_module

    obs, exporter = memory_observability(settings)
    settings.graph_enabled = True
    settings.neo4j_url = "bolt://synthetic.example.test:7687"
    settings.neo4j_password = SecretStr("synthetic")

    class Manager:
        def __init__(self, *_args, **_kwargs):
            pass

        async def start(self):
            pass

        async def stop(self):
            raise RuntimeError("synthetic shutdown failure")

    monkeypatch.setattr(app_module, "GraphManager", Manager)
    app = create_app(settings, observability=obs)
    with pytest.raises(RuntimeError):
        async with app.router.lifespan_context(app):
            with obs.operation("parse"):
                pass
    assert exporter.get_finished_spans()


def test_all_runtime_event_families_have_utc_timestamp_and_single_json_line(settings, caplog):
    from datetime import datetime

    app = create_app(settings)
    with caplog.at_level(logging.INFO):
        with TestClient(app):
            with app.state.observability.operation("parse"):
                pass
        logging.getLogger("rag_engine").warning("rag_graph_build_failed code=%s", "GRAPH_BUILD_FAILED")
        logging.getLogger("urllib3.connectionpool").warning("private-dependency-body")
    records = [record for record in caplog.records if record.name.startswith(("rag_engine", "urllib3"))]
    assert len(records) >= 3
    for record in records:
        line = record.getMessage()
        value = json.loads(line)
        assert "\n" not in line
        assert value["@timestamp"].endswith("Z")
        assert datetime.fromisoformat(value["@timestamp"]).utcoffset().total_seconds() == 0


def test_mapping_log_argument_cannot_bypass_approved_template(settings, caplog):
    create_app(settings)
    with caplog.at_level(logging.WARNING):
        logging.getLogger("rag_engine").warning("rag_graph_build_failed code=%s", {"body": "private-body"})
    assert "private-body" not in caplog.text
