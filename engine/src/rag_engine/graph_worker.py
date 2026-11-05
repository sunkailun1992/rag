"""一次性图构建进程；输入经私有管道，输出仅计数或白名单错误码。"""

import asyncio
import json
import logging
import sys

import httpx

from rag_engine.config import Settings
from rag_engine.errors import EngineError
from rag_engine.graph_models import GraphBuildRequest
from rag_engine.graph_sources import digest, read_active_sources, source_key


def source_text_node(source, tenant, kb, generation):
    from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode

    chunk_id = "c-" + digest(tenant, kb, generation, source.identity.pointId)
    return TextNode(
        id_=chunk_id,
        text=source.content,
        relationships={NodeRelationship.SOURCE: RelatedNodeInfo(node_id=source.identity.documentId)},
    )


def build(payload: dict) -> dict:
    from llama_index.core import PropertyGraphIndex
    from llama_index.core.indices.property_graph import SimpleLLMPathExtractor

    from rag_engine.graph_extract import (
        EXTRACT_PROMPT,
        QwenGraphLLM,
        ScopedGraphTransform,
        graph_store,
        parse_triplets,
    )

    settings = Settings(**payload["settings"])
    request = GraphBuildRequest.model_validate(payload["request"])
    tenant, generation = payload["tenant"], payload["buildId"]

    async def read_sources():
        async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
            return await read_active_sources(
                request.activeSources, tenant, request.knowledgeBase, settings, client
            )

    verified = asyncio.run(read_sources())
    nodes, sources = [], {}
    for source in verified:
        node = source_text_node(source, tenant, request.knowledgeBase, generation)
        nodes.append(node)
        sources[node.id_] = source.provenance
    transform = ScopedGraphTransform(
        tenant=tenant,
        knowledge_base=request.knowledgeBase,
        generation=generation,
        sources=sources,
        max_triplets=settings.graph_max_triplets_per_chunk,
        extraction_model=settings.graph_model,
    )
    client = httpx.AsyncClient(
        timeout=settings.graph_llm_timeout_seconds,
        trust_env=False,
        follow_redirects=False,
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
    )
    store = graph_store(settings)
    try:
        llm = QwenGraphLLM(settings=settings, client=client)
        extractor = SimpleLLMPathExtractor(
            llm=llm,
            extract_prompt=EXTRACT_PROMPT,
            parse_fn=parse_triplets,
            max_paths_per_chunk=settings.graph_max_triplets_per_chunk,
            num_workers=1,
            raise_on_error=True,
        )
        PropertyGraphIndex(
            nodes=nodes,
            llm=llm,
            kg_extractors=[extractor, transform],
            property_graph_store=store,
            embed_kg_nodes=False,
            use_async=True,
        )
        # 不以索引构造返回代替持久化读回；空关系文档仍必须存在完整来源 chunk。
        rows = store.structured_query(
            "MATCH (c:Chunk {tenant_id:$tenant,knowledge_base:$kb,generation:$generation}) "
            "RETURN c.point_id AS point,c.source_key AS sourceKey,c.document_id AS documentId,"
            "c.document_version AS version,c.document_revision AS revision",
            {"tenant": tenant, "kb": request.knowledgeBase, "generation": generation},
        )
        actual = {
            row["point"]: (row["sourceKey"], row["documentId"], row["version"], row["revision"])
            for row in rows
        }
        expected = {
            source.pointId: (
                source_key(source),
                source.documentId,
                source.documentVersion,
                source.documentRevision,
            )
            for source in request.activeSources
        }
        if actual != expected or len(rows) != len(expected):
            raise EngineError("GRAPH_READBACK_FAILED")
        counts = store.structured_query(
            "MATCH (e:__Entity__ {tenant_id:$tenant,knowledge_base:$kb,generation:$generation}) "
            "RETURN count(e) AS count",
            {"tenant": tenant, "kb": request.knowledgeBase, "generation": generation},
        )
        edges = store.structured_query(
            "MATCH (s:__Entity__ {tenant_id:$tenant,knowledge_base:$kb,generation:$generation})-[r]->() "
            "WHERE r.tenant_id=$tenant AND r.knowledge_base=$kb AND r.generation=$generation "
            "AND r.predicate IS NOT NULL RETURN count(r) AS count",
            {"tenant": tenant, "kb": request.knowledgeBase, "generation": generation},
        )
        if counts[0]["count"] != transform.node_count or edges[0]["count"] != transform.edge_count:
            raise EngineError("GRAPH_READBACK_FAILED")
        asyncio.run(read_sources())
        return {
            "processedDocumentCount": len({value.documentId for value in request.activeSources}),
            "nodeCount": counts[0]["count"],
            "edgeCount": edges[0]["count"],
        }
    finally:
        store.close()
        asyncio.run(client.aclose())


def main():
    # 由父容器限制 RSS；不用 RLIMIT_AS 误杀 NumPy/BLAS 的虚拟地址映射。
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (180, 181))
    logging.disable(logging.CRITICAL)
    try:
        raw = sys.stdin.buffer.read(9 * 1024 * 1024 + 1)
        if len(raw) > 9 * 1024 * 1024:
            raise EngineError("GRAPH_REQUEST_TOO_LARGE", 413)
        result = build(json.loads(raw))
        print(json.dumps({"result": result}))
    except BaseException as error:
        allowed = {
            "GRAPH_EXTRACTION_INVALID",
            "GRAPH_EXTRACTION_UNGROUNDED",
            "GRAPH_EXTRACTION_LIMIT",
            "GRAPH_SOURCE_CHANGED",
            "GRAPH_CONTENT_LIMIT",
            "GRAPH_READBACK_FAILED",
            "GRAPH_NOT_CONFIGURED",
            "GRAPH_REQUEST_TOO_LARGE",
        }
        code = str(error) if str(error) in allowed else "GRAPH_BUILD_FAILED"
        print(json.dumps({"error": code}))


if __name__ == "__main__":
    main()
