"""显式连接真实Neo4j，仅用随机标签运行提交事务；不占用现有worker/图数据。"""

import asyncio
import os
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import SecretStr

from rag_engine.errors import EngineError
from rag_engine.graph_models import ActiveGraphSource, GraphBuildRequest
from rag_engine.graph_repository import GraphRepository

pytestmark = pytest.mark.skipif(
    not os.environ.get("RAG_GRAPH_SUBMISSION_NEO4J_URL"),
    reason="requires explicit isolated graph-submission Neo4j connection",
)

LABELS = (
    "RagGraphRuntime",
    "RagGraphKB",
    "RagGraphDocument",
    "RagGraphBarrier",
    "RagGraphJob",
    "__Node__",
    "__Entity__",
    "Chunk",
)


class IsolatedRepository(GraphRepository):
    """只替换数据库I/O中的固定schema名称，原子的submit/claim/start代码保持真实。"""

    def __init__(self, settings, prefix):
        assert re.fullmatch(r"GraphSubmissionTest_[a-f0-9]{32}_", prefix)
        super().__init__(settings, "synthetic-" + uuid4().hex)
        self.prefix = prefix
        self.schema_names = set()

    def scoped(self, query):
        for label in LABELS:
            query = re.sub(r":" + re.escape(label) + r"\b", ":" + self.prefix + label, query)
        if query.startswith("CREATE CONSTRAINT IF NOT EXISTS"):
            label, prop = re.search(r"FOR \(n:(\w+)\) REQUIRE n\.(\w+)", query).groups()
            name = label + "_" + prop
            self.schema_names.add(("CONSTRAINT", name))
            query = query.replace("CREATE CONSTRAINT IF", "CREATE CONSTRAINT " + name + " IF", 1)
        if query.startswith("CREATE INDEX "):
            original = query.split()[2]
            name = self.prefix + original
            self.schema_names.add(("INDEX", name))
            query = query.replace("CREATE INDEX " + original, "CREATE INDEX " + name, 1)
        return query

    async def rows(self, query, **params):
        return await super().rows(self.scoped(query), **params)

    @asynccontextmanager
    async def transaction(self):
        async with super().transaction() as tx:
            repository = self

            class ScopedTransaction:
                async def run(self, query, **params):
                    return await tx.run(repository.scoped(query), **params)

            yield ScopedTransaction()

    async def cleanup(self):
        await super().rows(
            "MATCH (n) WHERE any(label IN labels(n) WHERE label IN $labels) DETACH DELETE n",
            labels=[self.prefix + label for label in LABELS],
        )
        for kind, name in self.schema_names:
            assert name.startswith(self.prefix) and re.fullmatch(r"[A-Za-z0-9_]+", name)
            await super().rows(f"DROP {kind} {name} IF EXISTS")


@pytest_asyncio.fixture
async def isolated():
    names = ("URL", "USERNAME", "PASSWORD", "DATABASE")
    values = {name: os.environ.get("RAG_GRAPH_SUBMISSION_NEO4J_" + name) for name in names}
    if not all(values.values()):
        pytest.fail("all isolated Neo4j variables must be explicitly supplied", pytrace=False)
    settings = SimpleNamespace(
        neo4j_url=values["URL"],
        neo4j_username=values["USERNAME"],
        neo4j_password=SecretStr(values["PASSWORD"]),
        neo4j_database=values["DATABASE"],
        graph_query_timeout_seconds=10,
        graph_lease_seconds=120,
        graph_queue_size=1,
        graph_build_timeout_seconds=300,
    )
    repository = IsolatedRepository(settings, "GraphSubmissionTest_" + uuid4().hex + "_")
    print("isolated Neo4j namespace: " + repository.prefix)
    try:
        await repository.start()
        yield repository
    finally:
        try:
            await repository.cleanup()
        finally:
            await repository.stop()


def body(**changes):
    source = ActiveGraphSource(
        pointId="point-1",
        documentId="document-1",
        documentCode="synthetic",
        documentVersion=1,
        documentRevision=100,
        chunkId="chunk-1",
        contentSha256="0" * 64,
    )
    return GraphBuildRequest(
        knowledgeBase="synthetic-kb",
        activeSources=[source],
        reconcile=True,
        submissionKey="synthetic-submission-0001",
        **changes,
    )


async def test_live_concurrent_replay_at_full_queue_has_one_job_sequence_and_barrier(isolated):
    request = body()
    builds = await asyncio.gather(
        *(isolated.submit("synthetic-tenant", request, uuid4().hex) for _ in range(8)),
        return_exceptions=True,
    )
    errors = [getattr(item, "code", type(item).__name__) for item in builds if isinstance(item, Exception)]
    assert not errors, errors
    assert len({item.buildId for item in builds}) == 1
    rows = await isolated.rows("MATCH (j:RagGraphJob) RETURN count(j) AS count")
    assert rows == [{"count": 1}]
    assert await isolated.rows("MATCH (r:RagGraphRuntime) RETURN r.sequence AS sequence") == [{"sequence": 1}]
    assert await isolated.rows("MATCH (b:RagGraphBarrier) RETURN b.epoch AS epoch") == [{"epoch": 1}]
    assert (
        await isolated.submission_status("synthetic-tenant", "synthetic-kb", request.submissionKey)
    ).buildId == builds[0].buildId
    with pytest.raises(EngineError, match="GRAPH_SUBMISSION_CONFLICT"):
        await isolated.submit(
            "synthetic-tenant", request.model_copy(update={"reconcile": False}), uuid4().hex
        )
    for tenant, kb in [("other-tenant", "synthetic-kb"), ("synthetic-tenant", "other-kb")]:
        with pytest.raises(EngineError, match="GRAPH_BUILD_NOT_FOUND"):
            await isolated.submission_status(tenant, kb, request.submissionKey)


async def test_live_new_worker_recovers_original_submission_after_running_interruption(isolated):
    request = body()
    original = await isolated.submit("synthetic-tenant", request, uuid4().hex)
    claimed = await isolated.claim()
    assert claimed["job"]["buildId"] == original.buildId
    await isolated.stop()
    restarted = IsolatedRepository(isolated.settings, isolated.prefix)
    try:
        await restarted.start()
        replay = await restarted.submit("synthetic-tenant", request, uuid4().hex)
        assert (replay.buildId, replay.status, replay.errorCode) == (
            original.buildId,
            "FAILED",
            "GRAPH_WORKER_RESTARTED",
        )
        assert await restarted.rows("MATCH (j:RagGraphJob) RETURN count(j) AS count") == [{"count": 1}]
        assert await restarted.rows("MATCH (r:RagGraphRuntime) RETURN r.sequence AS sequence") == [
            {"sequence": 1}
        ]
    finally:
        await restarted.cleanup()
        await restarted.stop()
        # fixture仍持有已关闭driver；移交新的可关闭driver仅用于它的幂等清理。
        isolated.driver = IsolatedRepository(isolated.settings, isolated.prefix).driver


async def test_live_competing_payloads_atomically_accept_only_one_input(isolated):
    original = body()
    competing = original.model_copy(update={"reconcile": False})
    results = await asyncio.gather(
        isolated.submit("synthetic-tenant", original, uuid4().hex),
        isolated.submit("synthetic-tenant", competing, uuid4().hex),
        return_exceptions=True,
    )
    successes = [item for item in results if not isinstance(item, Exception)]
    failures = [item for item in results if isinstance(item, Exception)]
    assert len(successes) == len(failures) == 1
    assert isinstance(failures[0], EngineError)
    assert (str(failures[0]), failures[0].status) == ("GRAPH_SUBMISSION_CONFLICT", 409)
    assert await isolated.rows("MATCH (j:RagGraphJob) RETURN count(j) AS count") == [{"count": 1}]
    assert await isolated.rows("MATCH (r:RagGraphRuntime) RETURN r.sequence AS sequence") == [{"sequence": 1}]
    assert await isolated.rows("MATCH (b:RagGraphBarrier) RETURN b.epoch AS epoch") == [{"epoch": 1}]
    winner = original if not isinstance(results[0], Exception) else competing
    replay = await isolated.submit("synthetic-tenant", winner, uuid4().hex)
    assert replay.buildId == successes[0].buildId
