"""真实SDK内存导出与ASGI消息流；只有合成正文，禁止外部依赖或模型调用。"""

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from conftest import auth
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from rag_engine.app import create_app
from rag_engine.observability import Observability, ObservationBoundary


def observation(settings):
    settings.observability_enabled = True
    settings.trace_sample_ratio = 0
    exporter = InMemorySpanExporter()
    return Observability(settings, exporter=exporter, log_exporter=InMemoryLogRecordExporter()), exporter


def events(caplog):
    return [
        value
        for record in caplog.records
        if record.name == "rag_engine.events"
        and (value := json.loads(record.getMessage())).get("event") == "http.server.exchange"
    ]


def scope():
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "POST",
        "path": "/v1/retrievals",
        "headers": [(b"traceparent", b"00-11111111111111111111111111111111-2222222222222222-00")],
        "query_string": b"token=SYNTHETIC_SECRET",
        "state": {},
    }


def test_other_environment_retains_configured_root_sampling(settings):
    settings.observability_environment = "review-test"
    obs, exporter = observation(settings)
    with obs.operation("parse"):
        pass
    obs.shutdown()
    assert exporter.get_finished_spans() == ()


async def test_github_dev_records_unsampled_parent_and_child_without_changing_trace(settings, caplog):
    obs, exporter = observation(settings)

    async def application(current, receive, send):
        current["route"] = SimpleNamespace(path="/v1/retrievals")
        with obs.operation("retrieval"):
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"SYNTHETIC_SECRET", "more_body": False})

    async def receive():
        return {"type": "http.request", "body": b"SYNTHETIC_SECRET", "more_body": False}

    async def send(message):
        pass

    with caplog.at_level(logging.INFO, logger="rag_engine.events"):
        await ObservationBoundary(application, obs)(scope(), receive, send)
    obs.shutdown()
    spans = exporter.get_finished_spans()
    server = next(span for span in spans if span.name == "POST /v1/retrievals")
    child = next(span for span in spans if span.name == "rag.retrieval")
    assert server.context.trace_id == int("1" * 32, 16)
    assert server.parent.span_id == int("2" * 16, 16)
    assert child.parent.span_id == server.context.span_id
    assert server.context.trace_flags.sampled
    assert all(not span.events for span in spans)
    event = events(caplog)[0]
    assert event["trace_id"] == "1" * 32 and event["span_id"] == f"{server.context.span_id:016x}"
    assert event["request_bytes"] == event["response_bytes"] == len(b"SYNTHETIC_SECRET")
    assert event["request_body_complete"] and event["response_body_complete"]
    assert "SYNTHETIC_SECRET" not in json.dumps(event)


async def test_real_fastapi_errors_are_observed_after_the_final_response(settings, caplog):
    obs, _ = observation(settings)
    app = create_app(settings, observability=obs)

    @app.get("/v1/synthetic-error")
    async def fail():
        raise RuntimeError("SYNTHETIC_SECRET")

    with caplog.at_level(logging.INFO, logger="rag_engine.events"):
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
            ) as client:
                response = await client.get("/v1/synthetic-error", headers=auth(settings))
    assert response.status_code == 500
    assert len(events(caplog)) == 1
    event = events(caplog)[0]
    assert event["http_status"] == response.status_code
    assert event["response_bytes"] == len(response.content)
    assert event["response_body_complete"]
    assert "SYNTHETIC_SECRET" not in json.dumps(event)


@pytest.mark.parametrize("failure", ["cancel", "write", "timeout"])
async def test_partial_response_never_claims_complete_or_records_exception(settings, caplog, failure):
    obs, _ = observation(settings)
    delivered = []

    async def application(current, receive, send):
        current["route"] = SimpleNamespace(path="/v1/retrievals")
        await receive()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        if failure == "cancel":
            raise asyncio.CancelledError("SYNTHETIC_SECRET")
        if failure == "timeout":
            raise TimeoutError("SYNTHETIC_SECRET")
        await send({"type": "http.response.body", "body": b"not-written", "more_body": False})

    async def receive():
        return {"type": "http.request", "body": b"partial", "more_body": True}

    async def send(message):
        if message.get("body") == b"not-written":
            raise OSError("SYNTHETIC_SECRET")
        delivered.append(message)

    expected = {"cancel": asyncio.CancelledError, "write": OSError, "timeout": TimeoutError}[failure]
    with caplog.at_level(logging.INFO, logger="rag_engine.events"), pytest.raises(expected):
        await ObservationBoundary(application, obs)(scope(), receive, send)
    obs.shutdown()
    event = events(caplog)[0]
    assert event["request_bytes"] == 7
    if failure == "write":
        assert "response_bytes" not in event
    else:
        assert event["response_bytes"] == 5
    assert not event["response_body_complete"] and not event["request_body_complete"]
    assert event["outcome"] == {"cancel": "cancelled", "write": "failure", "timeout": "timeout"}[failure]
    assert "SYNTHETIC_SECRET" not in json.dumps(event)


async def test_unread_input_is_not_preconsumed_and_sink_failure_does_not_change_response(
    settings, monkeypatch
):
    obs, _ = observation(settings)
    sent = []

    async def application(current, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def forbidden_receive():
        raise AssertionError("observation cannot consume request body")

    async def send(message):
        sent.append(message)

    calls = []

    def broken_logger(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("SYNTHETIC_SECRET")

    monkeypatch.setattr("rag_engine.observability.logger.info", broken_logger)
    await ObservationBoundary(application, obs)(scope(), forbidden_receive, send)
    obs.shutdown()
    assert calls == [1]
    assert [message["type"] for message in sent] == ["http.response.start", "http.response.body"]


async def test_a_fully_sent_timeout_error_has_a_complete_response_body(settings, caplog):
    obs, _ = observation(settings)

    async def application(current, receive, send):
        await send({"type": "http.response.start", "status": 504, "headers": []})
        await send({"type": "http.response.body", "body": b"safe", "more_body": False})

    async def receive():
        raise AssertionError("request must remain unread")

    async def send(message):
        pass

    with caplog.at_level(logging.INFO, logger="rag_engine.events"):
        await ObservationBoundary(application, obs)(scope(), receive, send)
    obs.shutdown()
    event = events(caplog)[0]
    assert event["outcome"] == "timeout"
    assert event["response_body_complete"] and event["response_bytes"] == 4


async def test_send_failure_after_partial_current_message_has_unknown_total_bytes(settings, caplog):
    obs, _ = observation(settings)
    wire = bytearray()

    async def application(current, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        await send({"type": "http.response.body", "body": b"partial", "more_body": False})

    async def receive():
        raise AssertionError("request must remain unread")

    async def send(message):
        body = message.get("body", b"")
        if body == b"partial":
            wire.extend(body[:3])
            raise OSError("SYNTHETIC_SECRET")
        wire.extend(body)

    with caplog.at_level(logging.INFO, logger="rag_engine.events"), pytest.raises(OSError):
        await ObservationBoundary(application, obs)(scope(), receive, send)
    obs.shutdown()
    assert wire == b"firstpar"
    event = events(caplog)[0]
    assert "response_bytes" not in event and not event["response_body_complete"]
    assert event["outcome"] == "failure"
