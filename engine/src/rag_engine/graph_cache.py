"""仅复用已验证的完整原文三元组；缓存不拥有活动来源、发布或删除授权。"""

import hashlib
import json
from importlib.metadata import version
from pathlib import Path

from neo4j import AsyncGraphDatabase, Query

from rag_engine.errors import EngineError
from rag_engine.graph_models import ActiveGraphSource
from rag_engine.graph_sources import content_fingerprint, digest, document_key

CACHE_VERSION = 1
CACHE_TTL_MILLIS = 24 * 60 * 60 * 1000
CACHE_MAX_ENTRIES = 10000
CACHE_MAX_BYTES = 128 * 1024 * 1024
CACHE_MAX_ENTRY_BYTES = 64 * 1024
CACHE_QUERY_TIMEOUT = 3


def _json(value):
    # ASCII JSON让Neo4j size(payload)与实际逻辑字节一致，不依赖字符宽度。
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Invalid cache object")
        result[key] = value
    return result


def _load(value):
    def invalid_constant(_value):
        raise ValueError("Invalid cache constant")

    return json.loads(value, object_pairs_hook=_pairs, parse_constant=invalid_constant)


def cache_config_fingerprint(settings):
    """不含凭据；模型、来源提示、严格schema与执行/校验源码变化均使旧缓存失效。"""
    from rag_engine import graph_extract, graph_schema

    return digest(
        {
            "cacheVersion": CACHE_VERSION,
            "llamaIndexVersion": version("llama-index-core"),
            "model": settings.graph_model,
            "provider": settings.qwen_base_url,
            "maximum": settings.graph_max_triplets_per_chunk,
            "temperature": 0,
            "maxTokens": 2048,
            "thinking": False,
            "prompt": graph_extract.EXTRACT_PROMPT,
            "schema": graph_schema.response_format(settings.graph_max_triplets_per_chunk),
            "extractor": hashlib.sha256(Path(graph_extract.__file__).read_bytes()).hexdigest(),
            "schemaSource": hashlib.sha256(Path(graph_schema.__file__).read_bytes()).hexdigest(),
        }
    )


def node_from_triplets(node, triples, maximum):
    """从当前可信来源创建新节点；不接受缓存里的元数据、存储ID或generation。"""
    from llama_index.core.graph_stores.types import KG_NODES_KEY, KG_RELATIONS_KEY, EntityNode, Relation

    from rag_engine import graph_extract

    parsed = graph_extract.parse_triplets(json.dumps(triples, ensure_ascii=False, allow_nan=False))
    result = node.model_copy(deep=True)
    result.metadata.pop(KG_NODES_KEY, None)
    result.metadata.pop(KG_RELATIONS_KEY, None)
    metadata = result.metadata.copy()
    entities, relations = [], []
    for subject, predicate, object_ in parsed:
        left = EntityNode(name=subject, properties=metadata.copy())
        right = EntityNode(name=object_, properties=metadata.copy())
        entities.extend([left, right])
        relations.append(
            Relation(label=predicate, source_id=left.id, target_id=right.id, properties=metadata.copy())
        )
    result.metadata[KG_NODES_KEY] = entities
    result.metadata[KG_RELATIONS_KEY] = relations
    graph_extract.validate_extracted_paths(result, maximum)
    return result


def triplets_from_node(node, maximum):
    """仅无损提取完整候选；官方ID碰撞或无法配对时不准入缓存，不改写原结果。"""
    from llama_index.core.graph_stores.types import KG_NODES_KEY, KG_RELATIONS_KEY

    from rag_engine import graph_extract

    graph_extract.validate_extracted_paths(node, maximum)
    entities = node.metadata.get(KG_NODES_KEY, [])
    relations = node.metadata.get(KG_RELATIONS_KEY, [])
    if len(entities) != len(relations) * 2:
        return None
    names = {}
    for entity in entities:
        if entity.id in names and names[entity.id] != entity.name:
            return None
        names[entity.id] = entity.name
    used = {identity for relation in relations for identity in (relation.source_id, relation.target_id)}
    if used != set(names):
        return None
    return [(names[r.source_id], r.label, names[r.target_id]) for r in relations]


async def delete_document_cache_tx(tx, tenant, document_id):
    """调用方必须已持有该文档删除barrier；在同一事务删除所有KB/版本的派生缓存。"""
    result = await tx.run(
        "MATCH (c:RagGraphExtractionCache {tenant:$tenant,documentId:$document}) DELETE c",
        tenant=tenant,
        document=document_id,
    )
    await result.consume()


class Neo4jValidatedTripletCache:
    """同一async循环中开启/使用/关闭；IO不可用只失去复用，已证来源失效仍明确失败。"""

    def __init__(self, settings, *, tenant, knowledge_base, build_id, config_fingerprint):
        self.settings, self.tenant, self.kb = settings, tenant, knowledge_base
        self.build_id, self.fingerprint = build_id, config_fingerprint
        self.driver = None
        self.documents = {}
        self.ready = False

    async def _rows(self, query, **params):
        records, _, _ = await self.driver.execute_query(
            Query(query, timeout=CACHE_QUERY_TIMEOUT),
            database_=self.settings.neo4j_database,
            parameters_=params,
        )
        return [record.data() for record in records]

    async def open(self):
        if self.driver is not None:
            return
        self.documents = {}
        self.ready = False
        try:
            self.driver = AsyncGraphDatabase.driver(
                self.settings.neo4j_url,
                auth=(self.settings.neo4j_username, self.settings.neo4j_password.get_secret_value()),
                max_connection_pool_size=4,
                connection_timeout=3,
                connection_acquisition_timeout=3,
                max_transaction_retry_time=0,
            )
            rows = await self._rows(
                "MATCH (j:RagGraphJob {buildId:$build,tenant:$tenant,knowledgeBase:$kb,status:'RUNNING'}) "
                "WHERE j.deadline>timestamp() RETURN j.documentsJson AS documents",
                build=self.build_id,
                tenant=self.tenant,
                kb=self.kb,
            )
            if len(rows) != 1 or not isinstance(rows[0].get("documents"), str):
                return
            raw = rows[0]["documents"]
            if len(raw.encode()) > 4 * 1024 * 1024:
                return
            documents = _load(raw)
            if not isinstance(documents, list) or not 1 <= len(documents) <= 10000:
                return
            for document in documents:
                if (
                    not isinstance(document, dict)
                    or set(document)
                    != {"key", "barrier", "documentId", "revision", "version", "barrierEpoch"}
                    or not isinstance(document["documentId"], str)
                    or document["key"] != document_key(self.tenant, self.kb, document["documentId"])
                    or document["barrier"] != digest(self.tenant, document["documentId"])
                    or any(
                        type(document[k]) is not int or document[k] < minimum
                        for k, minimum in [("revision", 0), ("version", 1), ("barrierEpoch", 1)]
                    )
                    or document["documentId"] in self.documents
                ):
                    self.documents = {}
                    return
                self.documents[document["documentId"]] = document
            for label in ["RagGraphExtractionCache", "RagGraphCacheQuota"]:
                await self._rows(f"CREATE CONSTRAINT IF NOT EXISTS FOR (c:{label}) REQUIRE c.key IS UNIQUE")
            await self._rows(
                "CREATE INDEX rag_graph_cache_document IF NOT EXISTS FOR (c:RagGraphExtractionCache) "
                "ON (c.tenant,c.documentId)"
            )
            self.ready = True
        except Exception:
            self.ready = False

    async def close(self):
        driver, self.driver = self.driver, None
        self.ready = False
        self.documents = {}
        if driver is not None:
            try:
                await driver.close()
            except Exception:
                pass

    def _context(self, source, raw_text):
        if not isinstance(source, ActiveGraphSource) or not isinstance(raw_text, str):
            raise EngineError("GRAPH_SOURCE_CHANGED", 409)
        document = self.documents.get(source.documentId)
        if (
            document is None
            or source.documentRevision != document["revision"]
            or source.documentVersion != document["version"]
            or content_fingerprint(raw_text) != source.contentSha256
        ):
            raise EngineError("GRAPH_SOURCE_CHANGED", 409)
        raw_hash = hashlib.sha256(raw_text.encode()).hexdigest()
        key = digest(CACHE_VERSION, self.tenant, self.kb, source.model_dump(), raw_hash, self.fingerprint)
        return document, raw_hash, key

    async def _fence(self, tx, document, *, lock):
        locking = "SET b.cacheLock=coalesce(b.cacheLock,0)+1 " if lock else ""
        result = await tx.run(
            "MATCH (b:RagGraphBarrier {key:$barrier}) "
            + locking
            + "WITH b OPTIONAL MATCH (d:RagGraphDocument {key:$document}) "
            "OPTIONAL MATCH (j:RagGraphJob {buildId:$build,tenant:$tenant,knowledgeBase:$kb}) "
            "RETURN b.epoch AS epoch,b.tombstoned AS tombstoned,d.requestedBuild AS requestedBuild,"
            "d.revision AS revision,d.version AS version,j.status AS status,j.deadline AS deadline,"
            "timestamp() AS now",
            barrier=document["barrier"],
            document=document["key"],
            build=self.build_id,
            tenant=self.tenant,
            kb=self.kb,
        )
        row = await result.single()
        if row is None:
            return False
        if not (
            row["epoch"] == document["barrierEpoch"]
            and row["tombstoned"] is False
            and row["requestedBuild"] == self.build_id
            and row["revision"] == document["revision"]
            and row["version"] == document["version"]
            and row["status"] == "RUNNING"
            and type(row["deadline"]) is int
            and row["deadline"] > row["now"]
        ):
            raise EngineError("GRAPH_SOURCE_CHANGED", 409)
        return True

    def _envelope(self, source, raw_hash, key, triples):
        return {
            "version": CACHE_VERSION,
            "key": key,
            "tenant": self.tenant,
            "knowledgeBase": self.kb,
            "source": source.model_dump(),
            "rawSha256": raw_hash,
            "configFingerprint": self.fingerprint,
            "triples": triples,
        }

    async def get(self, source, raw_text):
        if not self.ready:
            return None
        document, raw_hash, key = self._context(source, raw_text)
        try:
            async with self.driver.session(database=self.settings.neo4j_database) as session:
                async with await session.begin_transaction(timeout=CACHE_QUERY_TIMEOUT) as tx:
                    if not await self._fence(tx, document, lock=False):
                        return None
                    result = await tx.run(
                        "MATCH (c:RagGraphExtractionCache {key:$key,tenant:$tenant,knowledgeBase:$kb,"
                        "documentId:$document}) WHERE c.expiresAt>timestamp() "
                        "RETURN CASE WHEN c.payload IS :: STRING NOT NULL THEN CASE WHEN "
                        "size(c.payload)<=$maximum THEN c.payload ELSE null END ELSE null END AS payload,"
                        "CASE WHEN c.payloadSha256 IS :: STRING NOT NULL THEN CASE WHEN "
                        "size(c.payloadSha256)=64 THEN c.payloadSha256 ELSE null END "
                        "ELSE null END AS checksum,"
                        "CASE WHEN c.bytes IS :: INTEGER NOT NULL THEN c.bytes ELSE null END AS bytes,"
                        "c.expiresAt-timestamp() AS remaining",
                        key=key,
                        tenant=self.tenant,
                        kb=self.kb,
                        document=source.documentId,
                        maximum=CACHE_MAX_ENTRY_BYTES,
                    )
                    row = await result.single()
            if row is None:
                return None
            payload = row["payload"]
            if (
                not isinstance(payload, str)
                or not payload.isascii()
                or len(payload) > CACHE_MAX_ENTRY_BYTES
                or type(row["bytes"]) is not int
                or row["bytes"] != len(payload)
                or type(row["remaining"]) is not int
                or not 0 < row["remaining"] <= CACHE_TTL_MILLIS
                or row["checksum"] != hashlib.sha256(payload.encode()).hexdigest()
            ):
                raise ValueError("Invalid cache record")
            value = _load(payload)
            if not isinstance(value, dict) or set(value) != set(self._envelope(source, raw_hash, key, [])):
                raise ValueError("Invalid cache envelope")
            triples = value["triples"]
            if not isinstance(triples, list) or not triples:
                raise ValueError("Invalid cache paths")
            if _json(value) != _json(self._envelope(source, raw_hash, key, triples)):
                raise ValueError("Foreign cache identity")
            from llama_index.core.schema import TextNode

            checked = node_from_triplets(
                TextNode(text=raw_text), triples, self.settings.graph_max_triplets_per_chunk
            )
            return triplets_from_node(checked, self.settings.graph_max_triplets_per_chunk)
        except EngineError as error:
            if error.code == "GRAPH_SOURCE_CHANGED":
                raise
            await self.evict(key)
            return None
        except Exception:
            await self.evict(key)
            return None

    async def evict(self, key):
        if not self.ready:
            return
        try:
            await self._rows(
                "MATCH (c:RagGraphExtractionCache {key:$key,tenant:$tenant,knowledgeBase:$kb}) DELETE c",
                key=key,
                tenant=self.tenant,
                kb=self.kb,
            )
        except Exception:
            pass

    async def put(self, source, raw_text, triples):
        if not self.ready or not triples:
            return False
        document, raw_hash, key = self._context(source, raw_text)
        try:
            from llama_index.core.schema import TextNode

            node_from_triplets(TextNode(text=raw_text), triples, self.settings.graph_max_triplets_per_chunk)
            payload = _json(self._envelope(source, raw_hash, key, triples))
            if len(payload) > CACHE_MAX_ENTRY_BYTES:
                return False
        except Exception:
            return False
        try:
            async with self.driver.session(database=self.settings.neo4j_database) as session:
                async with await session.begin_transaction(timeout=CACHE_QUERY_TIMEOUT) as tx:
                    # 顺序为当前barrier→全局quota→cache行；删除事务只走barrier→cache行。
                    if not await self._fence(tx, document, lock=True):
                        return False
                    await (
                        await tx.run(
                            "MERGE (q:RagGraphCacheQuota {key:'validated-triplets'}) "
                            "SET q.lock=coalesce(q.lock,0)+1 RETURN q.key AS key"
                        )
                    ).consume()
                    await (
                        await tx.run(
                            "MATCH (c:RagGraphExtractionCache) WHERE c.expiresAt<=timestamp() "
                            "WITH c LIMIT 256 DELETE c"
                        )
                    ).consume()
                    usage = await (
                        await tx.run(
                            "MATCH (c:RagGraphExtractionCache) RETURN count(c) AS count,"
                            "coalesce(sum(CASE WHEN c.bytes>=0 AND c.bytes<=$maximum THEN c.bytes "
                            "ELSE $maximum END),0) AS bytes,"
                            "coalesce(sum(CASE WHEN c.key=$key THEN 1 ELSE 0 END),0) AS existing,"
                            "coalesce(sum(CASE WHEN c.key=$key THEN CASE WHEN "
                            "c.bytes>=0 AND c.bytes<=$maximum "
                            "THEN c.bytes ELSE $maximum END ELSE 0 END),0) AS oldBytes",
                            key=key,
                            maximum=CACHE_MAX_ENTRY_BYTES,
                        )
                    ).single()
                    if (
                        usage["count"] - usage["existing"] + 1 > CACHE_MAX_ENTRIES
                        or usage["bytes"] - usage["oldBytes"] + len(payload) > CACHE_MAX_BYTES
                    ):
                        return False
                    await (
                        await tx.run(
                            "MERGE (c:RagGraphExtractionCache {key:$key}) "
                            "SET c.tenant=$tenant,c.knowledgeBase=$kb,c.documentId=$document,"
                            "c.payload=$payload,"
                            "c.payloadSha256=$checksum,c.bytes=$bytes,c.expiresAt=timestamp()+$ttl",
                            key=key,
                            tenant=self.tenant,
                            kb=self.kb,
                            document=source.documentId,
                            payload=payload,
                            checksum=hashlib.sha256(payload.encode()).hexdigest(),
                            bytes=len(payload),
                            ttl=CACHE_TTL_MILLIS,
                        )
                    ).consume()
                    await tx.commit()
                    return True
        except EngineError:
            raise
        except Exception:
            return False
