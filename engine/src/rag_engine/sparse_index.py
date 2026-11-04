"""显式的稀疏 schema/完整回填；查询只检查 readiness，绝不自动写库。"""

import argparse
import asyncio
import json
import math
import re

import httpx

from rag_engine.config import Settings
from rag_engine.errors import EngineError
from rag_engine.http import request_json
from rag_engine.lexical import SPARSE_MODEL, encode_sparse


def scoped_filter(tenant: str, knowledge_base: str) -> dict:
    return {
        "must": [
            {"key": "tenant_id", "match": {"value": tenant}},
            {"key": "knowledge_base", "match": {"value": knowledge_base}},
            {"key": "document_status", "match": {"value": "ACTIVE"}},
        ]
    }


class SparseIndex:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client
        self.base = f"{settings.qdrant_url}/collections/{settings.collection}"
        self.headers = {}
        if settings.qdrant_api_key.get_secret_value():
            self.headers["api-key"] = settings.qdrant_api_key.get_secret_value()

    async def call(self, method: str, path: str, body: dict | None = None):
        return await request_json(
            self.client, method, self.base + path, body, self.headers, self.settings.max_upstream_bytes
        )

    async def schema(self, required: bool = True) -> bool:
        response = await self.call("GET", "")
        try:
            params = response["result"]["config"]["params"]
            dense = params["vectors"]
            if "size" not in dense:
                dense = dense[""]
            if dense["size"] != self.settings.embedding_dimension or dense["distance"] != "Cosine":
                raise EngineError("EMBEDDING_SCHEMA_MISMATCH", 503)
            sparse = (params.get("sparse_vectors") or {}).get(SPARSE_MODEL)
            if sparse is None:
                if required:
                    raise EngineError("SPARSE_SCHEMA_NOT_READY", 503)
                return False
            if sparse.get("modifier", "").lower() != "idf":
                raise EngineError("SPARSE_SCHEMA_MISMATCH", 503)
            return True
        except (KeyError, TypeError, AttributeError) as error:
            raise EngineError("VECTOR_INVALID_RESPONSE") from error

    async def count(self, query_filter: dict) -> int:
        response = await self.call("POST", "/points/count", {"filter": query_filter, "exact": True})
        try:
            count = response["result"]["count"]
            if type(count) is not int or count < 0:
                raise ValueError("count")
            return count
        except (KeyError, TypeError, ValueError) as error:
            raise EngineError("VECTOR_INVALID_RESPONSE") from error

    async def missing_count(self, tenant: str, knowledge_base: str) -> int:
        query_filter = scoped_filter(tenant, knowledge_base)
        # 要求每个 ACTIVE 点既有真实 sparse 值，又使用本版编码；不能只靠迁移完成标志。
        query_filter["must_not"] = [
            {
                "must": [
                    {"has_vector": SPARSE_MODEL},
                    {"key": "sparse_model", "match": {"value": SPARSE_MODEL}},
                ]
            }
        ]
        return await self.count(query_filter)

    async def require_ready(self, tenant: str, knowledge_base: str):
        await self.schema()
        if await self.missing_count(tenant, knowledge_base):
            raise EngineError("SPARSE_INDEX_INCOMPLETE", 503)

    async def prepare(self, apply: bool) -> dict:
        exists = await self.schema(required=False)
        if apply and not exists:
            await self.call("PUT", f"/vectors/{SPARSE_MODEL}", {"sparse": {"modifier": "idf"}})
            await self.schema()
        return {"sparseModel": SPARSE_MODEL, "schemaReady": exists or apply, "applied": apply and not exists}

    async def backfill(self, tenant: str, knowledge_base: str, apply: bool, batch_size: int = 64) -> dict:
        if not 1 <= batch_size <= 100:
            raise EngineError("INVALID_BATCH_SIZE", 422)
        if apply:
            await self.schema()
        offset = None
        visited = set()
        inspected = 0
        written = 0
        query_filter = scoped_filter(tenant, knowledge_base)
        while True:
            body = {"filter": query_filter, "limit": batch_size, "with_payload": True, "with_vector": [""]}
            if offset is not None:
                body["offset"] = offset
            response = await self.call("POST", "/points/scroll", body)
            try:
                points = response["result"]["points"]
                next_offset = response["result"]["next_page_offset"]
                if not isinstance(points, list) or len(points) > batch_size:
                    raise ValueError("page")
                updates = []
                ids = []
                for point in points:
                    payload = point["payload"]
                    expected = {
                        "tenant_id": tenant,
                        "knowledge_base": knowledge_base,
                        "document_status": "ACTIVE",
                    }
                    if any(payload.get(key) != value for key, value in expected.items()):
                        raise EngineError("TENANT_ISOLATION_VIOLATION")
                    if (
                        payload.get("embedding_model") != self.settings.embedding_model
                        or payload.get("embedding_dimension") != self.settings.embedding_dimension
                    ):
                        raise EngineError("EMBEDDING_METADATA_MISMATCH")
                    content = payload.get("content")
                    if not isinstance(content, str) or not content or len(content) > 6000:
                        raise ValueError("content")
                    dense = point["vector"]
                    if isinstance(dense, dict):
                        dense = dense[""]
                    if (
                        not isinstance(dense, list)
                        or len(dense) != self.settings.embedding_dimension
                        or any(type(value) not in {int, float} or not math.isfinite(value) for value in dense)
                    ):
                        raise ValueError("dense")
                    sparse = encode_sparse(content)
                    if not sparse["indices"]:
                        raise EngineError("SPARSE_TEXT_UNSUPPORTED", 422)
                    ids.append(point["id"])
                    updates.append({"id": point["id"], "vector": {SPARSE_MODEL: sparse}})
                if next_offset is not None:
                    if not points or not isinstance(next_offset, (str, int)) or next_offset in visited:
                        raise ValueError("invalid/repeated cursor")
                    visited.add(next_offset)
            except (KeyError, TypeError, ValueError) as error:
                raise EngineError("VECTOR_INVALID_RESPONSE") from error
            inspected += len(points)
            if apply and updates:
                # UpdateVectors 只改指定 sparse；不能使用 upsert 覆盖已有 dense 或 payload。
                result = await self.call("PUT", "/points/vectors?wait=true", {"points": updates})
                if result.get("result", {}).get("status") != "completed":
                    raise EngineError("SPARSE_WRITE_INCOMPLETE")
                result = await self.call(
                    "POST",
                    "/points/payload?wait=true",
                    {
                        "payload": {"sparse_model": SPARSE_MODEL},
                        "points": ids,
                    },
                )
                if result.get("result", {}).get("status") != "completed":
                    raise EngineError("SPARSE_WRITE_INCOMPLETE")
                written += len(updates)
            if next_offset is None:
                break
            offset = next_offset
        # count 是全量过滤结果，不将已滚动数量误当成完整索引证明。
        total = await self.count(query_filter)
        missing = await self.missing_count(tenant, knowledge_base) if apply else None
        if total != inspected or (apply and missing != 0):
            raise EngineError("SPARSE_BACKFILL_NOT_COMPLETE", 503)
        return {
            "inspected": inspected,
            "written": written,
            "totalActive": total,
            "missing": missing,
            "complete": apply and missing == 0,
        }


async def run_cli(arguments) -> dict:
    settings = Settings()
    if not settings.qdrant_url:
        raise EngineError("RETRIEVAL_NOT_CONFIGURED", 503)
    if arguments.command == "backfill" and arguments.apply and not arguments.writers_paused:
        raise EngineError("WRITERS_PAUSED_CONFIRMATION_REQUIRED", 422)
    for value in [arguments.tenant, arguments.knowledge_base]:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            raise EngineError("INVALID_SCOPE", 422)
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
        index = SparseIndex(settings, client)
        if arguments.command == "prepare":
            return await index.prepare(arguments.apply)
        if not arguments.tenant or not arguments.knowledge_base:
            raise EngineError("SCOPE_REQUIRED", 422)
        if arguments.command == "status":
            await index.schema()
            return {
                "totalActive": await index.count(scoped_filter(arguments.tenant, arguments.knowledge_base)),
                "missing": await index.missing_count(arguments.tenant, arguments.knowledge_base),
            }
        return await index.backfill(arguments.tenant, arguments.knowledge_base, arguments.apply)


def main():
    parser = argparse.ArgumentParser(
        description="显式准备/完整回填 lexical-v1，不创建 collection、不修改 dense/文档状态。"
    )
    parser.add_argument("command", choices=["prepare", "status", "backfill"])
    parser.add_argument("--tenant")
    parser.add_argument("--knowledge-base")
    parser.add_argument("--apply", action="store_true", help="默认只检查；指定后允许 schema/稀疏向量写入")
    parser.add_argument(
        "--writers-paused", action="store_true", help="确认该知识库 Java 摄取/删除/状态变更已经暂停"
    )
    arguments = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run_cli(arguments)), ensure_ascii=False))
    except (EngineError, httpx.HTTPError) as error:
        code = error.code if isinstance(error, EngineError) else "VECTOR_UNAVAILABLE"
        print(json.dumps({"error": {"code": code}}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
