"""图扩展查询计划在请求外预编译；编译不占 0.4 秒局部预算，失败只记录固定诊断并下轮重试。"""

import asyncio
import json
import logging

import pytest
from neo4j.exceptions import ServiceUnavailable

from rag_engine import graph_jobs
from rag_engine.graph_jobs import GraphManager
from rag_engine.graph_retrieval import (
    NEIGHBOR_QUERY,
    PUBLICATION_QUERY,
    plan_warmup_statements,
    warm_retrieval_plans,
)

PRIVATE = "SYNTHETIC_PRIVATE_PLAN_FAILURE"


class Repository:
    def __init__(self, failures=0):
        self.failures = failures
        self.calls = []
        self.rounds = 0
        self.second_round = asyncio.Event()

    async def start(self):
        pass

    async def stop(self):
        pass

    async def heartbeat(self):
        await asyncio.Event().wait()

    async def claim(self):
        await asyncio.Event().wait()

    async def explain(self, query, **params):
        if self.failures:
            self.failures -= 1
            raise ServiceUnavailable(PRIVATE)
        self.calls.append((query, params))
        if len(self.calls) % len(plan_warmup_statements()) == 0:
            self.rounds += 1
            if self.rounds >= 2:
                self.second_round.set()


def test_warmup_explains_each_retrieval_query_for_every_list_size_bucket():
    statements = plan_warmup_statements()
    neighbor = [params for query, params in statements if query == "EXPLAIN " + NEIGHBOR_QUERY]
    publication = [params for query, params in statements if query == "EXPLAIN " + PUBLICATION_QUERY]
    assert len(statements) == len(neighbor) + len(publication)
    # Neo4j 5.26 按列表参数大小分档缓存计划（1、2–10、11–100）；
    # 请求 seeds/excluded ≤100、sources ≤20。
    assert sorted(len(params["seeds"]) for params in neighbor) == [1, 10, 100]
    assert all(len(params["excluded"]) == len(params["seeds"]) for params in neighbor)
    assert all(params["limit"] == 20 for params in neighbor)
    assert sorted(len(params["sources"]) for params in publication) == [1, 10, 20]
    for params in neighbor:
        assert all(
            set(seed) == {"documentKey", "documentId", "pointId", "sourceKey"} for seed in params["seeds"]
        )
        assert all(isinstance(value, str) for seed in params["seeds"] for value in seed.values())
        assert all(isinstance(value, str) for value in params["excluded"])
    for params in publication:
        assert all(
            set(source) == {"documentKey", "documentId", "pointId", "sourceKey", "generation"}
            for source in params["sources"]
        )
        assert all(isinstance(value, str) for source in params["sources"] for value in source.values())
    assert all(
        isinstance(params["tenant"], str) and isinstance(params["kb"], str) for _, params in statements
    )


async def test_warm_retrieval_plans_explains_every_statement_in_order():
    repository = Repository()
    await warm_retrieval_plans(repository)
    assert repository.calls == plan_warmup_statements()


@pytest.mark.asyncio
async def test_manager_prewarms_plans_outside_readiness_and_retries_after_failure(
    settings, monkeypatch, caplog
):
    settings.graph_enabled = True
    settings.graph_retrieval_enabled = True
    monkeypatch.setattr(graph_jobs, "PLAN_WARMUP_INTERVAL_SECONDS", 0.01)
    repository = Repository(failures=1)
    manager = GraphManager(settings, None, repository)
    await manager.start()
    try:
        async with asyncio.timeout(5):
            await repository.second_round.wait()
        assert manager.is_ready() is True
        assert len(manager.tasks) == 2
        assert repository.calls[-len(plan_warmup_statements()) :] == plan_warmup_statements()
        records = [
            record for record in caplog.records if "rag_graph_plan_warmup_failed" in record.getMessage()
        ]
        assert len(records) == 1 and records[0].levelno == logging.WARNING
        event = json.loads(records[0].getMessage())
        assert event["event"] == "rag_diagnostic"
        assert event["diagnostic"] == "rag_graph_plan_warmup_failed code=GRAPH_STORE_UNAVAILABLE"
        assert PRIVATE not in caplog.text
    finally:
        await manager.stop()
    assert manager.plan_warmup is None


@pytest.mark.asyncio
async def test_manager_skips_plan_warmup_when_graph_retrieval_is_disabled(settings):
    settings.graph_enabled = True
    settings.graph_retrieval_enabled = False
    repository = Repository()
    manager = GraphManager(settings, None, repository)
    await manager.start()
    try:
        await asyncio.sleep(0.05)
        assert manager.plan_warmup is None
        assert repository.calls == []
        assert manager.is_ready() is True
    finally:
        await manager.stop()
