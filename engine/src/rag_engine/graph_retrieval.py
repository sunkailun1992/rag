"""以已验证向量候选为种子扩展已发布图，重新回读原文后再参与 RRF。"""

import asyncio
import json
from dataclasses import dataclass

import httpx

from rag_engine.config import Settings
from rag_engine.graph_models import ActiveGraphSource
from rag_engine.graph_sources import content_fingerprint, document_key, source_key
from rag_engine.http import post_json
from rag_engine.lexical import SPARSE_MODEL
from rag_engine.models import Hit, RetrievalRequest

# 每一端都受自己的文档活动 generation 及来源身份约束。同名实体仅在本租户/KB 内连接。
# 不执行模型 Cypher，不从无命中的请求开始全图遍历。
NEIGHBOR_QUERY = """
UNWIND $seeds AS a
MATCH (d:RagGraphDocument {key:a.documentKey,tenant:$tenant,knowledgeBase:$kb})
WHERE d.activeGeneration IS NOT NULL
MATCH (c:Chunk {tenant_id:$tenant,knowledge_base:$kb,generation:d.activeGeneration,
                point_id:a.pointId,source_key:a.sourceKey,document_id:a.documentId})
      -[:MENTIONS]->(s:__Entity__)
WHERE s.tenant_id=$tenant AND s.knowledge_base=$kb AND s.generation=d.activeGeneration
  AND s.point_id=a.pointId AND s.source_key=a.sourceKey AND s.document_id=a.documentId
WITH DISTINCT s.canonical_name AS canonical ORDER BY canonical LIMIT 40
MATCH (t:__Entity__ {tenant_id:$tenant,knowledge_base:$kb,canonical_name:canonical})
      <-[:MENTIONS]-(candidate:Chunk {tenant_id:$tenant,knowledge_base:$kb})
MATCH (target:RagGraphDocument {tenant:$tenant,knowledgeBase:$kb,
                               documentId:candidate.document_id,activeGeneration:candidate.generation})
WHERE t.generation=target.activeGeneration AND candidate.generation=target.activeGeneration
  AND t.point_id=candidate.point_id AND t.source_key=candidate.source_key
  AND t.document_id=candidate.document_id AND NOT candidate.point_id IN $excluded
RETURN DISTINCT candidate.point_id AS pointId,candidate.source_key AS sourceKey,
       candidate.source_json AS sourceJson,candidate.tenant_id AS tenant,
       candidate.knowledge_base AS knowledgeBase,candidate.generation AS generation
ORDER BY pointId LIMIT $limit
"""

PUBLICATION_QUERY = """
UNWIND $sources AS a
MATCH (d:RagGraphDocument {key:a.documentKey,tenant:$tenant,knowledgeBase:$kb,
                          documentId:a.documentId,activeGeneration:a.generation})
MATCH (c:Chunk {tenant_id:$tenant,knowledge_base:$kb,generation:d.activeGeneration,
                document_id:a.documentId,point_id:a.pointId,source_key:a.sourceKey})
RETURN c.point_id AS pointId,c.source_key AS sourceKey,c.generation AS generation
LIMIT 20
"""


@dataclass
class GraphAugmentation:
    hits: list[Hit]
    augmented: bool = False
    degraded: bool = False


def _identity(point_id: str, payload: dict) -> ActiveGraphSource:
    return ActiveGraphSource(
        pointId=point_id,
        documentId=payload["document_id"],
        documentCode=payload["document_code"],
        documentVersion=payload["document_version"],
        documentRevision=payload["document_revision"],
        chunkId=payload["chunk_id"],
        contentSha256=payload["content_sha256"],
    )


def _scoped_payload(payload: dict, tenant: str, base: str, settings: Settings) -> bool:
    return (
        payload.get("tenant_id") == tenant
        and payload.get("knowledge_base") == base
        and payload.get("document_status") == "ACTIVE"
        and payload.get("embedding_model") == settings.embedding_model
        and type(payload.get("embedding_dimension")) is int
        and payload["embedding_dimension"] == settings.embedding_dimension
        and (not settings.hybrid_enabled or payload.get("sparse_model") == SPARSE_MODEL)
    )


def _fuse(original: list[Hit], extra: list[Hit], limit: int) -> list[Hit]:
    scores = {}
    values = {}
    for ranking in (original, extra):
        for rank, hit in enumerate(ranking, 1):
            scores[hit.id] = scores.get(hit.id, 0) + 1 / (60 + rank)
            values[hit.id] = hit
    seen_content = set()
    result = []
    for point_id in sorted(scores, key=lambda value: (-scores[value], value)):
        hit = values[point_id]
        content_id = hit.payload.get("content_sha256") or point_id
        if content_id not in seen_content:
            result.append(Hit(id=point_id, score=scores[point_id], payload=hit.payload))
            seen_content.add(content_id)
            if len(result) == limit:
                break
    return result


async def augment_candidates(
    hits: list[Hit],
    request: RetrievalRequest,
    tenant: str,
    settings: Settings,
    client: httpx.AsyncClient,
    repository=None,
) -> GraphAugmentation:
    """图辅助失败只回退已验证候选；调用方取消必须继续传播给三秒总预算。"""
    if not hits or not settings.graph_retrieval_enabled:
        return GraphAugmentation(hits)
    if repository is None:
        return GraphAugmentation(hits, degraded=True)
    try:
        # Neo4j 和 Qdrant 回读共享这一个局部预算，不能各自累加至超过请求总时限。
        async with asyncio.timeout(0.4):
            seeds = []
            for hit in hits[:100]:
                if not _scoped_payload(hit.payload, tenant, request.knowledgeBase, settings):
                    raise ValueError("invalid graph seed")
                identity = _identity(hit.id, hit.payload)
                seeds.append(
                    {
                        "documentKey": document_key(tenant, request.knowledgeBase, identity.documentId),
                        "documentId": identity.documentId,
                        "pointId": identity.pointId,
                        "sourceKey": source_key(identity),
                    }
                )
            rows = await repository.rows(
                NEIGHBOR_QUERY,
                tenant=tenant,
                kb=request.knowledgeBase,
                seeds=seeds,
                excluded=[hit.id for hit in hits],
                limit=20,
            )
            if not isinstance(rows, list) or len(rows) > 20:
                raise ValueError("graph candidate limit")
            identities = {}
            generations = {}
            degraded = False
            original_ids = {hit.id for hit in hits}
            for row in rows:
                try:
                    if (
                        row["tenant"] != tenant
                        or row["knowledgeBase"] != request.knowledgeBase
                        or not isinstance(row["generation"], str)
                        or not 1 <= len(row["generation"]) <= 128
                        or not isinstance(row["sourceJson"], str)
                        or len(row["sourceJson"]) > 16384
                    ):
                        raise ValueError("graph source scope")
                    raw_source = json.loads(row["sourceJson"])
                    identity = ActiveGraphSource.model_validate(
                        {field: raw_source[field] for field in ActiveGraphSource.model_fields}
                    )
                    if row["pointId"] != identity.pointId or row["sourceKey"] != source_key(identity):
                        raise ValueError("graph source identity")
                    if identity.pointId in original_ids:
                        continue
                    previous = identities.get(identity.pointId)
                    if previous is not None and previous != identity:
                        raise ValueError("inconsistent graph sources")
                    identities[identity.pointId] = identity
                    generations[identity.pointId] = row["generation"]
                except (KeyError, TypeError, ValueError):
                    degraded = True
            if not identities:
                return GraphAugmentation(hits, degraded=degraded)
            response = await post_json(
                client,
                f"{settings.qdrant_url}/collections/{settings.collection}/points",
                {"ids": list(identities), "with_payload": True, "with_vector": False},
                {"api-key": settings.qdrant_api_key.get_secret_value()}
                if settings.qdrant_api_key.get_secret_value()
                else {},
                min(settings.max_upstream_bytes, 2 * 1024 * 1024),
            )
            raw_points = response["result"]
            if not isinstance(raw_points, list) or len(raw_points) > len(identities):
                raise ValueError("graph reread limit")
            points = {}
            duplicates = set()
            for point in raw_points:
                point_id = str(point["id"])
                if point_id not in identities or point_id in points:
                    degraded = True
                    duplicates.add(point_id)
                points[point_id] = point.get("payload")
            extra = []
            for point_id, expected in identities.items():
                try:
                    payload = points.get(point_id)
                    if (
                        point_id in duplicates
                        or not isinstance(payload, dict)
                        or not _scoped_payload(payload, tenant, request.knowledgeBase, settings)
                        or _identity(point_id, payload) != expected
                        or not isinstance(payload.get("content"), str)
                        or not 1 <= len(payload["content"]) <= 6000
                        or content_fingerprint(payload["content"]) != expected.contentSha256
                    ):
                        raise ValueError("stale graph evidence")
                    extra.append(Hit(id=point_id, score=0.0, payload=payload))
                except (KeyError, TypeError, ValueError):
                    degraded = True
            if not extra:
                return GraphAugmentation(hits, degraded=degraded)
            # 删除先退役图、后修改向量。回读期间若屏障已生效，即使向量仍 ACTIVE 也不扩展。
            confirmed = await repository.rows(
                PUBLICATION_QUERY,
                tenant=tenant,
                kb=request.knowledgeBase,
                sources=[
                    {
                        "documentKey": document_key(
                            tenant, request.knowledgeBase, identities[hit.id].documentId
                        ),
                        "documentId": identities[hit.id].documentId,
                        "pointId": hit.id,
                        "sourceKey": source_key(identities[hit.id]),
                        "generation": generations[hit.id],
                    }
                    for hit in extra
                ],
            )
            if not isinstance(confirmed, list) or len(confirmed) > 20:
                raise ValueError("graph publication response")
            active = {
                value["pointId"]
                for value in confirmed
                if value["pointId"] in identities
                and value["sourceKey"] == source_key(identities[value["pointId"]])
                and value["generation"] == generations[value["pointId"]]
            }
            if any(hit.id not in active for hit in extra):
                degraded = True
                extra = [hit for hit in extra if hit.id in active]
            if not extra:
                return GraphAugmentation(hits, degraded=degraded)
            fused = _fuse(hits, extra, request.candidateCount)
            return GraphAugmentation(
                fused, augmented=any(hit.id not in original_ids for hit in fused), degraded=degraded
            )
    except Exception:
        # 仅回退已验证的原召回，不记录图服务异常正文或把模型事实拼成上下文。
        # CancelledError 是 BaseException，调用方取消不会被这条可选降级吞掉。
        return GraphAugmentation(hits, degraded=True)
