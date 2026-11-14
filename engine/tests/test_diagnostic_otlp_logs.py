"""后台安全诊断使用真实日志/OTel SDK；不连接模型、数据库或远端Collector。"""

import asyncio
import json
import logging
import time

import pytest
from opentelemetry._logs import SeverityNumber
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from test_safe_otlp_logs import exchange, memory

from rag_engine.observability import SafeDependencyLog, SafeJsonFormatter, SafeRuntimeLog

PRIVATE = "SYNTHETIC_PRIVATE_DIAGNOSTIC"


@pytest.mark.parametrize(
    ("level", "text"),
    [
        (5, "TRACE"),
        (logging.DEBUG, "DEBUG"),
        (logging.INFO, "INFO"),
        (logging.WARNING, "WARN"),
        (logging.ERROR, "ERROR"),
        (logging.CRITICAL, "FATAL"),
    ],
)
@pytest.mark.parametrize("filter_type", [SafeRuntimeLog, SafeDependencyLog])
def test_final_safe_json_retains_normalized_level_without_exception_text(filter_type, level, text):
    record = logging.LogRecord(
        "rag_engine" if filter_type is SafeRuntimeLog else "opentelemetry.exporter.synthetic",
        level,
        __file__,
        1,
        PRIVATE,
        (),
        (RuntimeError, RuntimeError(PRIVATE), None),
    )
    record.levelname = PRIVATE
    accepted = filter_type().filter(record)
    assert accepted is (level >= logging.WARNING or filter_type is SafeRuntimeLog)
    rendered = SafeJsonFormatter(logging.Formatter("%(levelname)s %(message)s")).format(record)
    assert json.loads(rendered)["log"] == {"level": text}
    assert PRIVATE not in rendered and record.exc_info is None


def test_coded_background_failure_exports_real_error_severity_without_fabricating_a_trace(settings):
    observation, _, logs = memory(settings)
    try:
        logging.getLogger("rag_engine.graph").error("rag_graph_build_failed code=%s", "GRAPH_BUILD_FAILED")
    finally:
        observation.shutdown()
    records = logs.get_finished_logs()
    assert len(records) == 1
    record = records[0]
    assert record.log_record.body is None
    assert record.log_record.severity_number == SeverityNumber.ERROR
    assert record.log_record.severity_text == "ERROR"
    assert not record.log_record.trace_id and not record.log_record.span_id
    assert dict(record.log_record.attributes) == {
        "event": "rag_diagnostic",
        "summary": "RAG graph diagnostic: GRAPH_BUILD_FAILED",
        "error_code": "GRAPH_BUILD_FAILED",
        "error_type": "server",
    }
    assert dict(record.resource.attributes) == {
        "service.name": "rag-engine",
        "service.version": "0.3.2",
        "deployment.environment.name": "github-dev",
    }


@pytest.mark.parametrize(
    ("template", "arguments", "failure_class", "expected"),
    [
        (
            "rag_graph_worker_failed code=GRAPH_WORKER_LEASE_LOST",
            (),
            "ServiceUnavailable",
            "ServiceUnavailable",
        ),
        ("rag_graph_worker_failed code=GRAPH_WORKER_LEASE_LOST", (), PRIVATE, None),
        ("rag_graph_build_failed code=%s", ("GRAPH_BUILD_FAILED",), "ServiceUnavailable", None),
    ],
)
def test_only_lease_failure_exports_whitelisted_class_without_exception_text(
    settings, caplog, template, arguments, failure_class, expected
):
    observation, _, logs = memory(settings)
    try:
        logging.getLogger("rag_engine.graph").error(
            template,
            *arguments,
            extra={"failure_class": failure_class},
            exc_info=(RuntimeError, RuntimeError(PRIVATE), None),
        )
    finally:
        observation.shutdown()
    record = logs.get_finished_logs()[0].log_record
    assert record.severity_number == SeverityNumber.ERROR
    assert record.attributes.get("failure_class") == expected
    rendered = next(
        record.getMessage() for record in caplog.records if "rag_diagnostic" in record.getMessage()
    )
    assert json.loads(rendered).get("failure_class") == expected
    assert PRIVATE not in rendered


async def test_concurrent_diagnostics_use_each_active_context_and_actual_warn_error_levels(settings):
    observation, spans, logs = memory(settings)
    contexts = []

    async def emit(level, code):
        with observation.activate(), observation.operation("graph_build", root=True):
            from opentelemetry import trace

            context = trace.get_current_span().get_span_context()
            contexts.append((code, context.trace_id, context.span_id))
            await asyncio.sleep(0)
            logging.getLogger("rag_engine.graph").log(level, "rag_graph_build_failed code=%s", code)

    try:
        await asyncio.gather(
            emit(logging.WARNING, "GRAPH_BUILD_TIMEOUT"), emit(logging.ERROR, "GRAPH_BUILD_FAILED")
        )
    finally:
        observation.shutdown()
    records = [record.log_record for record in logs.get_finished_logs()]
    diagnostic = [record for record in records if record.attributes["event"] == "rag_diagnostic"]
    assert len(diagnostic) == 2
    for code, trace_id, span_id in contexts:
        record = next(record for record in diagnostic if record.attributes["error_code"] == code)
        assert (record.trace_id, record.span_id) == (trace_id, span_id)
        assert record.attributes["trace_id"] == f"{trace_id:032x}"
        assert record.attributes["span_id"] == f"{span_id:016x}"
        assert record.severity_number == (
            SeverityNumber.WARN if code == "GRAPH_BUILD_TIMEOUT" else SeverityNumber.ERROR
        )
    assert len(spans.get_finished_spans()) == 2
    stages = [record for record in records if record.attributes["event"] == "rag_operation_finished"]
    assert len(stages) == 2 and all(record.severity_number == SeverityNumber.INFO for record in stages)


def test_unknown_diagnostics_and_exporter_namespace_cannot_enter_otlp(settings, caplog):
    observation, _, logs = memory(settings)
    try:
        with caplog.at_level(logging.INFO):
            runtime = logging.getLogger("rag_engine.graph")
            runtime.warning(PRIVATE)
            runtime.error("rag_graph_build_failed code=%s", PRIVATE)
            runtime.error("%s code=%s reason=%s", "rag_graph_model_failed", "GRAPH_BUILD_FAILED", PRIVATE)
            logging.getLogger("opentelemetry.exporter.synthetic").error(
                "rag_graph_build_failed code=%s", "GRAPH_BUILD_FAILED"
            )
    finally:
        observation.shutdown()
    assert logs.get_finished_logs() == ()
    assert PRIVATE not in caplog.text


@pytest.mark.parametrize("name", ["opentelemetry", "opentelemetry.exporter.synthetic_late_created"])
def test_exporter_logger_created_after_startup_is_redacted_without_being_exported(settings, caplog, name):
    observation, _, logs = memory(settings)
    try:
        with caplog.at_level(logging.INFO):
            logging.getLogger(name).error(PRIVATE)
    finally:
        observation.shutdown()
    assert logs.get_finished_logs() == ()
    assert PRIVATE not in caplog.text


def test_same_log_record_is_exported_once_and_source_hash_is_not_uploaded(settings):
    observation, _, logs = memory(settings)
    record = logging.LogRecord(
        "rag_engine.graph",
        logging.WARNING,
        __file__,
        1,
        "%s code=%s reason=%s source_hash=%s",
        ("rag_graph_model_failed", "GRAPH_MODEL_TIMEOUT", "MODEL_TIMEOUT", "a" * 64),
        None,
    )
    try:
        runtime = logging.getLogger("rag_engine.graph")
        runtime.handle(record)
        runtime.handle(record)
    finally:
        observation.shutdown()
    records = logs.get_finished_logs()
    assert len(records) == 1
    assert records[0].log_record.attributes["error_code"] == "GRAPH_MODEL_TIMEOUT"
    assert "a" * 64 not in json.dumps(dict(records[0].log_record.attributes))


async def test_exporter_logging_cannot_reenter_diagnostics_or_break_http(settings, caplog):
    class NoisyFailure(InMemoryLogRecordExporter):
        def __init__(self):
            super().__init__()
            self.batches = []

        def export(self, batch):
            self.batches.extend(batch)
            logging.getLogger("opentelemetry.exporter.synthetic").error(PRIVATE)
            logging.getLogger("rag_engine.graph").error(
                "rag_graph_build_failed code=%s", "GRAPH_BUILD_FAILED"
            )
            raise RuntimeError(PRIVATE)

    sink = NoisyFailure()
    observation, _, _ = memory(settings, sink)
    try:
        with caplog.at_level(logging.INFO):
            logging.getLogger("rag_engine.graph").warning(
                "rag_graph_build_failed code=%s", "GRAPH_BUILD_FAILED"
            )
            started = time.monotonic()
            observation.log_provider.force_flush(timeout_millis=500)
            assert time.monotonic() - started < 1
            await asyncio.wait_for(exchange(observation), timeout=1)
            observation.log_provider.force_flush(timeout_millis=500)
    finally:
        observation.shutdown()
    diagnostic = [item for item in sink.batches if item.log_record.attributes["event"] == "rag_diagnostic"]
    assert len(diagnostic) == 1
    assert '"event":"http.server.exchange"' in caplog.text
    assert PRIVATE not in caplog.text
