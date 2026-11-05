"""Neo4j 持久任务、租约与原子发布；只执行应用内固定 Cypher。"""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from neo4j import AsyncGraphDatabase, Query

from rag_engine.errors import EngineError
from rag_engine.graph_models import GraphBuild, GraphBuildRequest
from rag_engine.graph_sources import digest, document_key, source_key


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_view(row: dict) -> GraphBuild:
    return GraphBuild(**{key: row.get(key) for key in GraphBuild.model_fields if key in row})


def publication_is_current(*, sequence: int, reconcile: bool, latest_reconcile_sequence: int) -> bool:
    """完整快照只被较新完整快照替代，不取消后入队的局部文档构建。"""
    return not reconcile or sequence == latest_reconcile_sequence


def should_retire_document(*, requested_sequence: int, reconcile_sequence: int) -> bool:
    return requested_sequence <= reconcile_sequence


def barrier_allows_submission(*, document_id, revision, tombstoned, blocked_revision, epoch, restore):
    """同版本只有精确补偿票据可以恢复；普通ACTIVE窗口不能跨删除屏障。"""
    if not tombstoned or revision > blocked_revision:
        return True
    return bool(
        restore
        and restore.get("documentId") == document_id
        and restore.get("barrierEpoch") == epoch
        and restore.get("documentRevision") == blocked_revision == revision
    )


class GraphRepository:
    def __init__(self, settings, owner: str):
        self.settings = settings
        self.owner = owner
        self.driver = AsyncGraphDatabase.driver(
            settings.neo4j_url,
            auth=(settings.neo4j_username, settings.neo4j_password.get_secret_value()),
            max_connection_pool_size=4,
            connection_timeout=3,
            connection_acquisition_timeout=3,
            max_transaction_retry_time=0,
        )

    async def rows(self, query, **params):
        records, _, _ = await self.driver.execute_query(
            Query(query, timeout=self.settings.graph_query_timeout_seconds),
            parameters_=params,
            database_=self.settings.neo4j_database,
        )
        return [record.data() for record in records]

    @asynccontextmanager
    async def transaction(self):
        async with self.driver.session(database=self.settings.neo4j_database) as session:
            async with await session.begin_transaction(
                timeout=self.settings.graph_query_timeout_seconds
            ) as tx:
                yield tx
                await tx.commit()

    async def lock(self, tx):
        result = await tx.run(
            "MATCH (r:RagGraphRuntime {key:'worker'}) SET r.lock=coalesce(r.lock,0)+1 "
            "WITH r WHERE r.owner=$owner AND r.leaseUntil>timestamp() RETURN r.key AS key",
            owner=self.owner,
        )
        if not await result.single():
            raise EngineError("GRAPH_WORKER_UNAVAILABLE", 503)

    async def start(self):
        for label, key in [
            ("RagGraphRuntime", "key"),
            ("RagGraphKB", "key"),
            ("RagGraphDocument", "key"),
            ("RagGraphBarrier", "key"),
            ("RagGraphJob", "buildId"),
            ("__Node__", "id"),
        ]:
            await self.rows(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")
        await self.rows(
            "CREATE INDEX rag_graph_source IF NOT EXISTS FOR (n:Chunk) "
            "ON (n.tenant_id,n.knowledge_base,n.generation,n.point_id)"
        )
        await self.rows(
            "CREATE INDEX rag_graph_entity_scope IF NOT EXISTS FOR (n:__Entity__) "
            "ON (n.tenant_id,n.knowledge_base,n.generation)"
        )
        lease = await self.rows(
            "MERGE (r:RagGraphRuntime {key:'worker'}) SET r.lock=coalesce(r.lock,0)+1 "
            "WITH r WHERE coalesce(r.leaseUntil,0)<timestamp() OR r.owner=$owner "
            "SET r.owner=$owner,r.leaseUntil=timestamp()+$lease RETURN r.key AS key",
            owner=self.owner,
            lease=self.settings.graph_lease_seconds * 1000,
        )
        if not lease:
            raise EngineError("GRAPH_WORKER_ALREADY_RUNNING", 503)
        await self.rows(
            "MATCH (j:RagGraphJob) WHERE j.status='RUNNING' OR "
            "(j.status='QUEUED' AND (j.deadline<timestamp() OR j.sequence IS NULL)) "
            "SET j.status='FAILED',j.errorCode='GRAPH_WORKER_RESTARTED',j.updatedAt=$updated "
            "REMOVE j.requestJson,j.documentsJson",
            updated=now_iso(),
        )

    async def heartbeat(self):
        rows = await self.rows(
            "MATCH (r:RagGraphRuntime {key:'worker',owner:$owner}) WHERE r.leaseUntil>timestamp() "
            "SET r.leaseUntil=timestamp()+$lease RETURN r.key AS key",
            owner=self.owner,
            lease=self.settings.graph_lease_seconds * 1000,
        )
        if not rows:
            raise EngineError("GRAPH_WORKER_LEASE_LOST", 503)

    async def stop(self):
        try:
            await self.rows(
                "MATCH (r:RagGraphRuntime {key:'worker',owner:$owner}) SET r.leaseUntil=0",
                owner=self.owner,
            )
        finally:
            await self.driver.close()

    async def submit(self, tenant: str, request: GraphBuildRequest, build_id: str) -> GraphBuild:
        documents = {}
        for source in request.activeSources:
            documents[source.documentId] = {
                "key": document_key(tenant, request.knowledgeBase, source.documentId),
                "barrier": digest(tenant, source.documentId),
                "documentId": source.documentId,
                "revision": source.documentRevision,
                "version": source.documentVersion,
            }
        async with self.transaction() as tx:
            await self.lock(tx)
            serial = await (
                await tx.run(
                    "MATCH (r:RagGraphRuntime {key:'worker',owner:$owner}) "
                    "SET r.sequence=coalesce(r.sequence,0)+1 RETURN r.sequence AS sequence",
                    owner=self.owner,
                )
            ).single()
            sequence = serial["sequence"]
            queued = await (
                await tx.run("MATCH (j:RagGraphJob {status:'QUEUED'}) RETURN count(j) AS count")
            ).single()
            if queued["count"] >= self.settings.graph_queue_size:
                raise EngineError("GRAPH_QUEUE_FULL", 429)
            scope_key = digest(tenant, request.knowledgeBase)
            await (
                await tx.run(
                    "MERGE (k:RagGraphKB {key:$key}) ON CREATE SET k.reconcileSequence=0 "
                    "SET k.reconcileSequence=CASE WHEN $reconcile THEN $sequence "
                    "ELSE coalesce(k.reconcileSequence,0) END RETURN k.reconcileSequence AS sequence",
                    key=scope_key,
                    reconcile=request.reconcile,
                    sequence=sequence,
                )
            ).single()
            result = await tx.run(
                "UNWIND $documents AS source "
                "MERGE (d:RagGraphDocument {key:source.key}) "
                "ON CREATE SET d.revision=source.revision "
                "MERGE (b:RagGraphBarrier {key:source.barrier}) "
                "ON CREATE SET b.epoch=0,b.tombstoned=false,b.blockedRevision=0 "
                "RETURN source AS document,d.revision AS revision,properties(b) AS barrier",
                documents=list(documents.values()),
                tenant=tenant,
                kb=request.knowledgeBase,
                build=build_id,
                sequence=sequence,
            )
            stored_documents = []
            restore = request.restoreFence.model_dump() if request.restoreFence else None
            async for row in result:
                document, barrier = row["document"], row["barrier"]
                if row["revision"] > document["revision"] or not barrier_allows_submission(
                    document_id=document["documentId"],
                    revision=document["revision"],
                    tombstoned=barrier.get("tombstoned", False),
                    blocked_revision=barrier.get("blockedRevision", 0),
                    epoch=barrier.get("epoch", 0),
                    restore=restore,
                ):
                    raise EngineError("GRAPH_SOURCE_DEACTIVATED", 409)
                stored_documents.append({**document, "barrierEpoch": barrier.get("epoch", 0) + 1})
            if len(stored_documents) != len(documents):
                raise EngineError("GRAPH_SOURCE_CHANGED", 409)
            changed = await (
                await tx.run(
                    "UNWIND $documents AS source "
                    "MATCH (d:RagGraphDocument {key:source.key}),(b:RagGraphBarrier {key:source.barrier}) "
                    "WHERE b.epoch=source.barrierEpoch-1 "
                    "SET b.epoch=source.barrierEpoch,b.tombstoned=false,b.tenant=$tenant,"
                    "b.documentId=source.documentId "
                    "SET d.tenant=$tenant,d.knowledgeBase=$kb,d.documentId=source.documentId,"
                    "d.revision=source.revision,d.version=source.version,d.requestedBuild=$build,"
                    "d.requestedSequence=$sequence RETURN count(d) AS count",
                    documents=stored_documents,
                    tenant=tenant,
                    kb=request.knowledgeBase,
                    build=build_id,
                    sequence=sequence,
                )
            ).single()
            if changed["count"] != len(documents):
                raise EngineError("GRAPH_SOURCE_CHANGED", 409)
            row = await (
                await tx.run(
                    "CREATE (j:RagGraphJob {buildId:$build,tenant:$tenant,knowledgeBase:$kb,status:'QUEUED',"
                    "documentCount:$count,processedDocumentCount:0,nodeCount:0,edgeCount:0,"
                    "createdAt:$updated,updatedAt:$updated,deadline:timestamp()+$budget,sequence:$sequence,"
                    "requestJson:$request,documentsJson:$documents}) RETURN properties(j) AS job",
                    build=build_id,
                    tenant=tenant,
                    kb=request.knowledgeBase,
                    count=len(documents),
                    updated=now_iso(),
                    budget=int(self.settings.graph_build_timeout_seconds * 1000),
                    sequence=sequence,
                    request=request.model_dump_json(),
                    documents=json.dumps(stored_documents),
                )
            ).single()
            return build_view(row["job"])

    async def claim(self) -> dict | None:
        async with self.transaction() as tx:
            await self.lock(tx)
            await (
                await tx.run(
                    "MATCH (j:RagGraphJob {status:'QUEUED'}) WHERE j.deadline<=timestamp() "
                    "SET j.status='FAILED',j.errorCode='GRAPH_BUILD_TIMEOUT',j.updatedAt=$updated "
                    "REMOVE j.requestJson,j.documentsJson",
                    updated=now_iso(),
                )
            ).consume()
            row = await (
                await tx.run(
                    "MATCH (j:RagGraphJob {status:'QUEUED'}) WITH j ORDER BY j.createdAt,j.buildId LIMIT 1 "
                    "SET j.status='RUNNING',j.owner=$owner,j.updatedAt=$updated RETURN properties(j) AS job,"
                    "(j.deadline-timestamp())/1000.0 AS remaining",
                    owner=self.owner,
                    updated=now_iso(),
                )
            ).single()
            return dict(row) if row else None

    async def status(self, tenant, kb, build_id=None):
        rows = await self.rows(
            "MATCH (j:RagGraphJob {tenant:$tenant,knowledgeBase:$kb}) "
            "WHERE $build IS NULL OR j.buildId=$build "
            "RETURN properties(j) AS job ORDER BY j.createdAt DESC,j.buildId DESC LIMIT 1",
            tenant=tenant,
            kb=kb,
            build=build_id,
        )
        if not rows:
            if build_id:
                raise EngineError("GRAPH_BUILD_NOT_FOUND", 404)
            return None
        return build_view(rows[0]["job"])

    async def finish_failed(self, job, code, cancelled=False):
        await self.rows(
            "MATCH (j:RagGraphJob {buildId:$build,tenant:$tenant,knowledgeBase:$kb,owner:$owner}) "
            "WHERE j.status='RUNNING' SET j.status=$status,j.errorCode=$code,j.updatedAt=$updated "
            "REMOVE j.requestJson,j.documentsJson",
            build=job["buildId"],
            tenant=job["tenant"],
            kb=job["knowledgeBase"],
            owner=self.owner,
            status="CANCELLED" if cancelled else "FAILED",
            code=code,
            updated=now_iso(),
        )

    async def publish(self, job, counts):
        documents = json.loads(job["documentsJson"])
        request = GraphBuildRequest.model_validate_json(job["requestJson"])
        async with self.transaction() as tx:
            await self.lock(tx)
            valid = await (
                await tx.run(
                    "MATCH (j:RagGraphJob {buildId:$build,tenant:$tenant,knowledgeBase:$kb,"
                    "owner:$owner,status:'RUNNING'}),(k:RagGraphKB {key:$key}) "
                    "WHERE j.deadline>timestamp() AND j.sequence=$sequence "
                    "RETURN j.buildId AS id,coalesce(k.reconcileSequence,0) AS reconcileSequence",
                    build=job["buildId"],
                    tenant=job["tenant"],
                    kb=job["knowledgeBase"],
                    owner=self.owner,
                    key=digest(job["tenant"], job["knowledgeBase"]),
                    sequence=job["sequence"],
                )
            ).single()
            if not valid or not publication_is_current(
                sequence=job["sequence"],
                reconcile=request.reconcile,
                latest_reconcile_sequence=valid["reconcileSequence"],
            ):
                raise EngineError("GRAPH_PUBLICATION_SUPERSEDED", 409)
            valid = await (
                await tx.run(
                    "UNWIND $documents AS source "
                    "MATCH (d:RagGraphDocument {key:source.key,requestedBuild:$build,"
                    "revision:source.revision}),"
                    "(b:RagGraphBarrier {key:source.barrier,epoch:source.barrierEpoch,tombstoned:false}) "
                    "RETURN count(d) AS count",
                    documents=documents,
                    build=job["buildId"],
                )
            ).single()
            if valid["count"] != len(documents):
                raise EngineError("GRAPH_PUBLICATION_SUPERSEDED", 409)
            if request.reconcile:
                candidates = await (
                    await tx.run(
                        "MATCH (d:RagGraphDocument {tenant:$tenant,knowledgeBase:$kb}) "
                        "WHERE d.activeGeneration IS NOT NULL AND NOT d.key IN $keys "
                        "RETURN d.key AS key,coalesce(d.requestedSequence,0) AS sequence LIMIT 10001",
                        tenant=job["tenant"],
                        kb=job["knowledgeBase"],
                        keys=[value["key"] for value in documents],
                    )
                ).data()
                if len(candidates) > 10000:
                    raise EngineError("GRAPH_SCOPE_LIMIT", 409)
                retire = [
                    candidate["key"]
                    for candidate in candidates
                    if should_retire_document(
                        requested_sequence=candidate["sequence"],
                        reconcile_sequence=job["sequence"],
                    )
                ]
                await (
                    await tx.run(
                        "UNWIND $keys AS key MATCH (d:RagGraphDocument "
                        "{tenant:$tenant,knowledgeBase:$kb,key:key}) REMOVE d.activeGeneration",
                        tenant=job["tenant"],
                        kb=job["knowledgeBase"],
                        keys=retire,
                    )
                ).consume()
            await (
                await tx.run(
                    "UNWIND $keys AS key MATCH (d:RagGraphDocument {key:key}) SET d.activeGeneration=$build",
                    keys=[value["key"] for value in documents],
                    build=job["buildId"],
                )
            ).consume()
            await (
                await tx.run(
                    "MATCH (j:RagGraphJob {buildId:$build}) SET j.status='SUCCEEDED',j.updatedAt=$updated,"
                    "j.processedDocumentCount=$documents,j.nodeCount=$nodes,j.edgeCount=$edges "
                    "REMOVE j.requestJson,j.documentsJson",
                    build=job["buildId"],
                    updated=now_iso(),
                    documents=counts["processedDocumentCount"],
                    nodes=counts["nodeCount"],
                    edges=counts["edgeCount"],
                )
            ).consume()

    async def deactivate(self, tenant, document_id, document_revision):
        async with self.transaction() as tx:
            await self.lock(tx)
            barrier = await (
                await tx.run(
                    "OPTIONAL MATCH (d:RagGraphDocument {tenant:$tenant,documentId:$document}) "
                    "WITH coalesce(max(d.revision),0) AS current "
                    "WITH CASE WHEN current>$revision THEN current ELSE $revision END AS requested "
                    "MERGE (b:RagGraphBarrier {key:$key}) "
                    "SET b.blockedRevision=CASE WHEN coalesce(b.blockedRevision,0)>requested "
                    "THEN b.blockedRevision ELSE requested END,b.tombstoned=true,b.tenant=$tenant,"
                    "b.documentId=$document,b.updatedAt=$updated,b.epoch=coalesce(b.epoch,0)+1 "
                    "RETURN b.epoch AS barrierEpoch,b.blockedRevision AS documentRevision",
                    key=digest(tenant, document_id),
                    tenant=tenant,
                    document=document_id,
                    updated=now_iso(),
                    revision=document_revision,
                )
            ).single()
            await (
                await tx.run(
                    "MATCH (d:RagGraphDocument {tenant:$tenant,documentId:$document}) "
                    "REMOVE d.activeGeneration",
                    tenant=tenant,
                    document=document_id,
                )
            ).consume()
            return {
                "deactivated": True,
                "barrierEpoch": barrier["barrierEpoch"],
                "documentRevision": barrier["documentRevision"],
            }

    async def graph_rows(self, tenant, request):
        allowed = [
            {
                "documentKey": document_key(tenant, request.knowledgeBase, source.documentId),
                "pointId": source.pointId,
                "sourceKey": source_key(source),
            }
            for source in request.activeSources
        ]
        # 所有端点和事实均检查同一来源作用域；来源身份必须来自本次 Java 活动快照。
        prefix = """
        UNWIND $allowed AS a
        MATCH (d:RagGraphDocument {key:a.documentKey,tenant:$tenant,knowledgeBase:$kb})
        WHERE d.activeGeneration IS NOT NULL
        MATCH (c:Chunk {tenant_id:$tenant,knowledge_base:$kb,generation:d.activeGeneration,
                       point_id:a.pointId,source_key:a.sourceKey})-[:MENTIONS]->(s:__Entity__)
        WHERE s.tenant_id=$tenant AND s.knowledge_base=$kb AND s.generation=d.activeGeneration
          AND s.point_id=a.pointId AND s.source_key=a.sourceKey
        """
        params = {
            "tenant": tenant,
            "kb": request.knowledgeBase,
            "allowed": allowed,
            "search": request.searchText.casefold().strip(),
            "limit": self.settings.graph_max_edges + 1,
        }
        edges = await self.rows(
            prefix
            + """
        MATCH (s)-[r]->(t:__Entity__)
        WHERE r.tenant_id=$tenant AND t.tenant_id=$tenant AND r.knowledge_base=$kb AND t.knowledge_base=$kb
          AND r.generation=d.activeGeneration AND t.generation=d.activeGeneration
          AND r.point_id=a.pointId AND t.point_id=a.pointId
          AND r.source_key=a.sourceKey AND t.source_key=a.sourceKey
          AND r.predicate IS NOT NULL AND r.triplet_source_id=c.id
          AND ($search='' OR s.canonical_name CONTAINS $search OR t.canonical_name CONTAINS $search
               OR toLower(r.predicate) CONTAINS $search)
        RETURN s.canonical_name AS subject,s.display_name AS subjectName,r.predicate AS predicate,
               t.canonical_name AS object,t.display_name AS objectName,c.source_json AS sourceJson,
               r.extraction_model AS model
        ORDER BY subject,predicate,object,a.pointId LIMIT $limit
        """,
            **params,
        )
        entities = await self.rows(
            prefix
            + """
        AND ($search='' OR s.canonical_name CONTAINS $search)
        RETURN s.canonical_name AS subject,s.display_name AS subjectName,c.source_json AS sourceJson,
               s.extraction_model AS model
        ORDER BY subject,a.pointId LIMIT $limit
        """,
            **params,
        )
        truncated = (
            len(edges) > self.settings.graph_max_edges or len(entities) > self.settings.graph_max_edges
        )
        for row in edges + entities:
            row["source"] = json.loads(row.pop("sourceJson"))
        return edges[: self.settings.graph_max_edges], entities[: self.settings.graph_max_edges], truncated

    async def cleanup(self, job):
        # 仅清理不再被任何文档激活的本任务 generation；分批以免大事务占满内存。
        for _ in range(4):
            rows = await self.rows(
                "MATCH (n:__Node__ {tenant_id:$tenant,knowledge_base:$kb}) "
                "WHERE NOT EXISTS { MATCH (d:RagGraphDocument {tenant:$tenant,knowledgeBase:$kb,"
                "activeGeneration:n.generation}) WHERE d.documentId=n.document_id } "
                "AND NOT EXISTS { MATCH (j:RagGraphJob {buildId:n.generation}) "
                "WHERE j.status IN ['QUEUED','RUNNING'] } "
                "WITH n LIMIT 512 DETACH DELETE n RETURN count(*) AS count",
                tenant=job["tenant"],
                kb=job["knowledgeBase"],
            )
            if not rows or rows[0]["count"] < 512:
                await self.rows(
                    "MATCH (j:RagGraphJob {tenant:$tenant,knowledgeBase:$kb}) "
                    "WHERE j.status IN ['SUCCEEDED','FAILED','CANCELLED'] SET j.cleaned=true",
                    tenant=job["tenant"],
                    kb=job["knowledgeBase"],
                )
                return

    async def cleanup_pending(self):
        rows = await self.rows(
            "MATCH (j:RagGraphJob) WHERE j.status IN ['SUCCEEDED','FAILED','CANCELLED'] "
            "AND coalesce(j.cleaned,false)=false RETURN j.tenant AS tenant,j.knowledgeBase AS knowledgeBase "
            "ORDER BY j.createdAt LIMIT 1"
        )
        if rows:
            await self.cleanup(rows[0])
