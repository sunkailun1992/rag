"""Qdrant 保温：定期只读触碰全部 payload 与向量索引，防止长时间空闲后首个检索冷读机械盘。
Qdrant 为合成替身，不连接任何真实向量库。"""

import asyncio
import json
import logging

import httpx
import pytest
from conftest import auth
from pydantic import SecretStr

from rag_engine import vector_warmup
from rag_engine.app import create_app
from rag_engine.lexical import SPARSE_MODEL
from rag_engine.vector_warmup import warm_vectors_once

BASE = "/collections/knowledge-vectors"


def recorder(pages=(None,)):
    seen = []
    offsets = iter(pages)

    def handler(request):
        seen.append(
            (request.method, request.url.path, json.loads(request.content), request.headers.get("api-key"))
        )
        if request.url.path == BASE + "/points/scroll":
            return httpx.Response(200, json={"result": {"points": [], "next_page_offset": next(offsets, 1)}})
        return httpx.Response(200, json={"result": []})

    return seen, handler


@pytest.mark.asyncio
async def test_one_round_reads_every_payload_page_and_both_vector_indexes(settings):
    settings.hybrid_enabled = True
    settings.qdrant_api_key = SecretStr("synthetic-qdrant-key")
    seen, handler = recorder(pages=(7, None))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await warm_vectors_once(settings, client)
    # 只取一个小字段：Qdrant 仍读出整条 payload，页被触碰，返回量与暴露面都小。
    scrolls = [body for _, path, body, _ in seen if path == BASE + "/points/scroll"]
    assert scrolls == [
        {"limit": 1000, "with_payload": ["tenant_id"], "with_vector": False},
        {"limit": 1000, "with_payload": ["tenant_id"], "with_vector": False, "offset": 7},
    ]
    searches = [body for _, path, body, _ in seen if path == BASE + "/points/search"]
    # 精确检索扫过全部稠密向量，HNSW 检索触碰索引图；稀疏检索走混合检索的另一路。
    assert [body["params"]["exact"] for body in searches[:2]] == [True, False]
    assert all(body["vector"] == [1.0, 0.0, 0.0] for body in searches[:2])
    assert searches[2]["vector"]["name"] == SPARSE_MODEL and searches[2]["vector"]["vector"]["indices"]
    assert all(body["limit"] == 1 and body["with_payload"] is False for body in searches)
    assert all(body["with_vector"] is False for body in searches)
    assert len(searches) == 3
    assert all(method == "POST" and key == "synthetic-qdrant-key" for method, _, _, key in seen)


@pytest.mark.asyncio
async def test_round_skips_sparse_index_when_hybrid_retrieval_is_off(settings):
    settings.hybrid_enabled = False
    seen, handler = recorder()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await warm_vectors_once(settings, client)
    searches = [body for _, path, body, _ in seen if path == BASE + "/points/search"]
    assert len(searches) == 2 and all(isinstance(body["vector"], list) for body in searches)


@pytest.mark.asyncio
async def test_round_stops_paging_at_its_bound_on_a_large_collection(settings):
    seen, handler = recorder(pages=())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await warm_vectors_once(settings, client)
    scrolls = [body for _, path, body, _ in seen if path == BASE + "/points/scroll"]
    assert len(scrolls) == vector_warmup.WARMUP_MAX_PAGES


@pytest.mark.asyncio
async def test_app_keeps_vectors_warm_in_background_and_retries_after_failure(settings, caplog):
    settings.qdrant_warmup_seconds = 0.01
    rounds = []
    second = asyncio.Event()

    def handler(request):
        if request.url.path == BASE + "/points/scroll":
            rounds.append(request)
            if len(rounds) == 1:
                return httpx.Response(503)
            second.set()
            return httpx.Response(200, json={"result": {"points": [], "next_page_offset": None}})
        return httpx.Response(200, json={"result": []})

    app = create_app(settings, transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with asyncio.timeout(3):
            await second.wait()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            # 保温不计入就绪，失败也不影响健康与检索入口。
            assert (await client.get("/health", headers=auth(settings))).status_code == 200
    stopped_at = len(rounds)
    await asyncio.sleep(0.05)
    assert len(rounds) == stopped_at
    records = [record for record in caplog.records if "rag_vector_warmup_failed" in record.getMessage()]
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    event = json.loads(records[0].getMessage())
    assert event["diagnostic"] == "rag_vector_warmup_failed code=VECTOR_STORE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_warmup_can_be_switched_off(settings):
    settings.qdrant_warmup_seconds = 0
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"result": []})

    app = create_app(settings, transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.05)
    assert seen == []
