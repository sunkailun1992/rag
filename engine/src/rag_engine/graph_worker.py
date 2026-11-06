"""一次性图构建进程；输入经私有管道，输出仅计数或白名单错误码。"""

import asyncio
import contextvars
import json
import logging
import re
import sys

import httpx

from rag_engine.config import Settings
from rag_engine.errors import (
    GRAPH_MODEL_CODES,
    GRAPH_MODEL_REASONS,
    VALIDATION_REASONS,
    EngineError,
    GraphModelError,
)
from rag_engine.graph_models import GraphBuildRequest
from rag_engine.graph_sources import digest, read_active_sources, source_key

EXTRACTION_ERRORS = frozenset(
    {"GRAPH_EXTRACTION_INVALID", "GRAPH_EXTRACTION_UNGROUNDED", "GRAPH_EXTRACTION_LIMIT"}
)
WORKER_ERRORS = EXTRACTION_ERRORS | {
    "GRAPH_SOURCE_CHANGED",
    "GRAPH_CONTENT_LIMIT",
    "GRAPH_READBACK_FAILED",
    "GRAPH_NOT_CONFIGURED",
    "GRAPH_REQUEST_TOO_LARGE",
}


WORKER_STAGES = frozenset(
    {
        "INPUT",
        "CONFIGURE",
        "SOURCE_READ",
        "PREPARE_NODES",
        "STORE_OPEN",
        "INDEX_BUILD",
        "READBACK",
        "SOURCE_RECHECK",
        "STORE_CLOSE",
        "CLIENT_CLOSE",
        "ENCODE_INPUT",
        "SPAWN",
        "COMMUNICATE",
        "DECODE_OUTPUT",
        "VALIDATE_RESULT",
        "PROCESS_CLEANUP",
        "WORKER_RUN",
        "PUBLISH",
    }
)
_FRAME_FUNCTIONS = {
    "graph_worker.py": frozenset({"main", "build", "read_sources", "_aextract", "acall"}),
    "graph_jobs.py": frozenset({"run_worker", "pump"}),
    "graph_repository.py": frozenset({"rows", "publish", "transaction"}),
    "graph_extract.py": frozenset(
        {
            "acall",
            "_aextract",
            "acomplete",
            "__call__",
            "graph_store",
            "upsert_nodes",
            "upsert_relations",
            "get_schema",
            "parse_triplets",
            "validate_extracted_paths",
        }
    ),
    "graph_sources.py": frozenset({"read_active_sources", "content_fingerprint"}),
    "http.py": frozenset({"post_json", "request_json"}),
}
_FRAME_MODULES = frozenset({"rag_engine." + name.removesuffix(".py") for name in _FRAME_FUNCTIONS})
_EXCEPTION_BUCKETS = frozenset(
    {
        "BUILTIN_RUNTIME",
        "BUILTIN_VALUE",
        "BUILTIN_TYPE",
        "BUILTIN_KEY",
        "BUILTIN_MEMORY",
        "BUILTIN_TIMEOUT",
        "BUILTIN_OS",
        "NEO4J_CLIENT",
        "NEO4J_DATABASE",
        "NEO4J_TRANSIENT",
        "NEO4J_UNAVAILABLE",
        "NEO4J_SESSION",
        "NEO4J_RESULT",
        "HTTP_TIMEOUT",
        "HTTP_NETWORK",
        "HTTP_PROTOCOL",
        "ENGINE",
        "OTHER",
    }
)
_WORKER_STAGE = "INPUT"


def _set_worker_stage(stage):
    global _WORKER_STAGE
    if stage in WORKER_STAGES:
        _WORKER_STAGE = stage


def safe_worker_diagnostic(error, stage):
    """仅固定阶段、真实异常类型桶与受控代码栈；不读异常文本、参数或局部变量。"""
    from neo4j import exceptions as neo4j_errors

    buckets = {
        json.JSONDecodeError: "BUILTIN_VALUE",
        RuntimeError: "BUILTIN_RUNTIME",
        ValueError: "BUILTIN_VALUE",
        TypeError: "BUILTIN_TYPE",
        KeyError: "BUILTIN_KEY",
        MemoryError: "BUILTIN_MEMORY",
        TimeoutError: "BUILTIN_TIMEOUT",
        OSError: "BUILTIN_OS",
        EngineError: "ENGINE",
        neo4j_errors.ClientError: "NEO4J_CLIENT",
        neo4j_errors.DatabaseError: "NEO4J_DATABASE",
        neo4j_errors.TransientError: "NEO4J_TRANSIENT",
        neo4j_errors.ServiceUnavailable: "NEO4J_UNAVAILABLE",
        neo4j_errors.SessionExpired: "NEO4J_SESSION",
        neo4j_errors.ResultError: "NEO4J_RESULT",
        httpx.TimeoutException: "HTTP_TIMEOUT",
        httpx.ConnectTimeout: "HTTP_TIMEOUT",
        httpx.ReadTimeout: "HTTP_TIMEOUT",
        httpx.WriteTimeout: "HTTP_TIMEOUT",
        httpx.PoolTimeout: "HTTP_TIMEOUT",
        httpx.NetworkError: "HTTP_NETWORK",
        httpx.ConnectError: "HTTP_NETWORK",
        httpx.ReadError: "HTTP_NETWORK",
        httpx.WriteError: "HTTP_NETWORK",
        httpx.CloseError: "HTTP_NETWORK",
        httpx.ProtocolError: "HTTP_PROTOCOL",
        httpx.RemoteProtocolError: "HTTP_PROTOCOL",
        httpx.LocalProtocolError: "HTTP_PROTOCOL",
    }
    frames = []
    trace = BaseException.__traceback__.__get__(error)
    for _ in range(128):
        if trace is None:
            break
        code = trace.tb_frame.f_code
        module = trace.tb_frame.f_globals.get("__name__")
        filename = code.co_filename.rsplit("/", 1)[-1]
        function = code.co_name
        if (
            type(module) is str
            and (
                (module in _FRAME_MODULES and filename == module.rsplit(".", 1)[-1] + ".py")
                or (module == "__main__" and filename == "graph_worker.py")
            )
            and function in _FRAME_FUNCTIONS.get(filename, ())
            and type(trace.tb_lineno) is int
            and 0 < trace.tb_lineno <= 1_000_000
        ):
            frames.append({"file": filename, "function": function, "line": trace.tb_lineno})
        trace = trace.tb_next
    bucket = buckets.get(type(error), "OTHER")
    if bucket == "OTHER":
        for family, family_bucket in (
            (neo4j_errors.ClientError, "NEO4J_CLIENT"),
            (neo4j_errors.DatabaseError, "NEO4J_DATABASE"),
            (neo4j_errors.TransientError, "NEO4J_TRANSIENT"),
            (neo4j_errors.ServiceUnavailable, "NEO4J_UNAVAILABLE"),
            (neo4j_errors.SessionExpired, "NEO4J_SESSION"),
            (neo4j_errors.ResultError, "NEO4J_RESULT"),
        ):
            if isinstance(error, family):
                bucket = family_bucket
                break
    return {
        "stage": stage if type(stage) is str and stage in WORKER_STAGES else "INPUT",
        "exception": bucket,
        "frames": frames[-8:],
    }


def sanitize_worker_diagnostic(value):
    """子进程同样不受日志信任；只接收固定枚举及有限代码位置。"""
    if not isinstance(value, dict):
        return None
    stage, bucket, frames = value.get("stage"), value.get("exception"), value.get("frames")
    if (
        type(stage) is not str
        or stage not in WORKER_STAGES
        or type(bucket) is not str
        or bucket not in _EXCEPTION_BUCKETS
        or not isinstance(frames, list)
        or len(frames) > 8
    ):
        return None
    clean = []
    for frame in frames:
        if not isinstance(frame, dict):
            return None
        filename, function, line = frame.get("file"), frame.get("function"), frame.get("line")
        if (
            type(filename) is not str
            or filename not in _FRAME_FUNCTIONS
            or type(function) is not str
            or function not in _FRAME_FUNCTIONS[filename]
            or type(line) is not int
            or not 0 < line <= 1_000_000
        ):
            return None
        clean.append({"file": filename, "function": function, "line": line})
    return {"stage": stage, "exception": bucket, "frames": clean}


def with_source_diagnostics(extractor_type):
    """只把应用生成的来源node哈希附到失败对象，不读取正文或模型字段。"""

    class SourceDiagnosticExtractor(extractor_type):
        async def _aextract(self, node):
            node_id = getattr(node, "id_", None)
            try:
                return await super()._aextract(node)
            except EngineError as error:
                # 清除上游已有属性，指纹只能来自调用前的可信应用node身份。
                if hasattr(error, "source_fingerprint"):
                    del error.source_fingerprint
                code = getattr(error, "code", None)
                trusted_model = (
                    isinstance(error, GraphModelError) and code in GRAPH_MODEL_CODES
                    if type(code) is str
                    else False
                )
                trusted_extraction = (
                    not isinstance(error, GraphModelError) and type(code) is str and code in EXTRACTION_ERRORS
                )
                if (trusted_model or trusted_extraction) and type(node_id) is str:
                    match = re.fullmatch(r"c-([0-9a-f]{64})", node_id)
                    if match:
                        error.source_fingerprint = match.group(1)
                raise

    return SourceDiagnosticExtractor


class _UnavailableValidatedCache:
    """检查点不可用时仍执行完整抽取和发布校验，不把缓存当作成功凭据。"""

    async def open(self):
        pass

    async def close(self):
        pass


def with_validated_cache(extractor_type, cache_factory, sources):
    """在同一异步索引循环中复用已验证路径；节点与来源身份始终由本次构建生成。"""
    cache_context = contextvars.ContextVar("validated_graph_cache", default=None)

    class CachedValidatedExtractor(extractor_type):
        async def acall(self, nodes, show_progress=False, **kwargs):
            cache = cache_factory()
            token = cache_context.set(cache)
            try:
                await cache.open()
                return await super().acall(nodes, show_progress=show_progress, **kwargs)
            finally:
                primary = sys.exception()
                try:
                    await cache.close()
                except BaseException:
                    if primary is None:
                        raise
                finally:
                    cache_context.reset(token)

        async def _aextract(self, node):
            from llama_index.core.schema import MetadataMode

            cache = cache_context.get()
            if cache is None or isinstance(cache, _UnavailableValidatedCache):
                return await super()._aextract(node)
            from rag_engine.graph_cache import node_from_triplets, triplets_from_node

            source = sources.get(node.id_)
            if source is None or node.get_content(metadata_mode=MetadataMode.NONE) != source.content:
                raise EngineError("GRAPH_SOURCE_CHANGED", 409)
            saved = await cache.get(source.identity, source.content)
            if saved is not None:
                return node_from_triplets(node, saved, self.max_paths_per_chunk)
            result = await super()._aextract(node)
            if (
                result.id_ != node.id_
                or result.get_content(metadata_mode=MetadataMode.NONE) != source.content
            ):
                raise EngineError("GRAPH_SOURCE_CHANGED", 409)
            triples = triplets_from_node(result, self.max_paths_per_chunk)
            # 缓存拒收/达到配额不改变已验证的图；删除或换版的fence则由cache明确抛错。
            if triples is not None:
                await cache.put(source.identity, source.content, triples)
            return result

    return CachedValidatedExtractor


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

    from rag_engine.graph_extract import (
        EXTRACT_PROMPT,
        QwenGraphLLM,
        ScopedGraphTransform,
        ValidatedSimpleLLMPathExtractor,
        graph_store,
        parse_triplets,
    )

    _set_worker_stage("CONFIGURE")
    settings = Settings(**payload["settings"])
    request = GraphBuildRequest.model_validate(payload["request"])
    tenant, generation = payload["tenant"], payload["buildId"]

    async def read_sources():
        async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
            return await read_active_sources(
                request.activeSources, tenant, request.knowledgeBase, settings, client
            )

    _set_worker_stage("SOURCE_READ")
    verified = asyncio.run(read_sources())
    _set_worker_stage("PREPARE_NODES")
    nodes, sources, verified_by_node = [], {}, {}
    for source in verified:
        node = source_text_node(source, tenant, request.knowledgeBase, generation)
        nodes.append(node)
        sources[node.id_] = source.provenance
        verified_by_node[node.id_] = source
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
        limits=httpx.Limits(max_connections=settings.graph_extraction_workers, max_keepalive_connections=0),
    )
    _set_worker_stage("STORE_OPEN")
    store = graph_store(settings)
    try:
        llm = QwenGraphLLM(settings=settings, client=client)

        def cache_factory():
            try:
                from rag_engine.graph_cache import Neo4jValidatedTripletCache, cache_config_fingerprint

                return Neo4jValidatedTripletCache(
                    settings,
                    tenant=tenant,
                    knowledge_base=request.knowledgeBase,
                    build_id=generation,
                    config_fingerprint=cache_config_fingerprint(settings),
                )
            except Exception:
                return _UnavailableValidatedCache()

        cached_extractor = with_validated_cache(
            ValidatedSimpleLLMPathExtractor, cache_factory, verified_by_node
        )
        extractor = with_source_diagnostics(cached_extractor)(
            llm=llm,
            extract_prompt=EXTRACT_PROMPT,
            parse_fn=parse_triplets,
            max_paths_per_chunk=settings.graph_max_triplets_per_chunk,
            num_workers=settings.graph_extraction_workers,
            raise_on_error=True,
        )
        _set_worker_stage("INDEX_BUILD")
        PropertyGraphIndex(
            nodes=nodes,
            llm=llm,
            kg_extractors=[extractor, transform],
            property_graph_store=store,
            embed_kg_nodes=False,
            use_async=True,
        )
        # 不以索引构造返回代替持久化读回；空关系文档仍必须存在完整来源 chunk。
        _set_worker_stage("READBACK")
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
        _set_worker_stage("SOURCE_RECHECK")
        asyncio.run(read_sources())
        return {
            "processedDocumentCount": len({value.documentId for value in request.activeSources}),
            "nodeCount": counts[0]["count"],
            "edgeCount": edges[0]["count"],
        }
    finally:
        primary, primary_stage = sys.exception(), _WORKER_STAGE
        cleanup_error = None
        cleanup_stage = None
        for stage, close in (
            ("STORE_CLOSE", store.close),
            ("CLIENT_CLOSE", lambda: asyncio.run(client.aclose())),
        ):
            _set_worker_stage(stage)
            try:
                close()
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error, cleanup_stage = error, stage
        _set_worker_stage(primary_stage if primary is not None else cleanup_stage or primary_stage)
        if primary is None and cleanup_error is not None:
            raise cleanup_error


def main():
    # 由父容器限制 RSS；不用 RLIMIT_AS 误杀 NumPy/BLAS 的虚拟地址映射。
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (180, 181))
    logging.disable(logging.CRITICAL)
    _set_worker_stage("INPUT")
    try:
        raw = sys.stdin.buffer.read(9 * 1024 * 1024 + 1)
        if len(raw) > 9 * 1024 * 1024:
            raise EngineError("GRAPH_REQUEST_TOO_LARGE", 413)
        result = build(json.loads(raw))
        print(json.dumps({"result": result}))
    except BaseException as error:
        code = getattr(error, "code", None)
        if isinstance(error, GraphModelError):
            known = type(code) is str and code in GRAPH_MODEL_CODES
        else:
            known = isinstance(error, EngineError) and type(code) is str and code in WORKER_ERRORS
        code = code if known else "GRAPH_BUILD_FAILED"
        diagnostic = {"error": code}
        if code == "GRAPH_BUILD_FAILED":
            diagnostic["diagnostic"] = safe_worker_diagnostic(error, _WORKER_STAGE)
        reason = getattr(error, "reason", None)
        model_reason = (
            code in GRAPH_MODEL_CODES
            and type(reason) is str
            and reason in GRAPH_MODEL_REASONS
            and GraphModelError(reason).code == code
        )
        extraction_reason = (
            code in EXTRACTION_ERRORS
            and type(reason) is str
            and reason in VALIDATION_REASONS - GRAPH_MODEL_REASONS
        )
        if model_reason or extraction_reason:
            diagnostic["reason"] = reason
            source_hash = getattr(error, "source_fingerprint", None)
            if type(source_hash) is str and re.fullmatch(r"[0-9a-f]{64}", source_hash):
                diagnostic["sourceHash"] = source_hash
        print(json.dumps(diagnostic))


if __name__ == "__main__":
    main()
