"""显式 opt-in 的真实 Neo4j 事务控制反例，不验证 Qdrant、模型或图语义。

仅在四个 RAG_ENGINE_NEO4J_LIVE_{URL,USERNAME,PASSWORD,DATABASE} 都由运行者
显式提供后连接。运行前必须停止本引擎图 worker；有效全局租约或其他未完成任务
均明确拒绝，不抢租约，不恢复/认领其他任务。每例使用唯一 tenant/KB/UUID 身份；
publish 的三个结果计数均为0，仅表示测试事务控制，不声称实际处理文档或抽取事实。
"""

import os
import secrets
from dataclasses import dataclass, field
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import SecretStr

from rag_engine.errors import EngineError
from rag_engine.graph_models import ActiveGraphSource, GraphBuildRequest, GraphRestoreFence
from rag_engine.graph_repository import GraphRepository
from rag_engine.graph_sources import digest, document_key

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("RAG_ENGINE_NEO4J_LIVE_URL"),
        reason="requires explicit RAG_ENGINE_NEO4J_LIVE_URL; never enables real services by default",
    ),
]

ZERO_COUNTS = {"processedDocumentCount": 0, "nodeCount": 0, "edgeCount": 0}


def live_settings():
    """不读取通用 RAG_ENGINE_* 配置，避免意外接入现有 Qdrant/模型或展示秘密。"""
    names = ["URL", "USERNAME", "PASSWORD", "DATABASE"]
    missing = [name for name in names if not os.environ.get("RAG_ENGINE_NEO4J_LIVE_" + name)]
    if missing:
        pytest.fail("live Neo4j opt-in requires variables: " + ", ".join(missing), pytrace=False)
    url = os.environ["RAG_ENGINE_NEO4J_LIVE_URL"]
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        pytest.fail("live Neo4j URL must omit credentials, path, query and fragment", pytrace=False)
    return SimpleNamespace(
        neo4j_url=url.rstrip("/"),
        neo4j_username=os.environ["RAG_ENGINE_NEO4J_LIVE_USERNAME"],
        neo4j_password=SecretStr(os.environ["RAG_ENGINE_NEO4J_LIVE_PASSWORD"]),
        neo4j_database=os.environ["RAG_ENGINE_NEO4J_LIVE_DATABASE"],
        graph_query_timeout_seconds=10,
        graph_lease_seconds=120,
        graph_queue_size=8,
        graph_build_timeout_seconds=300,
    )


@dataclass
class LiveCase:
    repository: GraphRepository
    tenant: str = field(default_factory=lambda: "graph-live-" + secrets.token_hex(10))
    kb: str = field(default_factory=lambda: "live-" + secrets.token_hex(10))
    document_ids: set[str] = field(default_factory=set)
    build_ids: set[str] = field(default_factory=set)

    def source(self, *, document_id=None, revision=100, version=1):
        document_id = document_id or str(uuid4())
        self.document_ids.add(document_id)
        return ActiveGraphSource(
            pointId=str(uuid4()),
            documentId=document_id,
            documentCode="synthetic-" + document_id,
            documentVersion=version,
            documentRevision=revision,
            chunkId=str(uuid4()),
            # repository层只存身份，不回读正文；不可把此值描述成内容真实性证据。
            contentSha256="0" * 64,
        )

    async def submit(self, sources, *, reconcile=False, restore=None):
        build_id = str(uuid4())
        self.build_ids.add(build_id)
        fence = None
        if restore:
            fence = GraphRestoreFence(
                documentId=restore["documentId"],
                barrierEpoch=restore["barrierEpoch"],
                documentRevision=restore["documentRevision"],
            )
        return await self.repository.submit(
            self.tenant,
            GraphBuildRequest(
                knowledgeBase=self.kb, activeSources=sources, reconcile=reconcile, restoreFence=fence
            ),
            build_id,
        )

    async def claim(self, build_id):
        await self.repository.heartbeat()
        foreign = await self.repository.rows(
            "MATCH (j:RagGraphJob) WHERE j.status IN ['QUEUED','RUNNING'] "
            "AND (j.tenant<>$tenant OR j.knowledgeBase<>$kb) RETURN count(j) AS count",
            tenant=self.tenant,
            kb=self.kb,
        )
        assert foreign[0]["count"] == 0, "another graph workload appeared; will not claim it"
        claimed = await self.repository.claim()
        assert claimed is not None
        job = claimed["job"]
        assert (job["buildId"], job["tenant"], job["knowledgeBase"], job["status"]) == (
            build_id,
            self.tenant,
            self.kb,
            "RUNNING",
        )
        return job

    async def publish(self, job):
        await self.repository.publish(job, dict(ZERO_COUNTS))
        status = await self.repository.status(self.tenant, self.kb, job["buildId"])
        assert status.status == "SUCCEEDED"
        assert (status.processedDocumentCount, status.nodeCount, status.edgeCount) == (0, 0, 0)
        return status

    async def document(self, source):
        rows = await self.repository.rows(
            "MATCH (d:RagGraphDocument {key:$key,tenant:$tenant,knowledgeBase:$kb}) "
            "RETURN properties(d) AS document",
            key=document_key(self.tenant, self.kb, source.documentId),
            tenant=self.tenant,
            kb=self.kb,
        )
        assert len(rows) == 1
        return rows[0]["document"]

    async def cleanup(self):
        # 所有删除均同时带自建tenant/KB或已知digest key；不删Runtime/约束/索引。
        await self.repository.rows(
            "MATCH (n:__Node__ {tenant_id:$tenant,knowledge_base:$kb}) "
            "WHERE n.generation IN $builds DETACH DELETE n",
            tenant=self.tenant,
            kb=self.kb,
            builds=list(self.build_ids),
        )
        await self.repository.rows(
            "MATCH (j:RagGraphJob {tenant:$tenant,knowledgeBase:$kb}) "
            "WHERE j.buildId IN $builds DETACH DELETE j",
            tenant=self.tenant,
            kb=self.kb,
            builds=list(self.build_ids),
        )
        await self.repository.rows(
            "MATCH (d:RagGraphDocument {tenant:$tenant,knowledgeBase:$kb}) "
            "WHERE d.key IN $keys DETACH DELETE d",
            tenant=self.tenant,
            kb=self.kb,
            keys=[document_key(self.tenant, self.kb, document) for document in self.document_ids],
        )
        await self.repository.rows(
            "MATCH (b:RagGraphBarrier {tenant:$tenant}) "
            "WHERE b.key IN $keys AND b.documentId IN $documents DETACH DELETE b",
            tenant=self.tenant,
            documents=list(self.document_ids),
            keys=[digest(self.tenant, document) for document in self.document_ids],
        )
        await self.repository.rows(
            "MATCH (k:RagGraphKB {key:$key}) DETACH DELETE k", key=digest(self.tenant, self.kb)
        )


@pytest_asyncio.fixture
async def neo4j_case():
    repository = GraphRepository(live_settings(), owner="live-test-" + str(uuid4()))
    case = LiveCase(repository)
    started = False
    try:
        # start()会恢复全局中断任务，因此在启动前额外拒绝任何已有未完成任务。
        occupied = await repository.rows(
            "OPTIONAL MATCH (r:RagGraphRuntime {key:'worker'}) "
            "RETURN coalesce(r.leaseUntil,0)>timestamp() AS leased"
        )
        if occupied and occupied[0]["leased"]:
            pytest.fail("active graph worker lease exists; stop that worker before live tests", pytrace=False)
        pending = await repository.rows(
            "MATCH (j:RagGraphJob) WHERE j.status IN ['QUEUED','RUNNING'] RETURN count(j) AS count"
        )
        if pending[0]["count"]:
            pytest.fail(
                "unfinished graph jobs exist; live tests will not recover or claim them", pytrace=False
            )
        try:
            await repository.start()
        except EngineError as error:
            if error.code == "GRAPH_WORKER_ALREADY_RUNNING":
                pytest.fail("another worker acquired the lease; live tests will not steal it", pytrace=False)
            raise
        started = True
        yield case
    finally:
        if started:
            try:
                await case.cleanup()
            finally:
                await repository.stop()
        else:
            await repository.driver.close()


async def test_live_full_running_then_new_targeted_document_both_publish(neo4j_case):
    case = neo4j_case
    source_a, source_b = case.source(), case.source()
    build_a = await case.submit([source_a], reconcile=True)
    job_a = await case.claim(build_a.buildId)
    build_b = await case.submit([source_b])
    assert (await case.repository.status(case.tenant, case.kb, build_b.buildId)).status == "QUEUED"

    await case.publish(job_a)
    # A只能退休其提交前文档；B虽尚未发布，身份/请求序号仍必须保留。
    assert (await case.document(source_b))["requestedBuild"] == build_b.buildId
    await case.publish(await case.claim(build_b.buildId))
    assert (await case.document(source_a))["activeGeneration"] == build_a.buildId
    assert (await case.document(source_b))["activeGeneration"] == build_b.buildId


async def test_live_new_full_snapshot_fences_earlier_running_full(neo4j_case):
    case = neo4j_case
    build_a = await case.submit([case.source()], reconcile=True)
    job_a = await case.claim(build_a.buildId)
    build_b = await case.submit([case.source()], reconcile=True)
    with pytest.raises(EngineError) as rejected:
        await case.repository.publish(job_a, dict(ZERO_COUNTS))
    assert rejected.value.code == "GRAPH_PUBLICATION_SUPERSEDED" and rejected.value.status == 409
    assert (await case.repository.status(case.tenant, case.kb, build_a.buildId)).status == "RUNNING"
    await case.repository.finish_failed(job_a, "GRAPH_PUBLICATION_SUPERSEDED")
    await case.publish(await case.claim(build_b.buildId))


async def test_live_deactivation_blocks_same_revision_and_old_ticket_but_current_ticket_restores(neo4j_case):
    case = neo4j_case
    source = case.source(revision=100)
    original = await case.submit([source])
    await case.publish(await case.claim(original.buildId))
    first = await case.repository.deactivate(case.tenant, source.documentId, 90)
    assert first["documentRevision"] == 100  # 不能低于已登记的文档revision。
    assert "activeGeneration" not in await case.document(source)
    with pytest.raises(EngineError) as ordinary:
        await case.submit([source])
    assert ordinary.value.code == "GRAPH_SOURCE_DEACTIVATED" and ordinary.value.status == 409

    second = await case.repository.deactivate(case.tenant, source.documentId, 100)
    assert second["barrierEpoch"] > first["barrierEpoch"]
    with pytest.raises(EngineError) as stale:
        await case.submit([source], restore={**first, "documentId": source.documentId})
    assert stale.value.code == "GRAPH_SOURCE_DEACTIVATED" and stale.value.status == 409
    restored = await case.submit([source], restore={**second, "documentId": source.documentId})
    await case.publish(await case.claim(restored.buildId))
    assert (await case.document(source))["activeGeneration"] == restored.buildId


async def test_live_higher_revision_reupload_of_same_document_can_publish(neo4j_case):
    case = neo4j_case
    source = case.source(revision=100)
    original = await case.submit([source])
    await case.publish(await case.claim(original.buildId))
    ticket = await case.repository.deactivate(case.tenant, source.documentId, 100)
    assert ticket["documentRevision"] == 100
    newer = case.source(document_id=source.documentId, revision=101, version=2)
    rebuilt = await case.submit([newer])
    await case.publish(await case.claim(rebuilt.buildId))
    document = await case.document(newer)
    assert document["revision"] == 101 and document["version"] == 2
    assert document["activeGeneration"] == rebuilt.buildId


async def test_live_second_owner_cannot_steal_valid_worker_lease(neo4j_case):
    case = neo4j_case
    rival = GraphRepository(case.repository.settings, owner="live-rival-" + str(uuid4()))
    rival_started = False
    try:
        with pytest.raises(EngineError) as rejected:
            await rival.start()
            rival_started = True
        assert rejected.value.code == "GRAPH_WORKER_ALREADY_RUNNING"
        rows = await case.repository.rows(
            "MATCH (r:RagGraphRuntime {key:'worker'}) "
            "RETURN r.owner=$owner AND r.leaseUntil>timestamp() AS retained",
            owner=case.repository.owner,
        )
        assert rows[0]["retained"] is True
    finally:
        if rival_started:
            await rival.stop()
        else:
            await rival.driver.close()
