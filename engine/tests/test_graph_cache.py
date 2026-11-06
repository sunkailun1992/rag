"""合成缓存回归；数据库事务竞态另由显式Neo4j接受脚本验证。"""

import asyncio
import copy
import hashlib
import json
import subprocess
import sys
from contextlib import asynccontextmanager

import pytest
from llama_index.core.graph_stores.types import KG_NODES_KEY, KG_RELATIONS_KEY
from llama_index.core.schema import TextNode

from rag_engine import graph_cache, graph_extract
from rag_engine.errors import EngineError
from rag_engine.graph_models import ActiveGraphSource
from rag_engine.graph_sources import content_fingerprint, digest, document_key

TEXT = "位置: 合成 / 第1行\n方案甲用于目标乙。"
TRIPLES = [("方案甲", "用于", "目标乙")]


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    async def single(self):
        return self.rows[0] if self.rows else None

    async def consume(self):
        pass


class MemoryDriver:
    def __init__(self):
        self.entries = {}
        self.contexts = {}
        self.states = {}
        self.now = 1000
        self.closed = False
        self.unavailable = False
        self.lock = asyncio.Lock()
        self.events = []

    def context(self, tenant="tenant-a", kb="kb-a", build="build-a", revision=7, epoch=2):
        document = {
            "key": document_key(tenant, kb, "doc-a"),
            "barrier": digest(tenant, "doc-a"),
            "documentId": "doc-a",
            "revision": revision,
            "version": 1,
            "barrierEpoch": epoch,
        }
        self.contexts[(tenant, kb, build)] = json.dumps([document])
        self.states[document["barrier"]] = {
            "epoch": epoch,
            "tombstoned": False,
            "requestedBuild": build,
            "revision": revision,
            "version": 1,
            "status": "RUNNING",
            "deadline": 999999999,
            "now": self.now,
        }

    async def execute_query(self, query, *, parameters_, **_kwargs):
        if self.unavailable:
            raise RuntimeError("Synthetic private transport details")
        text, p = query.text, parameters_
        if "RETURN j.documentsJson" in text:
            value = self.contexts.get((p["tenant"], p["kb"], p["build"]))
            rows = [] if value is None else [{"documents": value}]
        elif text.startswith("CREATE"):
            rows = []
        elif "DELETE c" in text:
            value = self.entries.get(p["key"])
            if value and value["tenant"] == p["tenant"] and value["kb"] == p["kb"]:
                del self.entries[p["key"]]
            rows = []
        else:
            raise AssertionError("Unexpected cache query")

        class Record:
            def __init__(self, row):
                self.row = row

            def data(self):
                return self.row

        return [Record(row) for row in rows], None, None

    @asynccontextmanager
    async def session(self, **_kwargs):
        driver = self

        class Session:
            async def begin_transaction(self, **_kwargs):
                return Transaction(driver)

        yield Session()

    async def close(self):
        self.closed = True


class Transaction:
    def __init__(self, driver):
        self.driver, self.committed = driver, False

    async def __aenter__(self):
        await self.driver.lock.acquire()
        self.before = copy.deepcopy(self.driver.entries)
        return self

    async def __aexit__(self, *_args):
        if not self.committed:
            self.driver.entries = self.before
        self.driver.lock.release()

    async def commit(self):
        self.committed = True

    async def run(self, query, **p):
        d = self.driver
        if d.unavailable:
            raise RuntimeError("Synthetic private transport details")
        if "MATCH (b:RagGraphBarrier" in query:
            d.events.append("barrier" if "SET b.cacheLock" in query else "fence-read")
            value = d.states.get(p["barrier"])
            return Result([value] if value else [])
        if "MERGE (q:RagGraphCacheQuota" in query:
            d.events.append("quota")
            return Result()
        if "c.expiresAt<=timestamp()" in query:
            d.events.append("prune")
            for key in [k for k, v in d.entries.items() if v["expiry"] <= d.now][:256]:
                del d.entries[key]
            return Result()
        if "RETURN count(c) AS count" in query:
            old = d.entries.get(p["key"])
            return Result(
                [
                    {
                        "count": len(d.entries),
                        "bytes": sum(v["bytes"] for v in d.entries.values()),
                        "existing": int(old is not None),
                        "oldBytes": old["bytes"] if old else 0,
                    }
                ]
            )
        if "MERGE (c:RagGraphExtractionCache" in query:
            d.events.append("entry")
            d.entries[p["key"]] = {**p, "expiry": d.now + p["ttl"]}
            return Result()
        if "c.expiresAt>timestamp()" in query:
            v = d.entries.get(p["key"])
            if (
                not v
                or v["tenant"] != p["tenant"]
                or v["kb"] != p["kb"]
                or v["document"] != p["document"]
                or v["expiry"] <= d.now
            ):
                return Result()
            return Result(
                [
                    {
                        "payload": v["payload"],
                        "checksum": v["checksum"],
                        "bytes": v["bytes"],
                        "remaining": v["expiry"] - d.now,
                    }
                ]
            )
        if "DELETE c" in query and "documentId:$document" in query:
            for key in [
                k
                for k, v in d.entries.items()
                if v["tenant"] == p["tenant"] and v["document"] == p["document"]
            ]:
                del d.entries[key]
            return Result()
        raise AssertionError("Unexpected cache transaction")


def source(**updates):
    values = {
        "pointId": "point-a",
        "documentId": "doc-a",
        "documentCode": "synthetic",
        "documentVersion": 1,
        "documentRevision": 7,
        "chunkId": "chunk-a",
        "contentSha256": content_fingerprint(TEXT),
    }
    return ActiveGraphSource(**{**values, **updates})


async def cache(
    settings, monkeypatch, driver=None, *, tenant="tenant-a", kb="kb-a", build="build-a", fp="fp-a"
):
    driver = driver or MemoryDriver()
    driver.context(tenant, kb, build)
    monkeypatch.setattr(graph_cache.AsyncGraphDatabase, "driver", lambda *_args, **_kwargs: driver)
    value = graph_cache.Neo4jValidatedTripletCache(
        settings, tenant=tenant, knowledge_base=kb, build_id=build, config_fingerprint=fp
    )
    await value.open()
    assert value.ready
    return value, driver


async def test_cache_preserves_complete_paths_and_rebuilds_only_current_source_metadata(
    settings, monkeypatch
):
    c, driver = await cache(settings, monkeypatch)
    assert await c.get(source(), TEXT) is None
    assert await c.put(source(), TEXT, TRIPLES)
    paths = await c.get(source(), TEXT)
    original = TextNode(id_="current-generation-node", text=TEXT, metadata={"current": "new-metadata"})
    restored = graph_cache.node_from_triplets(original, paths, 8)
    assert graph_cache.triplets_from_node(restored, 8) == TRIPLES
    assert restored.id_ == original.id_ and restored.metadata["current"] == "new-metadata"
    assert all(e.properties == {"current": "new-metadata"} for e in restored.metadata[KG_NODES_KEY])
    assert KG_NODES_KEY not in original.metadata and KG_RELATIONS_KEY not in original.metadata
    assert driver.events[:4] == ["fence-read", "barrier", "quota", "prune"]
    await c.close()
    assert driver.closed


@pytest.mark.parametrize(
    "updates", [{"pointId": "point-b"}, {"chunkId": "chunk-b"}, {"documentCode": "new-code"}]
)
async def test_every_same_revision_identity_change_misses(settings, monkeypatch, updates):
    c, _ = await cache(settings, monkeypatch)
    await c.put(source(), TEXT, TRIPLES)
    assert await c.get(source(**updates), TEXT) is None


async def test_raw_text_change_misses_even_with_equal_java_normalized_fingerprint(settings, monkeypatch):
    c, _ = await cache(settings, monkeypatch)
    await c.put(source(), TEXT, TRIPLES)
    changed = TEXT.replace("第1行", "第2行")
    assert content_fingerprint(changed) == source().contentSha256
    assert await c.get(source(), changed) is None


@pytest.mark.parametrize("scope", ["tenant", "kb", "config"])
async def test_scope_or_configuration_never_reuses_another_cache_entry(settings, monkeypatch, scope):
    c, d = await cache(settings, monkeypatch)
    await c.put(source(), TEXT, TRIPLES)
    other, _ = await cache(
        settings,
        monkeypatch,
        d,
        tenant="tenant-b" if scope == "tenant" else "tenant-a",
        kb="kb-b" if scope == "kb" else "kb-a",
        fp="fp-b" if scope == "config" else "fp-a",
    )
    assert await other.get(source(), TEXT) is None


async def test_new_job_can_reuse_old_valid_paths_but_old_epoch_cannot_write(settings, monkeypatch):
    c, d = await cache(settings, monkeypatch)
    await c.put(source(), TEXT, TRIPLES)
    d.context(build="build-b", epoch=3)
    monkeypatch.setattr(graph_cache.AsyncGraphDatabase, "driver", lambda *_args, **_kwargs: d)
    other = graph_cache.Neo4jValidatedTripletCache(
        settings, tenant="tenant-a", knowledge_base="kb-a", build_id="build-b", config_fingerprint="fp-a"
    )
    await other.open()
    assert await other.get(source(), TEXT) == TRIPLES
    with pytest.raises(EngineError, match="GRAPH_SOURCE_CHANGED"):
        await c.put(source(), TEXT, TRIPLES)


@pytest.mark.parametrize(
    "kind", ["json", "checksum", "scope", "identity", "raw", "extra", "ungrounded", "empty"]
)
async def test_corrupt_or_foreign_hits_are_evicted_and_never_accepted(settings, monkeypatch, kind):
    c, d = await cache(settings, monkeypatch)
    await c.put(source(), TEXT, TRIPLES)
    key, row = next(iter(d.entries.items()))
    value = json.loads(row["payload"])
    if kind == "json":
        row["payload"] = "{invalid"
    elif kind == "checksum":
        row["checksum"] = "0" * 64
    elif kind == "scope":
        value["tenant"] = "tenant-b"
    elif kind == "identity":
        value["source"]["documentVersion"] = 2
    elif kind == "raw":
        value["rawSha256"] = "0" * 64
    elif kind == "extra":
        value["generation"] = "old-generation"
    elif kind == "ungrounded":
        value["triples"][0][2] = "未在原文出现的对象"
    elif kind == "empty":
        value["triples"] = []
    if kind not in {"json", "checksum"}:
        row["payload"] = graph_cache._json(value)
        row["checksum"] = hashlib.sha256(row["payload"].encode()).hexdigest()
        row["bytes"] = len(row["payload"])
    assert await c.get(source(), TEXT) is None
    assert key not in d.entries


async def test_empty_invalid_and_oversize_paths_are_not_checkpointed(settings, monkeypatch):
    c, d = await cache(settings, monkeypatch)
    assert await c.put(source(), TEXT, []) is False
    assert await c.put(source(), TEXT, None) is False
    assert await c.put(source(), TEXT, [("方案甲", "用于", "未在原文出现")]) is False
    monkeypatch.setattr(graph_cache, "CACHE_MAX_ENTRY_BYTES", 32)
    assert await c.put(source(), TEXT, TRIPLES) is False
    assert not d.entries


async def test_ttl_and_global_entry_quota_are_bounded(settings, monkeypatch):
    c, d = await cache(settings, monkeypatch)
    monkeypatch.setattr(graph_cache, "CACHE_MAX_ENTRIES", 2)
    admitted = await asyncio.gather(*(c.put(source(pointId=f"point-{i}"), TEXT, TRIPLES) for i in range(4)))
    assert admitted.count(True) == 2 and len(d.entries) == 2
    d.now += graph_cache.CACHE_TTL_MILLIS + 1
    assert await c.get(source(pointId="point-0"), TEXT) is None
    assert await c.put(source(), TEXT, TRIPLES)
    assert len(d.entries) == 1


async def test_payload_byte_quota_declines_without_changing_valid_existing_entry(settings, monkeypatch):
    c, d = await cache(settings, monkeypatch)
    await c.put(source(), TEXT, TRIPLES)
    size = sum(r["bytes"] for r in d.entries.values())
    monkeypatch.setattr(graph_cache, "CACHE_MAX_BYTES", size)
    assert await c.put(source(pointId="point-b"), TEXT, TRIPLES) is False
    assert len(d.entries) == 1 and await c.get(source(), TEXT) == TRIPLES


async def test_deactivation_clears_cache_and_fences_late_old_put(settings, monkeypatch):
    c, d = await cache(settings, monkeypatch)
    await c.put(source(), TEXT, TRIPLES)
    d.states[digest("tenant-a", "doc-a")]["tombstoned"] = True
    async with d.session() as session:
        async with await session.begin_transaction() as tx:
            await graph_cache.delete_document_cache_tx(tx, "tenant-a", "doc-a")
            await tx.commit()
    with pytest.raises(EngineError, match="GRAPH_SOURCE_CHANGED"):
        await c.put(source(), TEXT, TRIPLES)
    assert not d.entries


async def test_cache_transport_failure_is_only_a_miss_or_declined_admission(settings, monkeypatch):
    c, d = await cache(settings, monkeypatch)
    d.unavailable = True
    assert await c.get(source(), TEXT) is None
    assert await c.put(source(), TEXT, TRIPLES) is False


async def test_close_and_reopen_reads_original_job_context_without_false_duplicate(settings, monkeypatch):
    c, _ = await cache(settings, monkeypatch)
    await c.put(source(), TEXT, TRIPLES)
    await c.close()
    assert not c.documents and not c.ready
    await c.open()
    assert c.ready and await c.get(source(), TEXT) == TRIPLES


async def test_cached_validation_engine_error_is_evicted_not_a_build_failure(settings, monkeypatch):
    c, d = await cache(settings, monkeypatch)
    await c.put(source(), TEXT, TRIPLES)

    def reject(*_args):
        raise EngineError("GRAPH_EXTRACTION_INVALID")

    monkeypatch.setattr(graph_cache, "node_from_triplets", reject)
    assert await c.get(source(), TEXT) is None
    assert not d.entries


async def test_changed_source_version_requires_new_job_context_and_cannot_hit_old_key(settings, monkeypatch):
    c, d = await cache(settings, monkeypatch)
    await c.put(source(), TEXT, TRIPLES)
    d.context(build="build-b", revision=8, epoch=3)
    document = json.loads(d.contexts[("tenant-a", "kb-a", "build-b")])[0]
    document["version"] = 2
    d.contexts[("tenant-a", "kb-a", "build-b")] = json.dumps([document])
    d.states[document["barrier"]]["version"] = 2
    other = graph_cache.Neo4jValidatedTripletCache(
        settings, tenant="tenant-a", knowledge_base="kb-a", build_id="build-b", config_fingerprint="fp-a"
    )
    await other.open()
    assert await other.get(source(documentRevision=8, documentVersion=2), TEXT) is None
    with pytest.raises(EngineError, match="GRAPH_SOURCE_CHANGED"):
        await c.get(source(), TEXT)


def test_delete_cache_import_does_not_load_heavy_extractor_dependencies():
    code = (
        "import sys; import rag_engine.graph_cache; "
        "assert not any(name.startswith('llama_index') for name in sys.modules); "
        "assert 'rag_engine.graph_extract' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_fingerprint_changes_with_model_provider_prompt_schema_and_dependency(settings, monkeypatch):
    baseline = graph_cache.cache_config_fingerprint(settings)
    assert (
        graph_cache.cache_config_fingerprint(settings.model_copy(update={"graph_model": "other"})) != baseline
    )
    assert (
        graph_cache.cache_config_fingerprint(
            settings.model_copy(update={"qwen_base_url": "https://new.test"})
        )
        != baseline
    )
    assert (
        graph_cache.cache_config_fingerprint(settings.model_copy(update={"graph_max_triplets_per_chunk": 7}))
        != baseline
    )
    monkeypatch.setattr(graph_extract, "EXTRACT_PROMPT", graph_extract.EXTRACT_PROMPT + " explicit version")
    changed = graph_cache.cache_config_fingerprint(settings)
    assert changed != baseline
    monkeypatch.setattr(graph_cache, "version", lambda _name: "different-official-version")
    assert graph_cache.cache_config_fingerprint(settings) != changed


def test_ambiguous_official_entity_ids_are_not_rewritten_into_cache_paths():
    raw = 'A"B适用对象甲；A B适用对象乙。'
    node = graph_cache.node_from_triplets(
        TextNode(text=raw), [('A"B', "适用", "对象甲"), ("A B", "适用", "对象乙")], 8
    )
    assert graph_cache.triplets_from_node(node, 8) is None
