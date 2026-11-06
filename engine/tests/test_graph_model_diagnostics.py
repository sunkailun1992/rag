"""合成错误覆盖模型边界到子进程协议；禁止原始异常或伪造来源进入诊断。"""

import io
import json
import logging
from types import SimpleNamespace

import pytest

from rag_engine import errors, graph_jobs, graph_worker

SOURCE_HASH = "ab" * 32
PRIVATE_MARKER = "private-model-response-key-body-url"
MODEL_REASONS = (
    "MODEL_TIMEOUT",
    "MODEL_NETWORK",
    "MODEL_TLS",
    "MODEL_CONFIGURATION",
    "MODEL_RATE_LIMIT",
    "MODEL_SERVER_UNAVAILABLE",
    "MODEL_AUTHENTICATION",
    "MODEL_HTTP_REJECTED",
    "MODEL_RESPONSE_LIMIT",
    "MODEL_RESPONSE_JSON",
)
MODEL_CODES = {
    "GRAPH_MODEL_TIMEOUT",
    "GRAPH_MODEL_NETWORK",
    "GRAPH_MODEL_RATE_LIMITED",
    "GRAPH_MODEL_UNAVAILABLE",
    "GRAPH_MODEL_AUTH_FAILED",
    "GRAPH_MODEL_REQUEST_REJECTED",
    "GRAPH_MODEL_RESPONSE_TOO_LARGE",
    "GRAPH_MODEL_INVALID_RESPONSE",
}


def worker_main(monkeypatch, capsys, error):
    import resource

    def fail(_payload):
        raise error

    monkeypatch.setattr(resource, "setrlimit", lambda *_args: None)
    monkeypatch.setattr(graph_worker.logging, "disable", lambda *_args: None)
    monkeypatch.setattr(graph_worker, "build", fail)
    monkeypatch.setattr(graph_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"{}")))
    graph_worker.main()
    result = capsys.readouterr()
    assert result.err == "" and PRIVATE_MARKER not in result.out
    return json.loads(result.out)


@pytest.mark.parametrize("code", ["GRAPH_EXTRACTION_INVALID", "GRAPH_SOURCE_CHANGED", *sorted(MODEL_CODES)])
def test_unknown_exception_text_and_spoofed_properties_cannot_become_worker_codes(monkeypatch, capsys, code):
    error = ValueError(code)
    error.code = code
    error.reason = "ENTITY_NOT_IN_SOURCE"
    error.source_fingerprint = SOURCE_HASH
    assert worker_main(monkeypatch, capsys, error) == {"error": "GRAPH_BUILD_FAILED"}


@pytest.mark.parametrize("reason", MODEL_REASONS)
def test_model_error_reason_and_trusted_hash_roundtrip_to_worker(monkeypatch, capsys, reason):
    error = errors.GraphModelError(reason)
    error.source_fingerprint = SOURCE_HASH
    error.model_output = PRIVATE_MARKER
    assert worker_main(monkeypatch, capsys, error) == {
        "error": error.code,
        "reason": reason,
        "sourceHash": SOURCE_HASH,
    }


@pytest.mark.parametrize("code", sorted(MODEL_CODES))
def test_plain_engine_error_cannot_impersonate_model_error(monkeypatch, capsys, code):
    error = errors.EngineError(code)
    error.reason = "MODEL_TIMEOUT"
    error.source_fingerprint = SOURCE_HASH
    assert worker_main(monkeypatch, capsys, error) == {"error": "GRAPH_BUILD_FAILED"}


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", MODEL_REASONS)
async def test_model_error_source_only_comes_from_application_node(reason):
    error = errors.GraphModelError(reason)
    error.source_fingerprint = PRIVATE_MARKER

    class Extractor:
        async def _aextract(self, _node):
            raise error

    with pytest.raises(errors.GraphModelError) as caught:
        await graph_worker.with_source_diagnostics(Extractor)()._aextract(
            SimpleNamespace(id_="c-" + SOURCE_HASH, text=PRIVATE_MARKER)
        )
    assert caught.value.source_fingerprint == SOURCE_HASH


@pytest.mark.asyncio
@pytest.mark.parametrize("node_id", [None, ["c-" + SOURCE_HASH], "c-" + SOURCE_HASH + "\n", PRIVATE_MARKER])
async def test_bad_application_node_clears_existing_model_hash(node_id):
    error = errors.GraphModelError("MODEL_TIMEOUT")
    error.source_fingerprint = SOURCE_HASH

    class Extractor:
        async def _aextract(self, _node):
            raise error

    with pytest.raises(errors.GraphModelError) as caught:
        await graph_worker.with_source_diagnostics(Extractor)()._aextract(SimpleNamespace(id_=node_id))
    assert not hasattr(caught.value, "source_fingerprint")


@pytest.mark.parametrize(
    "value",
    [
        None,
        [SOURCE_HASH],
        SOURCE_HASH.encode(),
        SOURCE_HASH.upper(),
        "a" * 63,
        "a" * 65,
        SOURCE_HASH + "\n",
        PRIVATE_MARKER,
    ],
)
def test_model_worker_never_emits_untrusted_hash_type_or_format(monkeypatch, capsys, value):
    error = errors.GraphModelError("MODEL_TIMEOUT")
    error.source_fingerprint = value
    assert worker_main(monkeypatch, capsys, error) == {"error": error.code, "reason": error.reason}


@pytest.mark.parametrize("value", [None, ["MODEL_TIMEOUT"], "MODEL_TIMEOUT\n" + PRIVATE_MARKER, "FIELD_TYPE"])
def test_model_worker_never_emits_unknown_or_wrong_family_reason(monkeypatch, capsys, value):
    error = errors.GraphModelError("MODEL_TIMEOUT")
    error.reason = value
    error.source_fingerprint = SOURCE_HASH
    assert worker_main(monkeypatch, capsys, error) == {"error": error.code}


def test_model_worker_never_emits_mismatched_reason_code(monkeypatch, capsys):
    error = errors.GraphModelError("MODEL_TIMEOUT")
    error.reason = "MODEL_AUTHENTICATION"
    error.source_fingerprint = SOURCE_HASH
    assert worker_main(monkeypatch, capsys, error) == {"error": error.code}


async def parent_worker(settings, monkeypatch, value, *, returncode=0, raw=None):
    class Process:
        def __init__(self):
            self.returncode = returncode

        async def communicate(self, _private_input):
            return (raw if raw is not None else json.dumps(value).encode()), b""

    async def spawn(*_args, **_kwargs):
        return Process()

    monkeypatch.setattr(graph_jobs.asyncio, "create_subprocess_exec", spawn)
    return await graph_jobs.run_worker({}, settings, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", MODEL_REASONS)
async def test_parent_preserves_each_model_code_and_logs_only_safe_diagnostics(
    settings, monkeypatch, caplog, reason
):
    error = errors.GraphModelError(reason)
    diagnostic = {"error": error.code, "reason": reason, "sourceHash": SOURCE_HASH, "raw": PRIVATE_MARKER}
    with caplog.at_level(logging.WARNING, logger="rag_engine.graph"):
        with pytest.raises(errors.EngineError) as caught:
            await parent_worker(settings, monkeypatch, diagnostic)
    assert caught.value.code == error.code
    assert "code=" + error.code in caplog.text
    assert "reason=" + reason in caplog.text and "source_hash=" + SOURCE_HASH in caplog.text
    assert PRIVATE_MARKER not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [None, ["MODEL_TIMEOUT"], "MODEL_TIMEOUT\n" + PRIVATE_MARKER, "FIELD_TYPE", "MODEL_AUTHENTICATION"],
)
async def test_parent_drops_wrong_model_reason_and_hash(settings, monkeypatch, caplog, reason):
    with caplog.at_level(logging.WARNING, logger="rag_engine.graph"):
        with pytest.raises(errors.EngineError) as caught:
            await parent_worker(
                settings,
                monkeypatch,
                {"error": "GRAPH_MODEL_TIMEOUT", "reason": reason, "sourceHash": SOURCE_HASH},
            )
    assert caught.value.code == "GRAPH_MODEL_TIMEOUT"
    assert (
        "source_hash" not in caplog.text
        and "reason=" not in caplog.text
        and PRIVATE_MARKER not in caplog.text
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [None, [SOURCE_HASH], SOURCE_HASH.upper(), "a" * 63, "a" * 65, SOURCE_HASH + "\n", PRIVATE_MARKER],
)
async def test_parent_drops_wrong_model_hash_but_preserves_reason(settings, monkeypatch, caplog, value):
    with caplog.at_level(logging.WARNING, logger="rag_engine.graph"):
        with pytest.raises(errors.EngineError) as caught:
            await parent_worker(
                settings,
                monkeypatch,
                {"error": "GRAPH_MODEL_TIMEOUT", "reason": "MODEL_TIMEOUT", "sourceHash": value},
            )
    assert caught.value.code == "GRAPH_MODEL_TIMEOUT"
    assert "reason=MODEL_TIMEOUT" in caplog.text
    assert "source_hash" not in caplog.text and PRIVATE_MARKER not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "diagnostic",
    [
        {"error": "GRAPH_MODEL_UNKNOWN", "reason": "MODEL_TIMEOUT", "sourceHash": SOURCE_HASH},
        {"error": "GRAPH_MODEL_TIMEOUT\n" + PRIVATE_MARKER, "reason": "MODEL_TIMEOUT"},
        {"error": ["GRAPH_MODEL_TIMEOUT"], "reason": "MODEL_TIMEOUT"},
        {"error": {"value": "GRAPH_MODEL_TIMEOUT"}},
        [],
        None,
        "GRAPH_MODEL_TIMEOUT",
        42,
        {"result": None},
        {"result": []},
        {"result": {"processedDocumentCount": True, "nodeCount": 2, "edgeCount": 1}},
    ],
)
async def test_unknown_or_malformed_child_is_failed_without_raw_logging(
    settings, monkeypatch, caplog, diagnostic
):
    with caplog.at_level(logging.WARNING, logger="rag_engine.graph"):
        with pytest.raises(errors.EngineError) as caught:
            await parent_worker(settings, monkeypatch, diagnostic)
    assert caught.value.code == "GRAPH_BUILD_FAILED"
    assert PRIVATE_MARKER not in caplog.text and SOURCE_HASH not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("returncode,raw", [(2, None), (0, b"{not-json"), (0, b"x" * 65537)])
async def test_child_exit_bad_json_and_oversize_remain_build_failed(
    settings, monkeypatch, caplog, returncode, raw
):
    diagnostic = {"error": "GRAPH_MODEL_TIMEOUT", "reason": "MODEL_TIMEOUT", "sourceHash": SOURCE_HASH}
    with pytest.raises(errors.EngineError) as caught:
        await parent_worker(settings, monkeypatch, diagnostic, returncode=returncode, raw=raw)
    assert caught.value.code == "GRAPH_BUILD_FAILED"
    assert SOURCE_HASH not in caplog.text


class StringSubclass(str):
    pass


@pytest.mark.parametrize("value", [StringSubclass(SOURCE_HASH), StringSubclass("MODEL_TIMEOUT")])
def test_model_worker_rejects_string_subclasses_in_diagnostic_fields(monkeypatch, capsys, value):
    error = errors.GraphModelError("MODEL_TIMEOUT")
    error.reason = value if value == "MODEL_TIMEOUT" else "MODEL_TIMEOUT"
    error.source_fingerprint = value
    result = worker_main(monkeypatch, capsys, error)
    assert "sourceHash" not in result
    if value == "MODEL_TIMEOUT":
        assert "reason" not in result


@pytest.mark.parametrize(
    "code",
    [
        None,
        ["GRAPH_MODEL_TIMEOUT"],
        StringSubclass("GRAPH_MODEL_TIMEOUT"),
        "GRAPH_MODEL_TIMEOUT\n" + PRIVATE_MARKER,
    ],
)
def test_mutated_model_error_code_is_not_trusted(monkeypatch, capsys, code):
    error = errors.GraphModelError("MODEL_TIMEOUT")
    error.code = code
    error.source_fingerprint = SOURCE_HASH
    assert worker_main(monkeypatch, capsys, error) == {"error": "GRAPH_BUILD_FAILED"}


def test_worker_does_not_render_even_typed_exception_text(monkeypatch, capsys):
    class UnrenderableError(errors.EngineError):
        def __str__(self):
            raise AssertionError("exception text must never be rendered")

    error = UnrenderableError("GRAPH_EXTRACTION_INVALID")
    error.reason = "FIELD_TYPE"
    assert worker_main(monkeypatch, capsys, error) == {
        "error": "GRAPH_EXTRACTION_INVALID",
        "reason": "FIELD_TYPE",
    }


@pytest.mark.asyncio
async def test_plain_engine_model_impersonation_cannot_gain_application_hash():
    error = errors.EngineError("GRAPH_MODEL_TIMEOUT")
    error.source_fingerprint = SOURCE_HASH

    class Extractor:
        async def _aextract(self, _node):
            raise error

    with pytest.raises(errors.EngineError) as caught:
        await graph_worker.with_source_diagnostics(Extractor)()._aextract(
            SimpleNamespace(id_="c-" + SOURCE_HASH)
        )
    assert not hasattr(caught.value, "source_fingerprint")


@pytest.mark.asyncio
async def test_extraction_error_cannot_reuse_a_model_reason(settings, monkeypatch, caplog, capsys):
    error = errors.EngineError("GRAPH_EXTRACTION_INVALID")
    error.reason = "MODEL_TIMEOUT"
    error.source_fingerprint = SOURCE_HASH
    assert worker_main(monkeypatch, capsys, error) == {"error": "GRAPH_EXTRACTION_INVALID"}
    with caplog.at_level(logging.WARNING, logger="rag_engine.graph"):
        with pytest.raises(errors.EngineError) as caught:
            await parent_worker(
                settings,
                monkeypatch,
                {"error": error.code, "reason": error.reason, "sourceHash": SOURCE_HASH},
            )
    assert caught.value.code == error.code
    assert "reason=MODEL_TIMEOUT" not in caplog.text and SOURCE_HASH not in caplog.text
