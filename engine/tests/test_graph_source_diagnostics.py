"""只用合成错误验证不可读来源指纹，不记录原文、模型字段或可读点ID。"""

import io
import json
import logging
from types import SimpleNamespace

import pytest

from rag_engine import graph_jobs, graph_worker
from rag_engine.errors import VALIDATION_REASONS, EngineError

SOURCE_HASH = "ab" * 32
RAW = "RAW_MARKER_DO_NOT_LOG"


def run_worker_main(monkeypatch, capsys, error):
    import resource

    def fail(_payload):
        raise error

    monkeypatch.setattr(resource, "setrlimit", lambda *_args: None)
    monkeypatch.setattr(graph_worker.logging, "disable", lambda *_args: None)
    monkeypatch.setattr(graph_worker, "build", fail)
    monkeypatch.setattr(graph_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"{}")))
    graph_worker.main()
    captured = capsys.readouterr()
    assert captured.err == "" and RAW not in captured.out
    return json.loads(captured.out)


def test_worker_emits_only_hash_for_whitelisted_extraction_error(monkeypatch, capsys):
    error = EngineError("GRAPH_EXTRACTION_UNGROUNDED", 422)
    error.reason = "ENTITY_NOT_IN_SOURCE"
    error.source_fingerprint = SOURCE_HASH
    error.model_output = RAW
    result = run_worker_main(monkeypatch, capsys, error)
    assert result == {
        "error": "GRAPH_EXTRACTION_UNGROUNDED",
        "reason": "ENTITY_NOT_IN_SOURCE",
        "sourceHash": SOURCE_HASH,
    }


@pytest.mark.parametrize(
    "value",
    [
        None,
        [SOURCE_HASH],
        {"value": SOURCE_HASH},
        SOURCE_HASH.encode(),
        SOURCE_HASH.upper(),
        "a" * 63,
        "a" * 65,
        "a" * 64 + "\n",
        "{{" + RAW + "}}",
        RAW,
    ],
)
def test_worker_drops_wrong_hash_type_length_and_template_markers(monkeypatch, capsys, value):
    error = EngineError("GRAPH_EXTRACTION_INVALID", 422)
    error.reason = "FIELD_TYPE"
    error.source_fingerprint = value
    assert run_worker_main(monkeypatch, capsys, error) == {
        "error": "GRAPH_EXTRACTION_INVALID",
        "reason": "FIELD_TYPE",
    }


@pytest.mark.parametrize(
    "code,reason",
    [
        ("GRAPH_SOURCE_CHANGED", "FIELD_TYPE"),
        (RAW, "FIELD_TYPE"),
        ("GRAPH_EXTRACTION_INVALID", RAW),
        ("GRAPH_EXTRACTION_INVALID", ["FIELD_TYPE"]),
    ],
)
def test_worker_never_attaches_source_hash_to_unknown_error_or_reason(monkeypatch, capsys, code, reason):
    error = EngineError(code)
    error.reason = reason
    error.source_fingerprint = SOURCE_HASH
    result = run_worker_main(monkeypatch, capsys, error)
    assert "sourceHash" not in result
    assert result["error"] in {"GRAPH_SOURCE_CHANGED", "GRAPH_BUILD_FAILED", "GRAPH_EXTRACTION_INVALID"}


async def parent_run(settings, monkeypatch, diagnostic):
    class Process:
        returncode = 0

        async def communicate(self, _input):
            return json.dumps(diagnostic).encode(), b""

    async def spawn(*_args, **_kwargs):
        return Process()

    monkeypatch.setattr(graph_jobs.asyncio, "create_subprocess_exec", spawn)
    return await graph_jobs.run_worker({}, settings, 1)


@pytest.mark.asyncio
async def test_parent_logs_safe_reason_and_strict_source_hash_only(settings, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="rag_engine.graph"):
        with pytest.raises(EngineError, match="GRAPH_EXTRACTION_INVALID"):
            await parent_run(
                settings,
                monkeypatch,
                {
                    "error": "GRAPH_EXTRACTION_INVALID",
                    "reason": "FIELD_TYPE",
                    "sourceHash": SOURCE_HASH,
                    "unknown": RAW,
                },
            )
    assert "reason=FIELD_TYPE" in caplog.text and "source_hash=" + SOURCE_HASH in caplog.text
    assert RAW not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        None,
        [SOURCE_HASH],
        {"value": SOURCE_HASH},
        SOURCE_HASH.upper(),
        "a" * 63,
        "a" * 65,
        "a" * 64 + "\n",
        "{{" + RAW + "}}",
        RAW,
    ],
)
async def test_parent_invalid_source_hash_preserves_safe_reason_only(settings, monkeypatch, caplog, value):
    with caplog.at_level(logging.WARNING, logger="rag_engine.graph"):
        with pytest.raises(EngineError, match="GRAPH_EXTRACTION_INVALID"):
            await parent_run(
                settings,
                monkeypatch,
                {
                    "error": "GRAPH_EXTRACTION_INVALID",
                    "reason": "FIELD_TYPE",
                    "sourceHash": value,
                    "unknown": RAW,
                },
            )
    assert "reason=FIELD_TYPE" in caplog.text
    assert "source_hash" not in caplog.text and RAW not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,reason",
    [
        ("GRAPH_SOURCE_CHANGED", "FIELD_TYPE"),
        (RAW, "FIELD_TYPE"),
        ("GRAPH_EXTRACTION_INVALID", RAW),
        ("GRAPH_EXTRACTION_INVALID", ["FIELD_TYPE"]),
    ],
)
async def test_parent_never_logs_hash_for_non_extraction_code_or_bad_reason(
    settings, monkeypatch, caplog, code, reason
):
    with caplog.at_level(logging.WARNING, logger="rag_engine.graph"):
        with pytest.raises(EngineError):
            await parent_run(
                settings,
                monkeypatch,
                {"error": code, "reason": reason, "sourceHash": SOURCE_HASH, "unknown": RAW},
            )
    assert "source_hash" not in caplog.text and RAW not in caplog.text


@pytest.mark.asyncio
async def test_application_node_id_is_only_source_of_error_fingerprint():
    class Extractor:
        async def _aextract(self, _node):
            error = EngineError("GRAPH_EXTRACTION_INVALID", 422)
            error.reason = "FIELD_EMPTY"
            error.source_fingerprint = RAW
            raise error

    wrapped = graph_worker.with_source_diagnostics(Extractor)()
    with pytest.raises(EngineError) as caught:
        await wrapped._aextract(SimpleNamespace(id_="c-" + SOURCE_HASH, text=RAW))
    assert caught.value.source_fingerprint == SOURCE_HASH
    assert str(caught.value) == "GRAPH_EXTRACTION_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node_id",
    [RAW, "c-" + SOURCE_HASH.upper(), "c-" + "a" * 63, "c-" + SOURCE_HASH + "\n", ["c-" + SOURCE_HASH], None],
)
async def test_readable_or_malformed_node_ids_cannot_become_diagnostics(node_id):
    class Extractor:
        async def _aextract(self, _node):
            error = EngineError("GRAPH_EXTRACTION_INVALID", 422)
            error.source_fingerprint = SOURCE_HASH
            raise error

    wrapped = graph_worker.with_source_diagnostics(Extractor)()
    with pytest.raises(EngineError) as caught:
        await wrapped._aextract(SimpleNamespace(id_=node_id, text=RAW))
    assert not hasattr(caught.value, "source_fingerprint")


@pytest.mark.asyncio
async def test_upstream_errors_and_success_counts_do_not_gain_source_diagnostics(
    settings, monkeypatch, caplog
):
    class Extractor:
        async def _aextract(self, _node):
            raise EngineError("UPSTREAM_REJECTED")

    with pytest.raises(EngineError) as caught:
        await graph_worker.with_source_diagnostics(Extractor)()._aextract(
            SimpleNamespace(id_="c-" + SOURCE_HASH)
        )
    assert not hasattr(caught.value, "source_fingerprint")
    counts = {"processedDocumentCount": 1, "nodeCount": 2, "edgeCount": 1}
    result = await parent_run(settings, monkeypatch, {"result": counts, "sourceHash": RAW})
    assert result == counts and RAW not in caplog.text


@pytest.mark.asyncio
async def test_real_validated_extractor_to_worker_stdout_to_parent_log_has_only_application_hash(
    settings, monkeypatch, capsys, caplog
):
    from llama_index.core.llms import MockLLM

    from rag_engine.graph_extract import EXTRACT_PROMPT, ValidatedSimpleLLMPathExtractor, parse_triplets
    from rag_engine.graph_models import ActiveGraphSource
    from rag_engine.graph_sources import VerifiedSource, digest

    class InvalidModel(MockLLM):
        async def apredict(self, _prompt, **_kwargs):
            return json.dumps([[{"invalid": RAW}, "uses", "Beta"]])

    source = ActiveGraphSource(
        pointId="readable-point-" + RAW,
        documentId="synthetic-document",
        documentCode="fixture",
        documentVersion=1,
        documentRevision=10,
        chunkId="synthetic-chunk",
        contentSha256="a" * 64,
    )
    verified = VerifiedSource(source, "Alpha uses Beta. " + RAW, source.model_dump())
    node = graph_worker.source_text_node(verified, "tenant-a", "kb-a", "generation-a")
    expected = digest("tenant-a", "kb-a", "generation-a", source.pointId)
    wrapped = graph_worker.with_source_diagnostics(ValidatedSimpleLLMPathExtractor)(
        llm=InvalidModel(),
        extract_prompt=EXTRACT_PROMPT,
        parse_fn=parse_triplets,
        max_paths_per_chunk=12,
        num_workers=1,
        raise_on_error=True,
    )
    with pytest.raises(EngineError) as caught:
        await wrapped.acall([node])
    assert caught.value.source_fingerprint == expected
    assert caught.value.reason in VALIDATION_REASONS
    diagnostic = run_worker_main(monkeypatch, capsys, caught.value)
    assert diagnostic["sourceHash"] == expected
    with caplog.at_level(logging.WARNING, logger="rag_engine.graph"):
        with pytest.raises(EngineError):
            await parent_run(settings, monkeypatch, diagnostic)
    assert "source_hash=" + expected in caplog.text
    assert RAW not in caplog.text and source.pointId not in caplog.text
