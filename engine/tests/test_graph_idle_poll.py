"""空闲领取间隔与提交唤醒：空闲时不高频写 Neo4j 领取锁，本进程提交的新任务仍立即被领取。"""

import asyncio

import pytest
from pydantic import ValidationError

from rag_engine import graph_jobs
from rag_engine.config import Settings
from rag_engine.graph_jobs import GraphManager
from rag_engine.graph_models import GraphBuild, GraphBuildRequest


class Repository:
    def __init__(self):
        self.claims = 0
        self.first_claim = asyncio.Event()
        self.second_claim = asyncio.Event()

    async def claim(self):
        self.claims += 1
        (self.first_claim if self.claims == 1 else self.second_claim).set()
        return None

    async def cleanup_pending(self):
        pass

    async def submit(self, _tenant, request, build_id):
        return GraphBuild(
            buildId=build_id,
            knowledgeBase=request.knowledgeBase,
            status="QUEUED",
            documentCount=0,
            processedDocumentCount=0,
            nodeCount=0,
            edgeCount=0,
        )


def _settings_from_environment(settings):
    return Settings(
        api_key=settings.api_key,
        qwen_api_key=settings.qwen_api_key,
        qwen_base_url=settings.qwen_base_url,
        qdrant_url=settings.qdrant_url,
        embedding_dimension=settings.embedding_dimension,
    )


def test_idle_poll_defaults_to_five_seconds_and_reads_environment(settings, monkeypatch):
    assert settings.graph_idle_poll_seconds == 5
    monkeypatch.setenv("RAG_ENGINE_GRAPH_IDLE_POLL_SECONDS", "12")
    assert _settings_from_environment(settings).graph_idle_poll_seconds == 12
    for invalid in ["0", "61"]:
        monkeypatch.setenv("RAG_ENGINE_GRAPH_IDLE_POLL_SECONDS", invalid)
        with pytest.raises(ValidationError):
            _settings_from_environment(settings)


@pytest.mark.asyncio
async def test_idle_wait_returns_as_soon_as_woken_and_clears_the_flag(settings):
    settings.graph_idle_poll_seconds = 60
    manager = GraphManager(settings, None, Repository())
    waiting = asyncio.create_task(manager.idle_wait())
    for _ in range(5):
        await asyncio.sleep(0)
    assert not waiting.done()
    manager.wakeup.set()
    async with asyncio.timeout(5):
        await waiting
    assert not manager.wakeup.is_set()


@pytest.mark.asyncio
async def test_idle_pump_waits_for_the_interval_and_submit_wakes_it(settings, monkeypatch):
    settings.graph_enabled = True
    settings.graph_idle_poll_seconds = 60
    repository = Repository()

    async def sources(*_args):
        return []

    monkeypatch.setattr(graph_jobs, "read_active_sources", sources)
    manager = GraphManager(settings, None, repository)
    monkeypatch.setattr(manager, "is_ready", lambda: True)
    pump = asyncio.create_task(manager.pump())
    try:
        async with asyncio.timeout(5):
            await repository.first_claim.wait()
        # 旧实现 0.5 秒就会再写一次领取锁；空闲间隔内不应再次领取。
        await asyncio.sleep(0.7)
        assert repository.claims == 1
        await manager.submit("tenant-a", GraphBuildRequest(knowledgeBase="general", activeSources=[]))
        async with asyncio.timeout(5):
            await repository.second_claim.wait()
        assert repository.claims == 2
    finally:
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
