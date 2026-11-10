"""真实异步 task/ASGI 回归；模型、数据库和来源均为合成边界。"""

import asyncio
import json
import logging

import httpx
import pytest
from conftest import auth
from neo4j.exceptions import ServiceUnavailable

from rag_engine.app import create_app
from rag_engine.graph_jobs import GraphManager
from rag_engine.graph_models import GraphBuild

PRIVATE = "SYNTHETIC_PRIVATE_LEASE_FAILURE"


class Repository:
    def __init__(self):
        self.heartbeat_started = asyncio.Event()
        self.fail_heartbeat = asyncio.Event()
        self.claim_started = asyncio.Event()
        self.submissions = 0

    async def start(self):
        pass

    async def stop(self):
        pass

    async def heartbeat(self):
        self.heartbeat_started.set()
        await self.fail_heartbeat.wait()
        raise ServiceUnavailable(PRIVATE)

    async def claim(self):
        self.claim_started.set()
        await asyncio.Event().wait()

    async def submit(self, _tenant, request, build_id):
        self.submissions += 1
        return GraphBuild(
            buildId=build_id,
            knowledgeBase=request.knowledgeBase,
            status="QUEUED",
            documentCount=0,
            processedDocumentCount=0,
            nodeCount=0,
            edgeCount=0,
        )


@pytest.mark.asyncio
async def test_failed_heartbeat_makes_health_and_inflight_enqueue_fail_closed(settings, monkeypatch, caplog):
    from rag_engine import graph_jobs

    settings.graph_enabled = True
    settings.graph_lease_seconds = 10
    settings.graph_query_timeout_seconds = 10
    repository = Repository()
    manager = GraphManager(settings, None, repository)
    source_started, release_source = asyncio.Event(), asyncio.Event()

    async def sources(*_args):
        source_started.set()
        await release_source.wait()
        return []

    monkeypatch.setattr(graph_jobs, "read_active_sources", sources)
    app = create_app(settings)
    app.state.graph = manager
    await manager.start()
    pending = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 200
            pending = asyncio.create_task(
                client.post(
                    "/v1/graphs/builds",
                    headers=auth(settings),
                    json={"knowledgeBase": "general", "activeSources": [], "reconcile": True},
                )
            )
            async with asyncio.timeout(5):
                await source_started.wait()
                await repository.heartbeat_started.wait()
                repository.fail_heartbeat.set()
                await manager.tasks[0]
                await asyncio.gather(manager.tasks[1], return_exceptions=True)
            release_source.set()
            response = await pending
            health = await client.get("/health")
            assert health.status_code == 503
            assert health.json() == {"status": "down"}
            assert response.status_code == 503
            assert response.json() == {"error": {"code": "GRAPH_WORKER_UNAVAILABLE"}}
            assert repository.submissions == 0
            assert manager.is_ready() is False
        records = [record for record in caplog.records if "GRAPH_WORKER_LEASE_LOST" in record.getMessage()]
        assert len(records) == 1 and records[0].levelno == logging.ERROR
        event = json.loads(records[0].getMessage())
        assert event["failure_class"] == "ServiceUnavailable"
        assert PRIVATE not in caplog.text
    finally:
        release_source.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await manager.stop()


@pytest.mark.asyncio
async def test_finished_pump_rejects_new_graph_work_and_health_without_restarting(settings):
    settings.graph_enabled = True
    repository = Repository()
    manager = GraphManager(settings, None, repository)
    app = create_app(settings)
    app.state.graph = manager
    await manager.start()
    try:
        await repository.claim_started.wait()
        manager.tasks[1].cancel()
        await asyncio.gather(manager.tasks[1], return_exceptions=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 503
            response = await client.post(
                "/v1/graphs/builds",
                headers=auth(settings),
                json={"knowledgeBase": "general", "activeSources": []},
            )
            assert response.status_code == 503
            assert response.json() == {"error": {"code": "GRAPH_WORKER_UNAVAILABLE"}}
        assert manager.is_ready() is False
        assert repository.submissions == 0
        assert len(manager.tasks) == 2
    finally:
        await manager.stop()
    assert manager.is_ready() is False


@pytest.mark.asyncio
async def test_health_distinguishes_disabled_graph_and_unsuccessful_start(settings):
    class UnavailableRepository(Repository):
        async def start(self):
            raise ServiceUnavailable(PRIVATE)

    settings.graph_enabled = True
    manager = GraphManager(settings, None, UnavailableRepository())
    with pytest.raises(ServiceUnavailable):
        await manager.start()
    app = create_app(settings)
    app.state.graph = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 503
        settings.graph_enabled = False
        healthy = await client.get("/health")
        assert healthy.status_code == 200 and healthy.json() == {"status": "up"}
    assert manager.is_ready() is False
    assert manager.tasks == []
