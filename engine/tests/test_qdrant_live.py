"""显式 opt-in 的真实 Qdrant 协议验收；只创建/删除本测试唯一命名的合成数据 collection。"""

import asyncio
import json
import os
import secrets

import httpx
import pytest
from conftest import auth
from fastapi.testclient import TestClient
from test_hybrid import hybrid_hit

from rag_engine.app import create_app
from rag_engine.lexical import SPARSE_MODEL
from rag_engine.sparse_index import SparseIndex

QDRANT_URL = os.environ.get("RAG_TEST_QDRANT_URL", "")
pytestmark = pytest.mark.skipif(not QDRANT_URL, reason="requires explicit RAG_TEST_QDRANT_URL")


def test_real_unnamed_dense_full_backfill_and_hybrid_api(settings):
    settings.qdrant_url = QDRANT_URL
    settings.collection = "rag_engine_acceptance_" + secrets.token_hex(8)
    settings.hybrid_enabled = True
    base = f"{QDRANT_URL}/collections/{settings.collection}"
    with httpx.Client(timeout=10, trust_env=False) as management:
        created = False
        try:
            response = management.put(base, json={"vectors": {"size": 3, "distance": "Cosine"}})
            response.raise_for_status()
            created = True
            points = []
            for index in range(140):
                point = hybrid_hit(index, f"ordinary synthetic knowledge {index}")
                point["payload"].pop("sparse_model")
                point["vector"] = [0, 1, 0]
                point.pop("score")
                points.append(point)
            points[0]["payload"]["content"] = "dense semantic answer"
            points[0]["vector"] = [1, 0, 0]
            points[1]["payload"]["content"] = "特殊条款 rare exact phrase"
            points[1]["vector"] = [-1, 0, 0]
            points[2]["payload"]["content"] = "特殊条款 semantic and lexical answer"
            points[2]["vector"] = [0.8, 0.2, 0]
            foreign = hybrid_hit(1000, "特殊条款 foreign tenant secret", tenant="tenant-b")
            foreign.pop("score")
            foreign["payload"].pop("sparse_model")
            foreign["vector"] = [1, 0, 0]
            management.put(
                base + "/points", params={"wait": "true"}, json={"points": points + [foreign]}
            ).raise_for_status()
            before = management.post(base + "/points", json={"ids": [0, 1, 2], "with_vector": True}).json()

            async def migrate():
                async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                    index = SparseIndex(settings, client)
                    assert (await index.prepare(False))["schemaReady"] is False
                    assert (await index.prepare(True))["schemaReady"] is True
                    assert await index.missing_count("tenant-a", "general") == 140
                    result = await index.backfill("tenant-a", "general", True)
                    assert result == {
                        "inspected": 140,
                        "written": 140,
                        "totalActive": 140,
                        "missing": 0,
                        "complete": True,
                    }
                    assert await index.missing_count("tenant-b", "general") == 1

            asyncio.run(migrate())
            after = management.post(base + "/points", json={"ids": [0, 1, 2], "with_vector": True}).json()
            originals = {point["id"]: point["vector"] for point in before["result"]}
            for point in after["result"]:
                assert point["vector"][""] == originals[point["id"]]
                assert point["vector"][SPARSE_MODEL]["indices"]

            class Transport(httpx.AsyncBaseTransport):
                def __init__(self):
                    self.network = httpx.AsyncHTTPTransport()

                async def handle_async_request(self, request):
                    if request.url.host == "models.example.test":
                        if request.url.path.endswith("/embeddings"):
                            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0, 0]}]})
                        return httpx.Response(503, text="synthetic rerank unavailable")
                    return await self.network.handle_async_request(request)

                async def aclose(self):
                    await self.network.aclose()

            with TestClient(create_app(settings, Transport())) as client:
                response = client.post(
                    "/v1/retrievals",
                    headers=auth(settings),
                    json={
                        "query": "特殊条款",
                        "knowledgeBase": "general",
                        "topK": 3,
                        "candidateCount": 5,
                        "similarityThreshold": 0.2,
                        "maxContextCharacters": 12000,
                    },
                )
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["mode"] == "hybrid" and result["degraded"] is True
            assert result["hits"][0]["id"] == "2"
            assert {point["id"] for point in result["hits"]} == {"0", "1", "2"}
            assert "foreign" not in json.dumps(result)
            # 旧服务单写替换向量后，真实性检查发现缺失；不能沿用旧 sparse 标记冒充完整。
            management.put(
                base + "/points", params={"wait": "true"}, json={"points": [points[1]]}
            ).raise_for_status()

            async def check_old_writer():
                async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                    assert await SparseIndex(settings, client).missing_count("tenant-a", "general") == 1

            asyncio.run(check_old_writer())
        finally:
            if created:
                management.delete(base).raise_for_status()
                assert management.get(base).status_code == 404
