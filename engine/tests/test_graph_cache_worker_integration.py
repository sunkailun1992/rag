"""合成来源验证检查点整合：跨构建重用、当前节点身份与同事务删除屏障。"""

import asyncio
from contextlib import asynccontextmanager

import pytest

from rag_engine import graph_worker
from rag_engine.errors import EngineError
from rag_engine.graph_models import ActiveGraphSource
from rag_engine.graph_sources import VerifiedSource, content_fingerprint

CONTENT = "Alpha uses Beta."


def verified(point):
    source = ActiveGraphSource(
        pointId=point,
        documentId="document-a",
        documentCode="synthetic",
        documentVersion=1,
        documentRevision=10,
        chunkId="chunk-" + point,
        contentSha256=content_fingerprint(CONTENT),
    )
    return VerifiedSource(source, CONTENT, source.model_dump())


def nodes_and_sources(generation):
    sources = [verified("point-a"), verified("point-b")]
    nodes = [graph_worker.source_text_node(source, "tenant-a", "kb-a", generation) for source in sources]
    return nodes, {node.id_: source for node, source in zip(nodes, sources, strict=True)}


def cached_extractor(sources, *, saved=None, fail_point=None, close_error=None, admit=True, put_error=None):
    from rag_engine.graph_cache import node_from_triplets

    calls, lifecycle = [], []
    saved = saved if saved is not None else {}

    class Base:
        max_paths_per_chunk = 8

        async def acall(self, nodes, **_kwargs):
            return [await self._aextract(node) for node in nodes]

        async def _aextract(self, node):
            calls.append(node.id_)
            if fail_point is not None and sources[node.id_].identity.pointId == fail_point:
                raise EngineError("GRAPH_EXTRACTION_UNGROUNDED")
            return node_from_triplets(node, [("Alpha", "uses", "Beta")], 8)

    class Cache:
        async def open(self):
            lifecycle.append(("open", id(asyncio.get_running_loop())))

        async def close(self):
            lifecycle.append(("close", id(asyncio.get_running_loop())))
            if close_error:
                raise close_error

        async def get(self, source, raw_text):
            lifecycle.append(("get", id(asyncio.get_running_loop())))
            assert raw_text == CONTENT
            return saved.get(source.pointId)

        async def put(self, source, raw_text, triples):
            lifecycle.append(("put", id(asyncio.get_running_loop())))
            assert raw_text == CONTENT
            if put_error:
                raise put_error
            if admit:
                saved[source.pointId] = triples
            return admit

    return graph_worker.with_validated_cache(Base, Cache, sources)(), saved, calls, lifecycle


@pytest.mark.asyncio
async def test_failed_build_retains_only_validated_results_and_next_build_uses_current_ids():
    first, first_sources = nodes_and_sources("generation-a")
    one, saved, first_calls, lifecycle = cached_extractor(first_sources, fail_point="point-b")
    with pytest.raises(EngineError, match="GRAPH_EXTRACTION_UNGROUNDED"):
        await one.acall(first)
    assert set(saved) == {"point-a"}
    assert len(first_calls) == 2
    assert {loop for _, loop in lifecycle} == {lifecycle[0][1]}
    assert lifecycle[0][0] == "open" and lifecycle[-1][0] == "close"
    second, second_sources = nodes_and_sources("generation-b")
    two, _, second_calls, _ = cached_extractor(second_sources, saved=saved)
    restored = await two.acall(second)
    assert second_calls == [second[1].id_]
    assert [node.id_ for node in restored] == [node.id_ for node in second]
    assert all(node.id_ != first[index].id_ for index, node in enumerate(restored))


@pytest.mark.asyncio
async def test_cache_policy_declines_do_not_turn_valid_extraction_into_failure():
    nodes, sources = nodes_and_sources("generation-c")
    extractor, saved, calls, _ = cached_extractor(sources, admit=False)
    result = await extractor.acall(nodes)
    assert len(result) == len(calls) == 2 and saved == {}


@pytest.mark.asyncio
async def test_cache_close_failure_does_not_mask_primary_extraction_failure():
    nodes, sources = nodes_and_sources("generation-d")
    extractor, _, _, _ = cached_extractor(
        sources, fail_point="point-a", close_error=RuntimeError("synthetic")
    )
    with pytest.raises(EngineError, match="GRAPH_EXTRACTION_UNGROUNDED"):
        await extractor.acall(nodes)


@pytest.mark.asyncio
async def test_direct_single_source_probe_does_not_open_cache():
    nodes, sources = nodes_and_sources("generation-e")
    extractor, saved, calls, lifecycle = cached_extractor(sources)
    result = await extractor._aextract(nodes[0])
    assert result.id_ == nodes[0].id_ and calls == [nodes[0].id_]
    assert lifecycle == [] and saved == {}


@pytest.mark.asyncio
async def test_unknown_node_cannot_select_a_different_source_for_cache_lookup():
    nodes, sources = nodes_and_sources("generation-f")
    extractor, _, _, _ = cached_extractor({})
    with pytest.raises(EngineError, match="GRAPH_SOURCE_CHANGED"):
        await extractor.acall(nodes)


@pytest.mark.asyncio
async def test_deactivation_purges_cache_inside_the_same_barrier_transaction(monkeypatch):
    from rag_engine import graph_cache
    from rag_engine.graph_repository import GraphRepository

    events = []

    class Result:
        async def single(self):
            return {"barrierEpoch": 3, "documentRevision": 10}

        async def consume(self):
            pass

    class Transaction:
        async def run(self, query, **_params):
            if "b.tombstoned=true" in query:
                events.append("barrier")
            elif "REMOVE d.activeGeneration" in query:
                events.append("unpublish")
            return Result()

    tx = Transaction()

    @asynccontextmanager
    async def transaction():
        events.append("begin")
        yield tx
        events.append("commit")

    async def lock(actual):
        assert actual is tx
        events.append("runtime-lock")

    async def delete_cache(actual, tenant, document):
        assert actual is tx and tenant == "tenant-a" and document == "document-a"
        events.append("cache-purge")

    repository = GraphRepository.__new__(GraphRepository)
    monkeypatch.setattr(repository, "transaction", transaction)
    monkeypatch.setattr(repository, "lock", lock)
    monkeypatch.setattr(graph_cache, "delete_document_cache_tx", delete_cache)
    result = await repository.deactivate("tenant-a", "document-a", 10)
    assert result == {"deactivated": True, "barrierEpoch": 3, "documentRevision": 10}
    assert events == ["begin", "runtime-lock", "barrier", "cache-purge", "unpublish", "commit"]


@pytest.mark.asyncio
async def test_cancelled_cache_run_preserves_cancellation_even_if_close_fails():
    nodes, sources = nodes_and_sources("generation-cancel")
    extractor, _, _, lifecycle = cached_extractor(sources, close_error=RuntimeError("synthetic"))

    async def cancelled(_node):
        raise asyncio.CancelledError()

    extractor._aextract = cancelled
    with pytest.raises(asyncio.CancelledError):
        await extractor.acall(nodes)
    assert lifecycle[-1][0] == "close"


@pytest.mark.asyncio
async def test_cache_hit_is_transformed_into_only_current_generation_scope():
    from llama_index.core.graph_stores.types import KG_NODES_KEY, KG_RELATIONS_KEY

    from rag_engine.graph_extract import ScopedGraphTransform

    nodes, sources = nodes_and_sources("generation-current")
    extractor, _, calls, _ = cached_extractor(sources, saved={"point-a": [("Alpha", "uses", "Beta")]})
    result = await extractor.acall(nodes)
    assert calls == [nodes[1].id_]
    scoped = ScopedGraphTransform(
        tenant="tenant-a",
        knowledge_base="kb-a",
        generation="generation-current",
        sources={key: value.provenance for key, value in sources.items()},
        max_triplets=8,
        extraction_model="synthetic",
    )(result)
    for node in scoped:
        assert node.id_ in sources
        for fact in node.metadata[KG_NODES_KEY] + node.metadata[KG_RELATIONS_KEY]:
            assert fact.properties["generation"] == "generation-current"
            assert fact.properties["point_id"] == sources[node.id_].identity.pointId


@pytest.mark.asyncio
async def test_nonserializable_valid_candidate_is_not_admitted(monkeypatch):
    from rag_engine import graph_cache

    nodes, sources = nodes_and_sources("generation-no-admit")
    extractor, saved, calls, lifecycle = cached_extractor(sources)
    monkeypatch.setattr(graph_cache, "triplets_from_node", lambda *_: None)
    result = await extractor.acall(nodes)
    assert len(result) == len(calls) == 2 and saved == {}
    assert not any(phase == "put" for phase, _ in lifecycle)


@pytest.mark.asyncio
async def test_known_delete_fence_from_cache_put_is_not_silently_ignored():
    nodes, sources = nodes_and_sources("generation-stale")
    extractor, saved, _, _ = cached_extractor(sources, put_error=EngineError("GRAPH_SOURCE_CHANGED", 409))
    with pytest.raises(EngineError, match="GRAPH_SOURCE_CHANGED"):
        await extractor.acall(nodes)
    assert saved == {}


@pytest.mark.asyncio
async def test_mutated_source_is_rejected_before_any_cache_lookup():
    nodes, sources = nodes_and_sources("generation-mutated")
    extractor, saved, calls, lifecycle = cached_extractor(sources)
    nodes[0].text = "Changed source"
    with pytest.raises(EngineError, match="GRAPH_SOURCE_CHANGED"):
        await extractor.acall(nodes)
    assert calls == [] and saved == {} and not any(phase == "get" for phase, _ in lifecycle)


@pytest.mark.asyncio
async def test_optional_cache_unavailable_does_not_mask_invalid_fresh_extraction():
    calls = []

    class Base:
        async def acall(self, nodes, **_kwargs):
            return [await self._aextract(node) for node in nodes]

        async def _aextract(self, node):
            calls.append(node.id_)
            raise EngineError("GRAPH_EXTRACTION_UNGROUNDED")

    nodes, sources = nodes_and_sources("generation-no-cache")
    extractor = graph_worker.with_validated_cache(Base, graph_worker._UnavailableValidatedCache, sources)()
    with pytest.raises(EngineError, match="GRAPH_EXTRACTION_UNGROUNDED"):
        await extractor.acall(nodes)
    assert calls == [nodes[0].id_]
