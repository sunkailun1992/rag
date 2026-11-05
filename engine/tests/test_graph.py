"""合成图谱反例；不访问真实模型、Neo4j、知识库或私有配置。"""

import asyncio
import hashlib
import json

import httpx
import pytest
from conftest import auth
from fastapi.testclient import TestClient

from rag_engine.app import create_app
from rag_engine.errors import EngineError


def source(index=1, **changes):
    text = f"Synthetic entity source {index}"
    value = {
        "pointId": f"00000000-0000-0000-0000-{index:012d}",
        "documentId": f"doc-{index}",
        "documentCode": f"guide-{index}",
        "documentVersion": 1,
        "documentRevision": 1000 + index,
        "chunkId": f"chunk-{index}",
        "contentSha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    value.update(changes)
    return value


def point(identity, tenant="tenant-a", kb="general"):
    fields = {
        "documentId": "document_id",
        "documentCode": "document_code",
        "documentVersion": "document_version",
        "documentRevision": "document_revision",
        "chunkId": "chunk_id",
        "contentSha256": "content_sha256",
    }
    return {
        "id": identity["pointId"],
        "payload": {
            **{fields[key]: identity[key] for key in fields},
            "tenant_id": tenant,
            "knowledge_base": kb,
            "document_status": "ACTIVE",
            "content": f"Synthetic entity source {int(identity['pointId'][-12:])}",
            "source_name": "synthetic.pdf",
            "source_locator": "page 1",
        },
    }


def test_graph_api_disabled_is_explicit_and_requires_auth(settings):
    with TestClient(create_app(settings)) as client:
        body = {"knowledgeBase": "general", "activeSources": [], "reconcile": True}
        assert client.post("/v1/graphs/builds", json=body).status_code == 401
        response = client.post("/v1/graphs/builds", json=body, headers=auth(settings))
        assert response.status_code == 503
        assert response.json() == {"error": {"code": "GRAPH_NOT_CONFIGURED"}}


def test_source_contract_rejects_duplicates_mixed_revisions_and_client_tenant():
    from pydantic import ValidationError

    from rag_engine.graph_models import GraphBuildRequest

    base = {"knowledgeBase": "general", "activeSources": [source()], "reconcile": False}
    assert GraphBuildRequest(**base).activeSources[0].documentRevision == 1001
    for sources in [[source(), source()], [source(), source(2, documentId="doc-1")]]:
        with pytest.raises(ValidationError):
            GraphBuildRequest(**{**base, "activeSources": sources})
    with pytest.raises(ValidationError):
        GraphBuildRequest(**base, tenantId="other")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["tenant", "version", "revision", "status", "hash", "missing"])
async def test_qdrant_source_recheck_rejects_stale_or_foreign_snapshot(settings, change):
    from rag_engine.graph_models import ActiveGraphSource
    from rag_engine.graph_sources import read_active_sources

    identity = source()
    value = point(identity)
    keys = {
        "tenant": ("tenant_id", "other"),
        "version": ("document_version", 2),
        "revision": ("document_revision", 1002),
        "status": ("document_status", "DELETED"),
        "hash": ("content_sha256", "0" * 64),
    }
    if change in keys:
        key, replacement = keys[change]
        value["payload"][key] = replacement

    def upstream(request):
        assert request.url.path.endswith("/points")
        assert json.loads(request.content) == {
            "ids": [identity["pointId"]],
            "with_payload": True,
            "with_vector": False,
        }
        return httpx.Response(200, json={"result": [] if change == "missing" else [value]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        with pytest.raises(EngineError, match="GRAPH_SOURCE_CHANGED"):
            await read_active_sources(
                [ActiveGraphSource(**identity)], "tenant-a", "general", settings, client
            )


@pytest.mark.asyncio
async def test_qdrant_sources_are_read_without_writing_or_embedding(settings):
    from rag_engine.graph_models import ActiveGraphSource
    from rag_engine.graph_sources import read_active_sources

    identities = [source(index) for index in range(1, 66)]
    calls = []

    def upstream(request):
        body = json.loads(request.content)
        calls.append(body)
        selected = [identity for identity in identities if identity["pointId"] in body["ids"]]
        return httpx.Response(200, json={"result": [point(identity) for identity in reversed(selected)]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        values = await read_active_sources(
            [ActiveGraphSource(**item) for item in identities], "tenant-a", "general", settings, client
        )
    assert len(calls) == 2
    assert [value.identity.pointId for value in values] == [item["pointId"] for item in identities]


@pytest.mark.parametrize(
    "text",
    [
        "位置: Sheet １ / 第９行 / 片段２\r\n- 序号: ９９\r\n- 名称: Ａ\t  Ｂ",
        "位置: Sheet 1 / 第77行\n- 行号: 3\n- 记录编号: abc\n- 名称: A B  ",
    ],
)
def test_content_fingerprint_matches_java_semantic_deduplication(text):
    from rag_engine.graph_sources import content_fingerprint

    expected = hashlib.sha256("位置: Sheet 1\n- 名称: A B".encode()).hexdigest()
    assert content_fingerprint(text) == expected


@pytest.mark.asyncio
async def test_qdrant_source_provenance_preserves_safe_pdf_metadata(settings):
    from rag_engine.graph_models import ActiveGraphSource
    from rag_engine.graph_sources import read_active_sources

    identity = source()
    value = point(identity)
    value["payload"]["source_metadata"] = {
        "page": 1,
        "bbox": [0, 1, 20, 30],
        "block_type": "table",
        "ocr_capabilities": {"textRecognition": True, "tableStructure": False, "layoutAnalysis": True},
        "tenant_id": "must-not-copy",
        "private": "must-not-copy",
    }
    value["payload"]["duplicate_locators"] = ["page 1", "page 2"]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"result": [value]}))
    ) as client:
        verified = await read_active_sources(
            [ActiveGraphSource(**identity)], "tenant-a", "general", settings, client
        )
    metadata = verified[0].provenance["sourceMetadata"]
    assert metadata["page"] == 1 and metadata["bbox"] == [0, 1, 20, 30]
    assert metadata["ocr_capabilities"]["tableStructure"] is False
    assert "private" not in metadata and "tenant_id" not in metadata
    assert verified[0].provenance["duplicateLocators"] == ["page 1", "page 2"]


def test_real_llama_extractor_scopes_same_names_and_preserves_each_source():
    from llama_index.core import PropertyGraphIndex
    from llama_index.core.graph_stores import SimplePropertyGraphStore
    from llama_index.core.indices.property_graph import SimpleLLMPathExtractor
    from llama_index.core.llms import MockLLM
    from llama_index.core.schema import TextNode

    from rag_engine.graph_extract import ScopedGraphTransform, parse_triplets

    class Triples(MockLLM):
        async def apredict(self, _prompt, **_kwargs):
            return '[["Alpha","uses","Beta"]]'

    store = SimplePropertyGraphStore()
    for tenant, generation, index in [("tenant-a", "g1", 1), ("tenant-b", "g1", 1), ("tenant-a", "g2", 2)]:
        identity = source(index)
        node = TextNode(id_=f"{tenant}-{generation}-{index}", text="Alpha uses Beta.")
        transform = ScopedGraphTransform(
            tenant=tenant,
            knowledge_base="general",
            generation=generation,
            sources={node.id_: identity},
            max_triplets=4,
        )
        PropertyGraphIndex(
            nodes=[node],
            property_graph_store=store,
            embed_kg_nodes=False,
            kg_extractors=[
                SimpleLLMPathExtractor(llm=Triples(), parse_fn=parse_triplets, raise_on_error=True),
                transform,
            ],
        )
    entities = [value for value in store.get() if value.properties.get("canonical_name")]
    assert len(entities) == 6
    assert len({entity.id for entity in entities}) == 6
    assert {entity.properties["tenant_id"] for entity in entities} == {"tenant-a", "tenant-b"}
    triplets = store.get_triplets(ids=[entity.id for entity in entities])
    assert len(triplets) == 3
    assert all(rel.properties["source_key"] for _, rel, _ in triplets)


def test_extraction_parser_rejects_unbounded_or_invalid_output():
    from rag_engine.graph_extract import parse_triplets

    for output in [
        "not triples with private text",
        '[["a", "r", "b", "tenant"]]',
        '[["a", "r", "b"]]' * 1000,
    ]:
        with pytest.raises(ValueError, match="GRAPH_EXTRACTION_INVALID"):
            parse_triplets(output)
    assert parse_triplets('```json\n[["甲", "属于", "乙"]]\n```') == [("甲", "属于", "乙")]


def test_invented_entity_fails_real_llama_scope_transform_instead_of_empty_success():
    from llama_index.core import PropertyGraphIndex
    from llama_index.core.graph_stores import SimplePropertyGraphStore
    from llama_index.core.indices.property_graph import SimpleLLMPathExtractor
    from llama_index.core.llms import MockLLM
    from llama_index.core.schema import TextNode

    from rag_engine.graph_extract import ScopedGraphTransform, parse_triplets

    class Invented(MockLLM):
        async def apredict(self, _prompt, **_kwargs):
            return '[["Invented","uses","Beta"]]'

    store = SimplePropertyGraphStore()
    node = TextNode(id_="synthetic", text="Alpha uses Beta.")
    transform = ScopedGraphTransform(
        tenant="tenant-a",
        knowledge_base="general",
        generation="g1",
        sources={node.id_: source()},
        max_triplets=4,
    )
    with pytest.raises(EngineError, match="GRAPH_EXTRACTION_UNGROUNDED"):
        PropertyGraphIndex(
            nodes=[node],
            property_graph_store=store,
            embed_kg_nodes=False,
            kg_extractors=[
                SimpleLLMPathExtractor(llm=Invented(), parse_fn=parse_triplets, raise_on_error=True),
                transform,
            ],
        )
    assert store.get() == []


def test_official_llama_serializer_preserves_document_identity_for_published_chunk_cleanup():
    from llama_index.core import PropertyGraphIndex
    from llama_index.core.graph_stores import SimplePropertyGraphStore
    from llama_index.core.indices.property_graph import SimpleLLMPathExtractor
    from llama_index.core.llms import MockLLM

    from rag_engine.graph_extract import ScopedGraphTransform, parse_triplets
    from rag_engine.graph_models import ActiveGraphSource
    from rag_engine.graph_sources import VerifiedSource
    from rag_engine.graph_worker import source_text_node

    class Triples(MockLLM):
        async def apredict(self, _prompt, **_kwargs):
            return '[["Alpha","uses","Beta"]]'

    identity = ActiveGraphSource(**source())
    verified = VerifiedSource(identity, "Alpha uses Beta.", identity.model_dump())
    node = source_text_node(verified, "tenant-a", "general", "published-generation")
    transform = ScopedGraphTransform(
        tenant="tenant-a",
        knowledge_base="general",
        generation="published-generation",
        sources={node.id_: verified.provenance},
        max_triplets=4,
    )
    store = SimplePropertyGraphStore()
    PropertyGraphIndex(
        nodes=[node],
        property_graph_store=store,
        embed_kg_nodes=False,
        kg_extractors=[
            SimpleLLMPathExtractor(llm=Triples(), parse_fn=parse_triplets, raise_on_error=True),
            transform,
        ],
    )
    # 官方upsert_llama_nodes确实走node_to_metadata_dict，不用手造Chunk metadata掩盖覆盖。
    chunk = store.get(ids=[node.id_])[0]
    assert chunk.properties["document_id"] == "doc-1"
    assert chunk.properties["document_version"] == 1 and chunk.properties["document_revision"] == 1001
    # 已发布清理按document_id保留整个generation；Chunk不能被误判孤立，MENTIONS才不会被删。
    active_documents = {"doc-1": "published-generation"}
    removable = [
        value
        for value in store.get()
        if active_documents.get(value.properties["document_id"]) != value.properties["generation"]
    ]
    assert removable == []


def test_evidence_excerpt_includes_late_entities_and_marks_omission():
    from rag_engine.graph_sources import evidence_excerpt

    text = "a" * 2000 + " Alpha uses Beta. " + "z" * 2000
    excerpt = evidence_excerpt(text, ["Alpha", "Beta"])
    assert "Alpha uses Beta" in excerpt["evidence"]
    assert len(excerpt["evidence"]) <= 1200
    assert excerpt["evidenceTruncated"] is True


def test_graph_aggregation_preserves_many_sources_and_never_returns_dangling_edges():
    from rag_engine.graph_query import aggregate_graph

    rows = []
    for index in range(1, 11):
        rows.append(
            {
                "subject": "alpha",
                "subjectName": "Alpha",
                "predicate": "uses",
                "object": "beta",
                "objectName": "Beta",
                "source": source(index),
            }
        )
    result = aggregate_graph(rows, [], "general", "DETAIL", 500, 1000)
    assert len(result["nodes"]) == 2
    assert len(result["edges"]) == 1
    for item in result["nodes"] + result["edges"]:
        assert item["properties"]["sourceCount"] == 10
        assert item["properties"]["sourceTruncated"] is True
        assert len(item["properties"]["sources"]) == 8
    small = aggregate_graph(rows, [], "general", "DETAIL", 1, 1000)
    assert small["edges"] == [] and small["truncated"]


def test_display_entity_ids_include_tenant_and_knowledge_base():
    from rag_engine.graph_query import aggregate_graph

    row = {"subject": "alpha", "subjectName": "Alpha", "source": source()}
    ids = []
    for tenant, kb in [("a", "kb"), ("b", "kb"), ("a", "other")]:
        result = aggregate_graph([], [row], kb, "DETAIL", 10, 10, tenant)
        ids.append(result["nodes"][0]["id"])
    assert len(set(ids)) == 3


def test_dense_display_graph_obeys_java_limit_times_four_edge_budget():
    from rag_engine.graph_query import aggregate_graph

    pairs = [(left, right) for left in range(20) for right in range(20) if left != right][:81]
    rows = [
        {
            "subject": f"n{left}",
            "subjectName": f"n{left}",
            "object": f"n{right}",
            "objectName": f"n{right}",
            "predicate": "uses",
            "source": source(),
        }
        for left, right in pairs
    ]
    graph = aggregate_graph(rows, [], "general", "DETAIL", 20, 2000, "tenant-a")
    assert graph["nodeCount"] == 20
    assert graph["edgeCount"] == 80 and graph["truncated"] is True
    node_ids = {node["id"] for node in graph["nodes"]}
    assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in graph["edges"])


def test_reconcile_publication_preserves_later_queued_document():
    from rag_engine.graph_repository import publication_is_current, should_retire_document

    # A完整构建先提交且RUNNING，B的新文档随后入队；A完成不能废弃B。
    assert publication_is_current(sequence=1, reconcile=True, latest_reconcile_sequence=1)
    assert not should_retire_document(requested_sequence=2, reconcile_sequence=1)
    assert publication_is_current(sequence=2, reconcile=False, latest_reconcile_sequence=1)
    # 更晚完整快照supersede旧完整构建，但目标文档的新请求不受影响。
    assert not publication_is_current(sequence=1, reconcile=True, latest_reconcile_sequence=3)
    assert publication_is_current(sequence=2, reconcile=False, latest_reconcile_sequence=3)
    assert should_retire_document(requested_sequence=1, reconcile_sequence=3)


def test_delete_barrier_requires_new_revision_or_exact_restore_ticket():
    from rag_engine.graph_repository import barrier_allows_submission

    base = dict(document_id="doc-1", revision=100, tombstoned=True, blocked_revision=100, epoch=3)
    # delete已立屏障但Qdrant尚ACTIVE时，普通同版本sync不能复活。
    assert not barrier_allows_submission(**base, restore=None)
    assert barrier_allows_submission(**{**base, "revision": 101}, restore=None)
    ticket = {"documentId": "doc-1", "barrierEpoch": 3, "documentRevision": 100}
    assert barrier_allows_submission(**base, restore=ticket)
    # 新delete递增epoch后，旧补偿票据绝不可清除它。
    assert not barrier_allows_submission(**{**base, "epoch": 4}, restore=ticket)
    assert not barrier_allows_submission(**{**base, "blocked_revision": 101}, restore=ticket)


@pytest.mark.asyncio
async def test_qwen_llama_adapter_uses_bounded_compatible_chat_without_embeddings(settings):
    from rag_engine.graph_extract import QwenGraphLLM

    def upstream(request):
        assert request.url.path == "/compatible-mode/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == settings.graph_model and body["enable_thinking"] is False
        assert body["response_format"]["type"] == "json_schema"
        assert body["messages"] == [{"role": "user", "content": "Synthetic source"}]
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"triples":[]}'}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        response = await QwenGraphLLM(settings=settings, client=client).acomplete("Synthetic source")
    assert response.text == "[]"


@pytest.mark.asyncio
async def test_graph_query_rechecks_sources_after_database_read(settings):
    from rag_engine.graph_jobs import GraphManager
    from rag_engine.graph_models import GraphQueryRequest

    identity = source()
    value = point(identity)
    calls = 0

    def upstream(_request):
        nonlocal calls
        calls += 1
        if calls == 2:
            value["payload"]["document_status"] = "DELETED"
        return httpx.Response(200, json={"result": [value]})

    class Repository:
        async def graph_rows(self, tenant, request):
            return [], [{"subject": "alpha", "subjectName": "Alpha", "source": identity}], False

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        manager = GraphManager(settings, client, Repository())
        with pytest.raises(EngineError, match="GRAPH_SOURCE_CHANGED"):
            await manager.query(
                "tenant-a", GraphQueryRequest(knowledgeBase="general", activeSources=[identity])
            )
    assert calls == 2


@pytest.mark.asyncio
async def test_graph_query_rejects_foreign_stored_source_before_return(settings):
    from rag_engine.graph_jobs import GraphManager
    from rag_engine.graph_models import GraphQueryRequest

    identity = source()
    foreign = source(documentRevision=9999)

    class Repository:
        async def graph_rows(self, tenant, request):
            return [], [{"subject": "alpha", "subjectName": "Alpha", "source": foreign}], False

    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"result": [point(identity)]}))
    async with httpx.AsyncClient(transport=transport) as client:
        manager = GraphManager(settings, client, Repository())
        with pytest.raises(EngineError, match="GRAPH_SOURCE_CHANGED"):
            await manager.query(
                "tenant-a", GraphQueryRequest(knowledgeBase="general", activeSources=[identity])
            )


@pytest.mark.asyncio
async def test_parent_cancellation_marks_job_cancelled_and_does_not_publish(settings, monkeypatch):
    from rag_engine import graph_jobs

    published, failures = [], []
    job = {"buildId": "synthetic", "tenant": "tenant-a", "knowledgeBase": "general", "requestJson": "{}"}

    class Repository:
        async def claim(self):
            return {"job": job, "remaining": 5}

        async def finish_failed(self, actual, code, cancelled=False):
            failures.append((actual["buildId"], code, cancelled))

        async def publish(self, *_args):
            published.append(True)

    async def cancelled(*_args):
        raise asyncio.CancelledError()

    monkeypatch.setattr(graph_jobs, "run_worker", cancelled)
    manager = graph_jobs.GraphManager(settings, None, Repository())
    with pytest.raises(asyncio.CancelledError):
        await manager.pump()
    assert published == []
    assert failures == [("synthetic", "GRAPH_WORKER_STOPPED", True)]


@pytest.mark.asyncio
async def test_graph_worker_timeout_kills_and_reaps_without_late_success(settings, monkeypatch):
    from rag_engine import graph_jobs

    class Process:
        returncode = None
        killed = False
        waited = False

        async def communicate(self, _input):
            await asyncio.sleep(10)

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.waited = True

    process = Process()

    async def spawn(*_args, **_kwargs):
        return process

    monkeypatch.setattr(graph_jobs.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(EngineError, match="GRAPH_BUILD_TIMEOUT"):
        await graph_jobs.run_worker({"buildId": "synthetic"}, settings, 0.01)
    assert process.killed and process.waited


def test_graph_api_preserves_trusted_scope_and_async_status_contract(settings):
    from rag_engine.graph_models import GraphBuild
    from rag_engine.graph_query import aggregate_graph

    calls = []
    build = GraphBuild(
        buildId="synthetic",
        knowledgeBase="general",
        status="QUEUED",
        documentCount=1,
        processedDocumentCount=0,
        nodeCount=0,
        edgeCount=0,
    )

    class Repository:
        async def status(self, tenant, kb, build_id):
            calls.append(("status", tenant, kb, build_id))
            return build

        async def deactivate(self, tenant, document, revision):
            calls.append(("deactivate", tenant, document))
            return {"deactivated": True, "barrierEpoch": 1, "documentRevision": revision}

    class Manager:
        repository = Repository()

        async def submit(self, tenant, request):
            calls.append(("submit", tenant, request.knowledgeBase))
            return build

        async def query(self, tenant, request):
            row = {"subject": "alpha", "subjectName": "Alpha", "source": source()}
            return aggregate_graph([], [row], request.knowledgeBase, request.viewMode, 10, 10, tenant)

    app = create_app(settings)
    with TestClient(app) as client:
        app.state.graph = Manager()
        body = {"knowledgeBase": "general", "activeSources": [source()], "reconcile": False}
        result = client.post("/v1/graphs/builds", json=body, headers=auth(settings))
        assert result.status_code == 200 and result.json()["status"] == "QUEUED"
        graph = client.post(
            "/v1/graphs/queries",
            json={key: body[key] for key in ["knowledgeBase", "activeSources"]},
            headers=auth(settings),
        ).json()
        assert graph["backend"] == "neo4j" and graph["build"] is None
        assert graph["nodes"][0]["properties"]["sources"] == [source()]
        assert (
            client.get(
                "/v1/graphs/builds/synthetic?knowledgeBase=general", headers=auth(settings)
            ).status_code
            == 200
        )
        assert client.post(
            "/v1/graphs/deactivations",
            json={"documentId": "doc-1", "documentRevision": 1001},
            headers=auth(settings),
        ).json() == {"deactivated": True, "barrierEpoch": 1, "documentRevision": 1001}
        # lifespan 只关闭其真实拥有的 manager，不把测试边界替身当资源所有者。
        app.state.graph = None
    assert calls == [
        ("submit", "tenant-a", "general"),
        ("status", "tenant-a", "general", "synthetic"),
        ("deactivate", "tenant-a", "doc-1"),
    ]


def test_graph_body_has_independent_large_snapshot_limit(settings):
    body = {
        "knowledgeBase": "general",
        "activeSources": [source(index) for index in range(1, 301)],
        "reconcile": True,
    }
    assert len(json.dumps(body).encode()) > 65536
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/graphs/builds", json=body, headers=auth(settings))
    assert response.status_code == 503 and response.json()["error"]["code"] == "GRAPH_NOT_CONFIGURED"
