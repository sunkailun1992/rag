"""合成 worker 故障保留主异常，仅输出固定阶段、类别与受限代码栈。"""

import io
import json
import logging
from types import SimpleNamespace

import pytest

from rag_engine import graph_jobs, graph_worker
from rag_engine.errors import EngineError

PRIVATE = "private-clinical-body-url-key"


def main_result(monkeypatch, capsys, error):
    import resource

    def fail(_payload):
        raise error

    monkeypatch.setattr(resource, "setrlimit", lambda *_: None)
    monkeypatch.setattr(graph_worker.logging, "disable", lambda *_: None)
    monkeypatch.setattr(graph_worker, "build", fail)
    monkeypatch.setattr(graph_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"{}")))
    graph_worker.main()
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err
    return json.loads(captured.out)


def test_unknown_worker_error_has_safe_stage_and_category(monkeypatch, capsys):
    result = main_result(monkeypatch, capsys, RuntimeError(PRIVATE))
    assert result["error"] == "GRAPH_BUILD_FAILED"
    assert result["diagnostic"]["stage"] == "INPUT"
    assert result["diagnostic"]["exception"] == "BUILTIN_RUNTIME"
    assert result["diagnostic"]["frames"]
    assert all(set(frame) == {"file", "function", "line"} for frame in result["diagnostic"]["frames"])


def test_unknown_error_class_name_is_not_emitted(monkeypatch, capsys):
    error_type = type(PRIVATE, (Exception,), {})
    result = main_result(monkeypatch, capsys, error_type(PRIVATE))
    assert result["diagnostic"]["exception"] == "OTHER"


def test_wire_diagnostic_sanitizer_rejects_injected_values():
    valid = {
        "stage": "READBACK",
        "exception": "NEO4J_CLIENT",
        "frames": [{"file": "graph_worker.py", "function": "build", "line": 123}],
    }
    assert graph_worker.sanitize_worker_diagnostic(valid) == valid
    for field, value in (("stage", PRIVATE), ("exception", PRIVATE), ("frames", PRIVATE)):
        invalid = {**valid, field: value}
        assert graph_worker.sanitize_worker_diagnostic(invalid) is None
    for field, value in (
        ("file", "/private/" + PRIVATE),
        ("function", PRIVATE),
        ("line", True),
        ("line", -1),
    ):
        invalid = {**valid, "frames": [{**valid["frames"][0], field: value}]}
        assert graph_worker.sanitize_worker_diagnostic(invalid) is None


def stub_build(monkeypatch, *, primary=None, close_error=None, client_error=None, readback_error=None):
    import llama_index.core

    from rag_engine import graph_extract

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def aclose(self):
            if client_error:
                raise client_error

    class Store:
        def close(self):
            if close_error:
                raise close_error

        def structured_query(self, query, _params):
            if readback_error:
                raise readback_error
            return [{"count": 0}] if "count(" in query else []

    async def read(*_args):
        return []

    def index(**_kwargs):
        if primary:
            raise primary

    config = SimpleNamespace(
        graph_max_triplets_per_chunk=8,
        graph_model="synthetic",
        graph_llm_timeout_seconds=10,
        graph_extraction_workers=1,
    )
    monkeypatch.setattr(graph_worker, "Settings", lambda **_: config)
    monkeypatch.setattr(
        graph_worker,
        "GraphBuildRequest",
        SimpleNamespace(
            model_validate=lambda _: SimpleNamespace(activeSources=[], knowledgeBase="synthetic")
        ),
    )
    monkeypatch.setattr(graph_worker, "read_active_sources", read)
    monkeypatch.setattr(graph_worker.httpx, "AsyncClient", Client)
    monkeypatch.setattr(graph_extract, "graph_store", lambda _: Store())
    monkeypatch.setattr(
        graph_extract, "ScopedGraphTransform", lambda **_: SimpleNamespace(node_count=0, edge_count=0)
    )
    monkeypatch.setattr(graph_extract, "QwenGraphLLM", lambda **_: object())
    monkeypatch.setattr(
        graph_extract,
        "ValidatedSimpleLLMPathExtractor",
        type("Extractor", (), {"__init__": lambda self, **_: None}),
    )
    monkeypatch.setattr(llama_index.core, "PropertyGraphIndex", index)
    return {"settings": {}, "request": {}, "tenant": "synthetic", "buildId": "synthetic"}


def test_cleanup_errors_do_not_mask_primary_build_failure(monkeypatch):
    primary = RuntimeError(PRIVATE)
    payload = stub_build(
        monkeypatch, primary=primary, close_error=ValueError(PRIVATE), client_error=TypeError(PRIVATE)
    )
    with pytest.raises(RuntimeError) as caught:
        graph_worker.build(payload)
    assert caught.value is primary
    assert graph_worker._WORKER_STAGE == "INDEX_BUILD"


def test_when_build_succeeds_first_cleanup_failure_wins(monkeypatch):
    close_error = RuntimeError(PRIVATE)
    payload = stub_build(monkeypatch, close_error=close_error, client_error=TypeError(PRIVATE))
    with pytest.raises(RuntimeError) as caught:
        graph_worker.build(payload)
    assert caught.value is close_error
    assert graph_worker._WORKER_STAGE == "STORE_CLOSE"


@pytest.mark.asyncio
async def test_parent_logs_child_exit_signal_and_byte_count(settings, monkeypatch, caplog):
    class Process:
        returncode = -24

        async def communicate(self, _raw):
            return b"private", b""

    async def spawn(*_args, **_kwargs):
        return Process()

    monkeypatch.setattr(graph_jobs.asyncio, "create_subprocess_exec", spawn)
    with caplog.at_level(logging.WARNING, logger="rag_engine.graph"):
        with pytest.raises(EngineError, match="GRAPH_BUILD_FAILED"):
            await graph_jobs.run_worker({}, settings, 1)
    assert "returncode=-24" in caplog.text and "signal=24" in caplog.text and "stdout_bytes=7" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.asyncio
async def test_parent_unknown_exception_keeps_its_stage_and_not_cleanup_error(settings, monkeypatch, caplog):
    class Process:
        returncode = None
        waited = False

        async def communicate(self, _raw):
            raise RuntimeError(PRIVATE)

        def kill(self):
            raise OSError(PRIVATE)

        async def wait(self):
            self.waited = True

    process = Process()

    async def spawn(*_args, **_kwargs):
        return process

    monkeypatch.setattr(graph_jobs.asyncio, "create_subprocess_exec", spawn)
    with caplog.at_level(logging.WARNING, logger="rag_engine.graph"):
        with pytest.raises(EngineError) as caught:
            await graph_jobs.run_worker({}, settings, 1)
    assert caught.value.code == "GRAPH_BUILD_FAILED"
    assert process.waited
    assert "COMMUNICATE" in caplog.text and "BUILTIN_RUNTIME" in caplog.text
    assert PRIVATE not in caplog.text


@pytest.mark.asyncio
async def test_parent_logs_validated_child_diagnostic_and_drops_unknown_fields(settings, monkeypatch, caplog):
    class Process:
        returncode = 0

        async def communicate(self, _raw):
            return json.dumps(
                {
                    "error": "GRAPH_BUILD_FAILED",
                    "diagnostic": {
                        "stage": "READBACK",
                        "exception": "NEO4J_CLIENT",
                        "frames": [{"file": "graph_worker.py", "function": "build", "line": 123}],
                        "message": PRIVATE,
                    },
                }
            ).encode(), b""

    async def spawn(*_args, **_kwargs):
        return Process()

    monkeypatch.setattr(graph_jobs.asyncio, "create_subprocess_exec", spawn)
    with caplog.at_level(logging.WARNING, logger="rag_engine.graph"):
        with pytest.raises(EngineError, match="GRAPH_BUILD_FAILED"):
            await graph_jobs.run_worker({}, settings, 1)
    assert "READBACK" in caplog.text and "NEO4J_CLIENT" in caplog.text
    assert PRIVATE not in caplog.text


def test_storage_exception_keeps_readback_stage_and_exact_safe_class(monkeypatch):
    from neo4j.exceptions import ClientError

    error = ClientError(PRIVATE)
    payload = stub_build(monkeypatch, readback_error=error)
    with pytest.raises(ClientError) as caught:
        graph_worker.build(payload)
    diagnostic = graph_worker.safe_worker_diagnostic(caught.value, graph_worker._WORKER_STAGE)
    assert diagnostic["stage"] == "READBACK" and diagnostic["exception"] == "NEO4J_CLIENT"
    assert any(
        frame["file"] == "graph_worker.py" and frame["function"] == "build" for frame in diagnostic["frames"]
    )
    assert PRIVATE not in json.dumps(diagnostic)


@pytest.mark.parametrize(
    "frames", [[], [{}] * 9, "frame", [{"file": "graph_worker.py", "function": "build", "line": "42"}]]
)
def test_diagnostic_frames_are_bounded_and_typed(frames):
    result = graph_worker.sanitize_worker_diagnostic(
        {"stage": "READBACK", "exception": "OTHER", "frames": frames}
    )
    assert (result is not None) is (frames == [])


def test_real_module_worker_keeps_safe_main_frames_without_service_access():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "rag_engine.graph_worker"],
        input=b'{"settings":{},"request":{}}',
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0
    response = json.loads(result.stdout)
    assert response["error"] == "GRAPH_BUILD_FAILED"
    diagnostic = response["diagnostic"]
    assert diagnostic["stage"] == "CONFIGURE"
    assert {frame["function"] for frame in diagnostic["frames"]} >= {"main", "build"}
    assert all(frame["file"] == "graph_worker.py" for frame in diagnostic["frames"])
    assert b"api_key" not in result.stdout and b"settings" not in result.stdout


@pytest.mark.parametrize(
    "name,bucket",
    [
        ("CypherSyntaxError", "NEO4J_CLIENT"),
        ("ConstraintError", "NEO4J_CLIENT"),
        ("DatabaseUnavailable", "NEO4J_TRANSIENT"),
        ("ResultConsumedError", "NEO4J_RESULT"),
    ],
)
def test_official_neo4j_error_subclasses_use_fixed_family_buckets(name, bucket):
    from neo4j import exceptions

    error_type = getattr(exceptions, name)
    error = error_type(None, PRIVATE) if name == "ResultConsumedError" else error_type(PRIVATE)
    diagnostic = graph_worker.safe_worker_diagnostic(error, "INDEX_BUILD")
    assert diagnostic["exception"] == bucket
    assert PRIVATE not in json.dumps(diagnostic) and name not in json.dumps(diagnostic)


def test_custom_runtime_error_subclass_still_uses_other_bucket():
    error_type = type(PRIVATE, (RuntimeError,), {})
    assert graph_worker.safe_worker_diagnostic(error_type(PRIVATE), "READBACK")["exception"] == "OTHER"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["WORKER_RUN", "SOURCE_RECHECK", "PUBLISH"])
async def test_pump_unknown_error_has_fixed_stage_and_no_exception_text(
    settings, monkeypatch, caplog, failure_stage
):
    import asyncio

    from neo4j.exceptions import ClientError

    error = ClientError(PRIVATE)
    failures = []
    job = {
        "buildId": "synthetic",
        "tenant": "synthetic",
        "knowledgeBase": "synthetic",
        "requestJson": '{"knowledgeBase":"synthetic","activeSources":[]}',
    }

    class Repository:
        claimed = False

        async def claim(self):
            if self.claimed:
                raise asyncio.CancelledError()
            self.claimed = True
            return {"job": job, "remaining": 5}

        async def publish(self, *_args):
            if failure_stage == "PUBLISH":
                raise error

        async def finish_failed(self, _job, code):
            failures.append(code)

        async def cleanup(self, _job):
            pass

    async def worker(*_args):
        if failure_stage == "WORKER_RUN":
            raise error
        return {"processedDocumentCount": 1, "nodeCount": 2, "edgeCount": 1}

    async def sources(*_args):
        if failure_stage == "SOURCE_RECHECK":
            raise error
        return []

    monkeypatch.setattr(graph_jobs, "run_worker", worker)
    monkeypatch.setattr(graph_jobs, "read_active_sources", sources)
    with caplog.at_level(logging.WARNING, logger="rag_engine.graph"):
        with pytest.raises(asyncio.CancelledError):
            await graph_jobs.GraphManager(settings, None, Repository()).pump()
    assert failures == ["GRAPH_BUILD_FAILED"]
    entries = [
        record.getMessage()
        for record in caplog.records
        if "rag_graph_parent_stage_failed" in record.getMessage()
    ]
    assert len(entries) == 1
    assert failure_stage in entries[0] and "NEO4J_CLIENT" in entries[0]
    assert PRIVATE not in caplog.text
