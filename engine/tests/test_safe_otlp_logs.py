"""只导出合成安全事件；真实SDK/ASGI和loopback protobuf，不调用业务或外部模型。"""

import asyncio
import inspect
import json
import logging
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from types import SimpleNamespace

import pytest
from opentelemetry import _logs, trace
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from rag_engine.observability import Observability, ObservationBoundary, emit_safe_event


def memory(settings, log_exporter=None):
    settings.observability_enabled = True
    spans = InMemorySpanExporter()
    logs = log_exporter if log_exporter is not None else InMemoryLogRecordExporter()
    return Observability(settings, exporter=spans, log_exporter=logs), spans, logs


async def exchange(observation, secret=b"SYNTHETIC_PRIVATE_BODY"):
    async def app(scope, receive, send):
        scope["route"] = SimpleNamespace(path="/v1/retrievals")
        with observation.operation("retrieval"):
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": secret, "more_body": False})

    async def receive():
        return {"type": "http.request", "body": secret, "more_body": False}

    async def send(message):
        pass

    await ObservationBoundary(app, observation)(
        {
            "type": "http",
            "method": "POST",
            "path": "/SYNTHETIC_PRIVATE_PATH",
            "state": {},
            "headers": [
                (b"traceparent", b"00-11111111111111111111111111111111-2222222222222222-00"),
                (b"authorization", b"Bearer SYNTHETIC_PRIVATE_HEADER"),
            ],
            "query_string": b"token=SYNTHETIC_PRIVATE_QUERY",
        },
        receive,
        send,
    )


async def test_asgi_safe_event_logs_share_real_server_and_child_context(settings, caplog):
    global_traces, global_logs = trace.get_tracer_provider(), _logs.get_logger_provider()
    observation, spans, logs = memory(settings)
    with caplog.at_level(logging.INFO, logger="rag_engine.events"):
        await exchange(observation)
        # 标准库或普通runtime日志不经过OTLP事件接口，即使有当前trace也不能旁路。
        with observation.activate(), observation.operation("parse"):
            logging.getLogger("rag_engine").warning("SYNTHETIC_PRIVATE_RUNTIME")
            logging.getLogger("urllib3.connectionpool").warning("SYNTHETIC_PRIVATE_DEPENDENCY")
    observation.shutdown()
    records = logs.get_finished_logs()
    assert len(records) == 3
    http = next(
        record for record in records if record.log_record.attributes["event"] == "http.server.exchange"
    )
    operation = next(record for record in records if record.log_record.attributes.get("stage") == "retrieval")
    server = next(span for span in spans.get_finished_spans() if span.name == "POST /v1/retrievals")
    child = next(span for span in spans.get_finished_spans() if span.name == "rag.retrieval")
    assert (
        http.log_record.trace_id
        == operation.log_record.trace_id
        == server.context.trace_id
        == int("1" * 32, 16)
    )
    assert http.log_record.span_id == server.context.span_id
    assert operation.log_record.span_id == child.context.span_id
    assert child.parent.span_id == server.context.span_id
    assert http.log_record.trace_flags.sampled
    assert all(
        record.log_record.body is None and record.log_record.severity_text == "INFO" for record in records
    )
    attrs = http.log_record.attributes
    assert attrs["request_bytes"] == attrs["response_bytes"] == len(b"SYNTHETIC_PRIVATE_BODY")
    assert attrs["request_body_complete"] is attrs["response_body_complete"] is True
    assert attrs["http_method"] == "POST" and attrs["http_route"] == "/v1/retrievals"
    assert attrs["outcome"] == "success" and attrs["http_status"] == 200
    for record in records:
        assert dict(record.resource.attributes) == {
            "service.name": "rag-engine",
            "service.version": "0.3.2",
            "deployment.environment.name": "github-dev",
        }
        assert "SYNTHETIC_PRIVATE" not in json.dumps(dict(record.log_record.attributes))
    assert trace.get_tracer_provider() is global_traces and _logs.get_logger_provider() is global_logs
    assert not trace.get_current_span().get_span_context().is_valid
    assert len([r for r in caplog.records if r.name == "rag_engine.events"]) == 3


def test_unknown_fields_and_malformed_types_cannot_enter_otlp_log_record(settings):
    observation, _, logs = memory(settings)
    value = {
        "event": "http.server.exchange",
        "http_method": "POST",
        "http_route": "/v1/retrievals",
        "outcome": "success",
        "http_status": 200,
        "duration_ms": 7,
        "request_bytes": True,
        "response_bytes": -1,
        "request_body_complete": "true",
        "response_body_complete": True,
        "summary": "SYNTHETIC_PRIVATE_SUMMARY",
        "request_body": "SYNTHETIC_PRIVATE_BODY",
        "response_body": "SYNTHETIC_PRIVATE_ANSWER",
        "authorization": "SYNTHETIC_PRIVATE_HEADER",
        "trace_id": "9" * 32,
        "span_id": "9" * 16,
        "error_type": "SYNTHETIC_PRIVATE_EXCEPTION",
        "error_code": "SYNTHETIC_PRIVATE_CODE",
    }
    emit_safe_event(value, observation)
    emit_safe_event({**value, "http_route": "/SYNTHETIC_PRIVATE_ROUTE"}, observation)
    emit_safe_event({**value, "event": "SYNTHETIC_PRIVATE_EVENT"}, observation)
    emit_safe_event(
        {
            "event": "rag_operation_finished",
            "stage": "parse",
            "result": "success",
            "elapsed_ms": float("nan"),
        },
        observation,
    )
    observation.shutdown()
    records = logs.get_finished_logs()
    assert len(records) == 1
    attrs = records[0].log_record.attributes
    assert dict(attrs) == {
        "event": "http.server.exchange",
        "http_method": "POST",
        "http_route": "/v1/retrievals",
        "outcome": "success",
        "duration_ms": 7,
        "http_status": 200,
        "response_body_complete": True,
        "summary": "POST /v1/retrievals 200 7ms success",
    }
    assert not records[0].log_record.trace_id and not records[0].log_record.span_id


async def test_stderr_failure_does_not_disable_otlp_or_http(settings, monkeypatch):
    observation, _, logs = memory(settings)

    def fail(*args, **kwargs):
        raise RuntimeError("SYNTHETIC_PRIVATE_SINK")

    monkeypatch.setattr("rag_engine.observability.logger.info", fail)
    await exchange(observation)
    observation.shutdown()
    assert [item.log_record.attributes["event"] for item in logs.get_finished_logs()] == [
        "rag_operation_finished",
        "http.server.exchange",
    ]


async def test_blocked_exporter_has_bounded_queue_without_blocking_http(settings):
    class SlowExporter(InMemoryLogRecordExporter):
        def __init__(self):
            super().__init__()
            self.entered, self.release = threading.Event(), threading.Event()

        def export(self, batch):
            self.entered.set()
            self.release.wait(5)
            return super().export(batch)

    sink = SlowExporter()
    observation, _, _ = memory(settings, sink)
    try:
        for _ in range(32):
            observation.event("parse", "success", 1)
        assert await asyncio.to_thread(sink.entered.wait, 1)
        started = time.monotonic()
        for _ in range(400):
            observation.event("parse", "success", 1)
        await exchange(observation)
        assert time.monotonic() - started < 1
    finally:
        sink.release.set()
        observation.shutdown()
    assert 0 < len(sink.get_finished_logs()) <= 288


async def test_exporter_failure_does_not_change_http_or_expose_exception(settings, caplog):
    class BrokenExporter(InMemoryLogRecordExporter):
        def export(self, batch):
            raise RuntimeError("SYNTHETIC_PRIVATE_EXPORTER")

    observation, _, _ = memory(settings, BrokenExporter())
    with caplog.at_level(logging.INFO):
        await exchange(observation)
        observation.shutdown()
    assert "SYNTHETIC_PRIVATE_EXPORTER" not in caplog.text
    assert '"event":"http.server.exchange"' in caplog.text


def test_shutdown_has_short_budget_even_when_exporter_is_blocked(settings):
    class BlockedExporter(InMemoryLogRecordExporter):
        def __init__(self):
            super().__init__()
            self.entered, self.release = threading.Event(), threading.Event()

        def export(self, batch):
            self.entered.set()
            self.release.wait(5)
            return super().export(batch)

    sink = BlockedExporter()
    observation, _, _ = memory(settings, sink)
    try:
        for _ in range(32):
            observation.event("parse", "success", 1)
        assert sink.entered.wait(1)
        started = time.monotonic()
        observation.shutdown()
        assert time.monotonic() - started < 1.5
    finally:
        sink.release.set()
        observation.shutdown()


def test_pinned_sdk_batch_shutdown_budget_contract_requires_revalidation_on_upgrade():
    from opentelemetry.sdk._shared_internal import BatchProcessor

    assert version("opentelemetry-sdk") == "1.45.2"
    parameter = inspect.signature(BatchProcessor.shutdown).parameters["timeout_millis"]
    assert parameter.default == 30000
    assert parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD


def test_disabled_observation_never_constructs_log_sdk(settings, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("SDK must remain disabled")

    monkeypatch.setattr("rag_engine.observability.LoggerProvider", forbidden)
    monkeypatch.setattr("rag_engine.observability.OTLPLogExporter", forbidden)
    observation = Observability(settings)
    with observation.activate(), observation.operation("parse"):
        pass
    observation.shutdown()
    assert observation.log_provider is None


@pytest.mark.parametrize("module,code", [("graph_worker", "GRAPH_BUILD_FAILED"), ("worker", "INVALID_FILE")])
def test_worker_entry_does_not_create_log_or_trace_sdk(monkeypatch, module, code):
    monkeypatch.setenv("RAG_ENGINE_OBSERVABILITY_ENABLED", "true")
    script = """
import importlib, io, sys
from opentelemetry.sdk import trace
from opentelemetry.sdk import _logs
calls = []
def forbidden(*args, **kwargs):
    calls.append(True)
    raise AssertionError('SDK must not be created in worker')
trace.TracerProvider = _logs.LoggerProvider = forbidden
worker = importlib.import_module('rag_engine.' + sys.argv[1])
sys.stdin = io.TextIOWrapper(io.BytesIO(b'{}'))
worker.main()
assert not calls
"""
    result = subprocess.run(
        [sys.executable, "-c", script, module], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0 and result.stderr == ""
    assert json.loads(result.stdout)["error"] == code
    assert len(result.stdout.splitlines()) == 1


async def test_real_otlp_http_uses_same_authority_headers_and_protobuf_context(settings, monkeypatch):
    captured = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            captured.append(
                (
                    self.path,
                    self.headers.get("Authorization"),
                    self.rfile.read(int(self.headers["Content-Length"])),
                )
            )
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=Bearer SYNTHETIC_TRANSPORT_TOKEN")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "https://untrusted.example.invalid/v1/logs")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "prompt=SYNTHETIC_PRIVATE_RESOURCE")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    observation = None
    try:
        settings.observability_enabled = True
        settings.otlp_traces_endpoint = f"http://127.0.0.1:{server.server_port}/collector/v1/traces"
        observation = Observability(settings)
        await exchange(observation)
        observation.shutdown()
    finally:
        if observation:
            observation.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
    assert {path for path, _, _ in captured} == {"/collector/v1/traces", "/collector/v1/logs"}
    assert all(auth == "Bearer SYNTHETIC_TRANSPORT_TOKEN" for _, auth, _ in captured)
    assert all(b"SYNTHETIC" not in body for _, _, body in captured)
    log_message = ExportLogsServiceRequest.FromString(
        next(body for path, _, body in captured if path.endswith("/logs"))
    )
    trace_message = ExportTraceServiceRequest.FromString(
        next(body for path, _, body in captured if path.endswith("/traces"))
    )
    records = log_message.resource_logs[0].scope_logs[0].log_records
    server_span = next(
        span for span in trace_message.resource_spans[0].scope_spans[0].spans if span.kind == 2
    )
    http = next(
        record
        for record in records
        if any(a.key == "event" and a.value.string_value == "http.server.exchange" for a in record.attributes)
    )
    assert http.trace_id == server_span.trace_id and http.span_id == server_span.span_id
    assert http.body.WhichOneof("value") is None and http.severity_text == "INFO"
    resource = log_message.resource_logs[0].resource
    assert {attribute.key: attribute.value.string_value for attribute in resource.attributes} == {
        "service.name": "rag-engine",
        "service.version": "0.3.2",
        "deployment.environment.name": "github-dev",
    }


def test_log_transport_cannot_add_signal_specific_headers_or_guess_endpoint(settings, monkeypatch):
    settings.observability_enabled = True
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_HEADERS", "X-Private-Extra=SYNTHETIC_TRANSPORT_SECRET")
    with pytest.raises(ValueError, match="headers must match") as failure:
        Observability(settings)
    assert "SYNTHETIC" not in str(failure.value)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_HEADERS")
    settings.otlp_traces_endpoint = "http://127.0.0.1:4318/custom"
    with pytest.raises(ValueError, match="must end with /v1/traces"):
        Observability(settings)
