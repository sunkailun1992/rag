"""提交重放只使用合成任务身份，不读取来源或调用模型。"""

from contextlib import asynccontextmanager

import pytest
from conftest import auth
from fastapi.testclient import TestClient

from rag_engine.app import create_app
from rag_engine.errors import EngineError
from rag_engine.graph_models import GraphBuild, GraphBuildRequest
from rag_engine.graph_repository import GraphRepository

KEY = "synthetic-submission-0001"


def request(**changes):
    return GraphBuildRequest.model_validate(
        {"knowledgeBase": "general", "activeSources": [], "reconcile": True, "submissionKey": KEY, **changes}
    )


def job():
    return dict(
        buildId="original-build",
        knowledgeBase="general",
        status="FAILED",
        documentCount=0,
        processedDocumentCount=0,
        nodeCount=0,
        edgeCount=0,
        errorCode="GRAPH_WORKER_RESTARTED",
    )


def test_submission_key_is_accepted_and_readback_is_authenticated_and_tenant_scoped(settings):
    calls = []

    class Repository:
        async def submission_status(self, tenant, kb, key):
            if key == "synthetic-submission-missing":
                raise EngineError("GRAPH_BUILD_NOT_FOUND", 404)
            calls.append((tenant, kb, key))
            return GraphBuild(**job())

    class Manager:
        repository = Repository()

        async def stop(self):
            pass

        async def submit(self, tenant, body):
            calls.append((tenant, body.knowledgeBase, body.submissionKey))
            return GraphBuild(**job())

    app = create_app(settings)
    with TestClient(app) as client:
        app.state.graph = Manager()
        response = client.post(
            "/v1/graphs/builds",
            headers=auth(settings),
            json={
                "knowledgeBase": "general",
                "activeSources": [],
                "submissionKey": KEY,
            },
        )
        assert response.status_code == 200, "a valid opaque submission key must survive the HTTP contract"
        path = f"/v1/graphs/submissions/{KEY}?knowledgeBase=general"
        assert client.get(path).status_code == 401
        assert client.get(path, headers=auth(settings, "tenant-b")).json()["buildId"] == "original-build"
        assert (
            client.get(
                "/v1/graphs/submissions/synthetic-submission-missing?knowledgeBase=general",
                headers=auth(settings),
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/v1/graphs/submissions/short?knowledgeBase=general", headers=auth(settings)
            ).status_code
            == 422
        )
        app.state.graph = None
    assert calls == [("tenant-a", "general", KEY), ("tenant-b", "general", KEY)]


def test_fingerprint_covers_every_source_identity_and_execution_option_but_not_order_or_key():
    from rag_engine.graph_repository import submission_fingerprint

    source = dict(
        pointId="point-1",
        documentId="document-1",
        documentCode="synthetic",
        documentVersion=1,
        documentRevision=100,
        chunkId="chunk-1",
        contentSha256="0" * 64,
    )
    second = {**source, "pointId": "point-2", "documentId": "document-2", "chunkId": "chunk-2"}
    original = request(activeSources=[source, second])
    fingerprint = submission_fingerprint(original)
    assert submission_fingerprint(request(activeSources=[second, source])) == fingerprint
    assert (
        submission_fingerprint(original.model_copy(update={"submissionKey": "synthetic-submission-other"}))
        == fingerprint
    )
    for field, value in {
        "pointId": "other-point",
        "documentId": "other-document",
        "documentCode": "other-code",
        "documentVersion": 2,
        "documentRevision": 101,
        "chunkId": "other-chunk",
        "contentSha256": "1" * 64,
    }.items():
        assert (
            submission_fingerprint(request(activeSources=[{**source, field: value}, second])) != fingerprint
        )
    for change in [dict(knowledgeBase="other-kb"), dict(reconcile=False)]:
        assert submission_fingerprint(request(activeSources=[source, second], **change)) != fingerprint
    local = request(activeSources=[source], reconcile=False)
    restored = request(
        activeSources=[source],
        reconcile=False,
        restoreFence={
            "documentId": "document-1",
            "documentRevision": 100,
            "barrierEpoch": 1,
        },
    )
    assert submission_fingerprint(restored) != submission_fingerprint(local)


@pytest.mark.asyncio
async def test_atomic_replay_returns_terminal_job_before_sequence_queue_or_barrier_mutations(settings):
    from rag_engine import graph_repository

    fingerprint = getattr(graph_repository, "submission_fingerprint", None)
    assert callable(fingerprint), "submission requests need a canonical immutable fingerprint"
    body = request()
    stored = {**job(), "submissionFingerprint": fingerprint(body)}
    value = object.__new__(GraphRepository)
    value.settings, value.owner = settings, "synthetic-owner"
    mutations = []

    class Result:
        async def single(self):
            return {"job": stored}

    class Transaction:
        async def run(self, query, **params):
            assert "submissionScope" in query and "MATCH" in query
            assert "SET " not in query and "CREATE " not in query
            return Result()

    @asynccontextmanager
    async def transaction():
        yield Transaction()

    async def lock(_tx):
        mutations.append("lease-check")

    value.transaction, value.lock = transaction, lock
    replay = await value.submit("tenant-a", body, "must-not-create")
    assert replay.buildId == "original-build" and replay.status == "FAILED"
    assert mutations == ["lease-check"]
    with pytest.raises(EngineError, match="GRAPH_SUBMISSION_CONFLICT") as caught:
        await value.submit("tenant-a", request(reconcile=False), "must-not-create")
    assert caught.value.status == 409


@pytest.mark.asyncio
async def test_manager_replays_before_rechecking_any_source(settings):
    from rag_engine.graph_jobs import GraphManager

    class Repository:
        on_terminal = None

        async def replay_submission(self, tenant, body):
            assert tenant == "tenant-a" and body.submissionKey == KEY
            return GraphBuild(**job())

    # Any attempt to read a source would fail: this client has no HTTP methods.
    manager = GraphManager(settings, object(), repository=Repository())
    response = await manager.submit("tenant-a", request())
    assert response.buildId == "original-build" and response.status == "FAILED"


@pytest.mark.asyncio
async def test_submit_retries_only_aborted_deadlock_with_same_identity_and_one_total_budget():
    from types import SimpleNamespace

    from neo4j.exceptions import Neo4jError, ServiceUnavailable

    value = object.__new__(GraphRepository)
    value.settings = SimpleNamespace(graph_query_timeout_seconds=1)
    original = request()
    calls = []
    deadlock = Neo4jError._hydrate_neo4j(
        code="Neo.TransientError.Transaction.DeadlockDetected", message="synthetic"
    )

    async def once(tenant, body, build_id):
        calls.append((tenant, body, build_id))
        if len(calls) < 3:
            raise deadlock
        return GraphBuild(**job())

    value._submit_once = once
    assert (await value.submit("tenant-a", original, "same-build")).buildId == "original-build"
    assert calls == [("tenant-a", original, "same-build")] * 3
    for failure in [
        ServiceUnavailable("synthetic uncertain commit"),
        Neo4jError._hydrate_neo4j(
            code="Neo.TransientError.Transaction.LockAcquisitionTimeout",
            message="synthetic",
        ),
    ]:
        calls.clear()

        async def fail(*args, failure=failure):
            calls.append(args)
            raise failure

        value._submit_once = fail
        with pytest.raises(type(failure)):
            await value.submit("tenant-a", original, "same-build")
        assert len(calls) == 1

    calls.clear()
    value.settings = SimpleNamespace(graph_query_timeout_seconds=1)

    async def aborted(*args):
        calls.append(args)
        raise deadlock

    value._submit_once = aborted
    with pytest.raises(Neo4jError):
        await value.submit("tenant-a", original, "same-build")
    assert len(calls) == 3
    calls.clear()
    value.settings = SimpleNamespace(graph_query_timeout_seconds=0.025)
    with pytest.raises(TimeoutError):
        await value.submit("tenant-a", original, "same-build")
    assert len(calls) == 2
