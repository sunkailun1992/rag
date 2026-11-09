"""显式元数据白名单；不安装全局SDK或自动内容/异常采集器。"""

import asyncio
import contextvars
import json
import logging
import math
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, TraceIdRatioBased
from opentelemetry.trace import Link, SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import CollectorRegistry, Counter, Histogram

from rag_engine.errors import GRAPH_MODEL_CODES, VALIDATION_REASONS, EngineError

VERSION = "0.2.5"
STAGES = frozenset(
    {
        "parse",
        "retrieval",
        "embedding",
        "recall",
        "graph_expand",
        "rerank",
        "graph_submit",
        "graph_query",
        "graph_build",
        "worker_run",
        "source_recheck",
        "publish",
        "upstream",
    }
)
RESULTS = frozenset({"success", "failed", "cancelled", "timeout", "degraded", "empty", "client_error"})
PROPAGATOR = TraceContextTextMapPropagator()
_CURRENT = contextvars.ContextVar("rag_observability", default=None)
logger = logging.getLogger("rag_engine.events")
_SERVICE_ENVIRONMENT = "github-dev"


SAFE_TEMPLATES = frozenset(
    [
        "%s code=%s reason=%s",
        "%s code=%s reason=%s source_hash=%s",
        "rag_engine_failed code=%s status=%d",
        "rag_graph_build_failed code=%s",
        "rag_graph_failed code=GRAPH_STORE_UNAVAILABLE",
        "rag_graph_model_failed code=%s",
        "rag_graph_parent_cleanup_failed data=%s",
        "rag_graph_parent_failed data=%s",
        "rag_graph_parent_stage_failed data=%s",
        "rag_graph_scheduler_failed code=GRAPH_STORE_UNAVAILABLE",
        "rag_graph_worker_diagnostic data=%s",
        "rag_graph_worker_failed code=GRAPH_WORKER_LEASE_LOST",
        "rag_graph_worker_process_failed stage=%s returncode=%s signal=%s stdout_bytes=%s",
        "rag_parse_finished elapsed_ms=%d",
        "rag_parser_worker_failed phase=%s reason=cancelled elapsed_ms=%d",
        "rag_parser_worker_failed phase=%s reason=exit exit_code=%d elapsed_ms=%d",
        "rag_parser_worker_failed phase=%s reason=timeout elapsed_ms=%d",
        "rag_retrieval_finished elapsed_ms=%d",
    ]
)
SAFE_CODES = GRAPH_MODEL_CODES | frozenset(
    [
        "CHUNK_LIMIT_EXCEEDED",
        "DOCUMENT_HAS_NO_TEXT",
        "EMBEDDING_INVALID_RESPONSE",
        "EMBEDDING_METADATA_MISMATCH",
        "EMBEDDING_SCHEMA_MISMATCH",
        "FILE_SIZE_EXCEEDED",
        "GRAPH_BUILD_FAILED",
        "GRAPH_BUILD_NOT_FOUND",
        "GRAPH_BUILD_TIMEOUT",
        "GRAPH_CONTENT_LIMIT",
        "GRAPH_EXTRACTION_INVALID",
        "GRAPH_EXTRACTION_LIMIT",
        "GRAPH_EXTRACTION_UNGROUNDED",
        "GRAPH_NOT_CONFIGURED",
        "GRAPH_PUBLICATION_SUPERSEDED",
        "GRAPH_QUEUE_FULL",
        "GRAPH_READBACK_FAILED",
        "GRAPH_REQUEST_TIMEOUT",
        "GRAPH_REQUEST_TOO_LARGE",
        "GRAPH_RESPONSE_TOO_LARGE",
        "GRAPH_SCOPE_LIMIT",
        "GRAPH_SOURCE_CHANGED",
        "GRAPH_SOURCE_DEACTIVATED",
        "GRAPH_STORE_UNAVAILABLE",
        "GRAPH_WORKER_ALREADY_RUNNING",
        "GRAPH_WORKER_LEASE_LOST",
        "GRAPH_WORKER_UNAVAILABLE",
        "INVALID_BASE64",
        "INVALID_BATCH_SIZE",
        "INVALID_REQUEST",
        "INVALID_SCOPE",
        "OCR_INVALID_RESPONSE",
        "OCR_NOT_CONFIGURED",
        "OCR_PAGE_HAS_NO_TEXT",
        "OCR_UNAVAILABLE",
        "PARSER_BUSY",
        "PARSER_FAILED",
        "PARSER_RESOURCE_LIMIT",
        "PARSER_TIMEOUT",
        "RETRIEVAL_BUSY",
        "RETRIEVAL_NOT_CONFIGURED",
        "RETRIEVAL_TIMEOUT",
        "RETRIEVAL_UNAVAILABLE",
        "SCOPE_REQUIRED",
        "SPARSE_BACKFILL_NOT_COMPLETE",
        "SPARSE_INDEX_INCOMPLETE",
        "SPARSE_SCHEMA_MISMATCH",
        "SPARSE_SCHEMA_NOT_READY",
        "SPARSE_TEXT_UNSUPPORTED",
        "SPARSE_WRITE_INCOMPLETE",
        "TENANT_ISOLATION_VIOLATION",
        "TEXT_LIMIT_EXCEEDED",
        "UNSUPPORTED_FORMAT",
        "UPSTREAM_INVALID_RESPONSE",
        "UPSTREAM_REJECTED",
        "UPSTREAM_RESPONSE_TOO_LARGE",
        "VECTOR_INCONSISTENT_RESPONSE",
        "VECTOR_INVALID_RESPONSE",
        "WRITERS_PAUSED_CONFIRMATION_REQUIRED",
    ]
)
SAFE_VALUES = (
    SAFE_CODES
    | VALIDATION_REASONS
    | frozenset(
        {
            "inspect",
            "split",
            "ENCODE_INPUT",
            "SPAWN",
            "COMMUNICATE",
            "DECODE_OUTPUT",
            "VALIDATE_RESULT",
            "PROCESS_CLEANUP",
            "rag_graph_model_failed",
            "rag_graph_validation_failed",
        }
    )
)


def utc_timestamp():
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def event_metadata(observation=None):
    observation = observation or _CURRENT.get()
    value = {
        "@timestamp": utc_timestamp(),
        "service": {
            "name": "rag-engine",
            "version": VERSION,
            "environment": observation.environment if observation else _SERVICE_ENVIRONMENT,
        },
    }
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        value.update(trace_id=f"{context.trace_id:032x}", span_id=f"{context.span_id:016x}")
    return value


class SafeJsonFormatter(logging.Formatter):
    """在最终handler去除Uvicorn前缀；其他未受管的日志沿用原formatter。"""

    def __init__(self, original):
        super().__init__()
        self.original = original or logging.Formatter()

    def format(self, record):
        if getattr(record, "_rag_safe_json", False):
            return record.getMessage()
        return self.original.format(record)


def protect_final_handlers(log):
    while log is not None:
        for handler in log.handlers:
            if not isinstance(handler.formatter, SafeJsonFormatter):
                handler.setFormatter(SafeJsonFormatter(handler.formatter))
        if not log.propagate:
            break
        log = log.parent


class SafeRuntimeLog(logging.Filter):
    """只序列化现有已审核模板及受限诊断；陌生模板与异常原文失败关闭。"""

    def filter(self, record):
        trusted = (
            isinstance(record.msg, str) and record.msg in SAFE_TEMPLATES and isinstance(record.args, tuple)
        )
        arguments = list(record.args) if isinstance(record.args, tuple) else []
        for index, argument in enumerate(arguments if trusted else ()):
            if type(argument) in {int, float}:
                trusted &= math.isfinite(argument)
            elif argument is None:
                pass
            elif type(argument) is str and argument in SAFE_VALUES:
                pass
            elif (
                type(argument) is str
                and "source_hash=%s" in record.msg
                and index == len(arguments) - 1
                and re.fullmatch(r"[0-9a-f]{64}", argument)
            ):
                pass
            elif type(argument) is str and "data=%s" in record.msg:
                from rag_engine.graph_worker import sanitize_worker_diagnostic

                try:
                    diagnostic = json.loads(argument)
                    sanitized = sanitize_worker_diagnostic(diagnostic)
                    trusted &= sanitized == diagnostic
                    if sanitized is not None:
                        arguments[index] = json.dumps(sanitized, separators=(",", ":"))
                except (ValueError, TypeError):
                    trusted = False
            else:
                trusted = False
        record.args = tuple(arguments)
        try:
            diagnostic = record.getMessage() if trusted else "suppressed"
        except (TypeError, ValueError):
            diagnostic = "suppressed"
        value = {**event_metadata(), "event": "rag_diagnostic", "diagnostic": diagnostic}
        record._rag_safe_json = True
        record.msg, record.args = json.dumps(value, separators=(",", ":")), ()
        record.exc_info = record.exc_text = record.stack_info = None
        return True


class SafeDependencyLog(logging.Filter):
    """依赖诊断可能含URL和异常原文，统一替换而非以正则猜测秘密。"""

    def filter(self, record):
        record.msg = json.dumps(
            {**event_metadata(), "event": "rag_dependency_diagnostic", "result": "failed"},
            separators=(",", ":"),
        )
        record.args = ()
        record._rag_safe_json = True
        record.exc_info = record.exc_text = record.stack_info = None
        return record.levelno >= logging.WARNING


def protect_dependency_logs(environment):
    global _SERVICE_ENVIRONMENT
    _SERVICE_ENVIRONMENT = environment
    runtime_root = logging.getLogger("rag_engine")
    runtime_root.setLevel(logging.INFO)
    if not runtime_root.hasHandlers():
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        runtime_root.addHandler(handler)
    for name in ("rag_engine", "rag_engine.graph"):
        runtime = logging.getLogger(name)
        if not any(isinstance(item, SafeRuntimeLog) for item in runtime.filters):
            runtime.addFilter(SafeRuntimeLog())
        protect_final_handlers(runtime)
    dependency_names = {
        name
        for name in logging.Logger.manager.loggerDict
        if name.startswith(("httpcore.", "urllib3.", "neo4j.", "llama_index.", "opentelemetry."))
    }
    dependency_names.update(("httpcore.connection", "urllib3.connectionpool"))
    for name in (
        *dependency_names,
        "httpx",
        "uvicorn.access",
        "uvicorn.error",
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "opentelemetry.exporter.http_transport",
        "opentelemetry.sdk._shared_internal",
    ):
        dependency = logging.getLogger(name)
        if not any(isinstance(item, SafeDependencyLog) for item in dependency.filters):
            dependency.addFilter(SafeDependencyLog())
        protect_final_handlers(dependency)


def failure_result(error):
    if isinstance(error, asyncio.CancelledError):
        return "cancelled"
    if isinstance(error, (TimeoutError, httpx.TimeoutException)) or (
        isinstance(error, EngineError) and error.code.endswith("_TIMEOUT")
    ):
        return "timeout"
    return "failed"


@dataclass
class Operation:
    result: str = "success"


class Observability:
    def __init__(self, settings, *, exporter=None):
        self.environment = settings.observability_environment
        protect_dependency_logs(self.environment)
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "rag_engine_requests", "HTTP请求终态", ["route", "method", "result"], registry=self.registry
        )
        self.stages = Histogram(
            "rag_engine_stage_duration_seconds",
            "阶段耗时秒",
            ["stage", "result"],
            buckets=(0.01, 0.05, 0.1, 0.4, 0.8, 1, 3, 10, 60, 300, 1800),
            registry=self.registry,
        )
        self.retrievals = Counter(
            "rag_engine_retrievals", "检索业务结果", ["mode", "result"], registry=self.registry
        )
        self.graph_builds = Counter(
            "rag_engine_graph_builds", "父进程构建终态", ["result"], registry=self.registry
        )
        self.provider = None
        if settings.observability_enabled:
            # Resource.create会隐式读取环境属性和进程信息；这里仅保留显式白名单。
            self.provider = TracerProvider(
                resource=Resource(
                    {
                        "service.name": "rag-engine",
                        "service.version": VERSION,
                        "deployment.environment.name": settings.observability_environment,
                    }
                ),
                # 个人开发环境全采样，包括上游sampled=0；保留原trace/parent，不新建根替代。
                sampler=(
                    ALWAYS_ON
                    if settings.observability_environment == "github-dev"
                    else ParentBased(TraceIdRatioBased(settings.trace_sample_ratio))
                ),
                shutdown_on_exit=False,
                span_limits=SpanLimits(
                    max_attributes=8,
                    max_span_attributes=8,
                    max_span_attribute_length=128,
                    max_events=0,
                    max_links=1,
                    max_attribute_length=128,
                    max_event_attributes=0,
                    max_link_attributes=0,
                ),
            )
            if exporter is None:
                # Collector地址/总HTTP等待显式设置；不从业务请求复制任何头。
                exporter = OTLPSpanExporter(
                    endpoint=settings.otlp_traces_endpoint,
                    timeout=settings.otlp_timeout_seconds,
                    max_request_size=1024 * 1024,
                    meter_provider=NoOpMeterProvider(),
                )
            self.provider.add_span_processor(
                BatchSpanProcessor(
                    exporter,
                    max_queue_size=256,
                    max_export_batch_size=32,
                    schedule_delay_millis=500,
                    export_timeout_millis=1000,
                )
            )
        provider = self.provider or trace.NoOpTracerProvider()
        self.tracer = provider.get_tracer("rag_engine", VERSION)

    @contextmanager
    def activate(self):
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            _CURRENT.reset(token)

    def event(self, stage, result, elapsed_ms):
        value = {
            **event_metadata(self),
            "event": "rag_operation_finished",
            "stage": stage if stage in STAGES else "upstream",
            "result": result if result in RESULTS else "failed",
            "elapsed_ms": max(0, int(elapsed_ms)),
        }
        emit_safe_event(value)

    @contextmanager
    def operation(self, stage, *, root=False, links=()):
        stage = stage if stage in STAGES else "upstream"
        operation = Operation()
        started = time.monotonic()
        context = Context() if root else None
        with self.tracer.start_as_current_span(
            "rag." + stage,
            context=context,
            links=[Link(link) for link in links if link.is_valid],
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield operation
            except BaseException as error:
                operation.result = failure_result(error)
                raise
            finally:
                result = operation.result if operation.result in RESULTS else "failed"
                span.set_attribute("rag.stage", stage)
                span.set_attribute("rag.result", result)
                if result in {"failed", "timeout"}:
                    span.set_status(Status(StatusCode.ERROR))
                elapsed = time.monotonic() - started
                self.stages.labels(stage, result).observe(elapsed)
                if stage == "graph_build":
                    self.graph_builds.labels(result).inc()
                self.event(stage, result, elapsed * 1000)

    def retrieval_result(self, result):
        mode = result.mode if result.mode in {"dense", "hybrid"} else "dense"
        outcome = "degraded" if result.degraded else ("success" if result.hits else "empty")
        self.retrievals.labels(mode, outcome).inc()
        if result.graphDegraded:
            self.retrievals.labels("graph", "degraded").inc()
        elif result.graphAugmented:
            self.retrievals.labels("graph", "success").inc()

    def shutdown(self):
        if self.provider:
            self.provider.shutdown()


@contextmanager
def stage(name):
    observation = _CURRENT.get()
    if observation is None:
        yield Operation()
    else:
        with observation.operation(name) as operation:
            yield operation


class ObservedTransport(httpx.AsyncBaseTransport):
    """HTTPX标准传输边界；只记录固定操作及状态类别，不读取URL/正文/头。"""

    def __init__(self, transport, observation):
        self.transport, self.observation = transport, observation

    async def handle_async_request(self, request):
        with self.observation.tracer.start_as_current_span(
            "HTTP upstream",
            kind=SpanKind.CLIENT,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            carrier = {}
            PROPAGATOR.inject(carrier)
            # 独立服务调用不转发任何baggage；配置凭据头保持原协议。
            request.headers.pop("baggage", None)
            request.headers.pop("traceparent", None)
            request.headers.pop("tracestate", None)
            request.headers.update(carrier)
            try:
                response = await self.transport.handle_async_request(request)
            except BaseException as error:
                span.set_attribute("rag.result", failure_result(error))
                span.set_status(Status(StatusCode.ERROR))
                raise
            span.set_attribute("http.response.status_code", response.status_code)
            if response.status_code >= 400:
                span.set_status(Status(StatusCode.ERROR))
            return response

    async def aclose(self):
        await self.transport.aclose()


def emit_safe_event(value):
    """安全sink失败不能改变请求结果，也不回退打印原文或异常。"""
    try:
        logger.info(json.dumps(value, separators=(",", ":")), extra={"_rag_safe_json": True})
    except Exception:
        pass


def matched_route(scope):
    """只采用框架已经匹配的声明模板，不把原始路径或客户端值用作日志路由。"""
    path = getattr(scope.get("route"), "path", None)
    if not isinstance(path, str):
        return "UNMATCHED"
    if path == "/v1/graphs/builds/{buildId}" or path in {
        "/health",
        "/metrics",
        "/v1/openapi.json",
        "/v1/parses",
        "/v1/retrievals",
        "/v1/graphs/builds",
        "/v1/graphs/queries",
        "/v1/graphs/deactivations",
    }:
        return path
    return "UNMATCHED"


class ObservationBoundary:
    """最外层ASGI终态：只计实际receive/send字节，不消费、缓存或导出任何正文。"""

    def __init__(self, app, observation):
        self.app, self.observation = app, observation

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = (
            scope["method"]
            if scope["method"]
            in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"}
            else "OTHER"
        )
        # 只接受W3C trace字段，不提取baggage，也不向span复制请求header。
        carrier = {}
        for key in (b"traceparent", b"tracestate"):
            values = [value for name, value in scope["headers"] if name.lower() == key]
            if len(values) == 1:
                carrier[key.decode()] = values[0].decode("latin1")
        result, status, failure = "failed", None, None
        request_bytes = response_bytes = 0
        request_complete = response_complete = disconnected = False
        response_bytes_known = True
        response_stream = False
        started = time.monotonic()

        async def observed_receive():
            nonlocal request_bytes, request_complete, disconnected
            message = await receive()
            if message["type"] == "http.request":
                request_bytes += len(message.get("body", b""))
                if not message.get("more_body", False):
                    request_complete = True
            elif message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def observed_send(message):
            nonlocal status, response_bytes, response_complete, response_stream, response_bytes_known
            # 先写成功再计数；失败分片不能按已发送处理。
            try:
                await send(message)
            except BaseException:
                # server可能已经写出当前消息的一部分，之前成功片段不代表可证明的总数。
                response_bytes_known = response_complete = False
                raise
            if message["type"] == "http.response.start":
                value = message["status"]
                if type(value) is int and 100 <= value <= 599:
                    status = value
                response_stream = any(
                    key.lower() == b"content-type" and value.lower().startswith(b"text/event-stream")
                    for key, value in message.get("headers", ())
                )
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
                if not message.get("more_body", False):
                    response_complete = True

        with (
            self.observation.activate(),
            self.observation.tracer.start_as_current_span(
                method + " UNMATCHED",
                context=PROPAGATOR.extract(carrier, context=Context()),
                kind=SpanKind.SERVER,
                attributes={"http.request.method": method},
                record_exception=False,
                set_status_on_exception=False,
            ) as span,
        ):
            try:
                await self.app(scope, observed_receive, observed_send)
                result = (
                    "cancelled"
                    if disconnected
                    else "timeout"
                    if status in {408, 504}
                    else "failed"
                    if status is None or status >= 500 or not response_complete or not response_bytes_known
                    else "client_error"
                    if status >= 400
                    else "success"
                )
                if result == "cancelled":
                    response_complete = False
                business = scope.get("state", {}).get("observation_result")
                if result == "success" and business in {"degraded", "empty"}:
                    result = business
            except BaseException as error:
                failure = error
                result = failure_result(error)
                if result == "cancelled":
                    response_complete = False
                raise
            finally:
                if not response_bytes_known:
                    response_complete = False
                route = matched_route(scope)
                span.update_name(method + " " + route)
                span.set_attribute("http.route", route)
                if status is not None:
                    span.set_attribute("http.response.status_code", status)
                span.set_attribute("rag.result", result)
                if result in {"failed", "timeout"} or status is not None and status >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                try:
                    self.observation.requests.labels(route, method, result).inc()
                except Exception:
                    pass
                outcome = (
                    "rejected"
                    if status in {401, 403}
                    else "cancelled"
                    if result == "cancelled"
                    else "timeout"
                    if result == "timeout"
                    else "failure"
                    if result in {"failed", "client_error"}
                    else "success"
                )
                elapsed = max(0, int((time.monotonic() - started) * 1000))
                status_text = status if status is not None else "unknown"
                value = {
                    **event_metadata(self.observation),
                    "event": "http.server.exchange",
                    "http_method": method,
                    "http_route": route,
                    "duration_ms": elapsed,
                    "outcome": outcome,
                    "request_bytes": request_bytes,
                    "request_body_complete": request_complete,
                    "response_body_complete": response_complete,
                    "request_body_omitted": "policy"
                    if request_bytes
                    else "empty"
                    if request_complete
                    else "not_consumed",
                    "response_body_omitted": "stream"
                    if response_stream
                    else "policy"
                    if response_bytes
                    else "empty"
                    if response_complete
                    else "incomplete",
                    "summary": f"{method} {route} {status_text} {elapsed}ms {outcome}",
                }
                if status is not None:
                    value["http_status"] = status
                if response_bytes_known:
                    value["response_bytes"] = response_bytes
                if failure is not None:
                    value["error_type"] = (
                        "cancelled"
                        if result == "cancelled"
                        else "timeout"
                        if result == "timeout"
                        else "validation"
                        if isinstance(failure, ValueError)
                        else "server"
                    )
                    if isinstance(failure, EngineError) and failure.code in SAFE_CODES:
                        value["error_code"] = failure.code
                emit_safe_event(value)
