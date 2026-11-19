"""图扩展查询计划在请求外预热：占位参数实际执行一次（匹配不到任何数据），编译与首次执行都不占
0.4 秒局部预算；热点查询带 replan=skip，图数据统计变化不在请求里重编。失败只记录固定诊断并下轮重试。"""

import asyncio
import json
import logging

import pytest
from neo4j.exceptions import ServiceUnavailable

from rag_engine import graph_jobs
from rag_engine.graph_jobs import GraphManager
from rag_engine.graph_repository import PLAN_WARMUP_TIMEOUT_SECONDS, GraphRepository
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

    async def warm_plan(self, query, **params):
        if self.failures:
            self.failures -= 1
            raise ServiceUnavailable(PRIVATE)
        self.calls.append((query, params))
        if len(self.calls) % len(plan_warmup_statements()) == 0:
            self.rounds += 1
            if self.rounds >= 2:
                self.second_round.set()


def test_hot_graph_queries_keep_cached_plans_when_graph_statistics_change():
    # 上传/删除文档会改变图统计；Neo4j 判定计划过期后在下一次查询里重编，NAS 实测 330–390 ms，
    # 落在请求里就超出 0.4 秒局部预算。replan=skip 继续用已缓存计划，只有缓存缺失时编译，由预热承担。
    for query in (NEIGHBOR_QUERY, PUBLICATION_QUERY):
        assert query.lstrip().startswith("CYPHER replan=skip\n")


def test_warmup_runs_each_retrieval_query_for_every_list_size_bucket():
    statements = plan_warmup_statements()
    # 只 EXPLAIN 时首次真实执行仍要约 40 ms 编译执行部分；预热按请求原文实际执行。
    neighbor = [params for query, params in statements if query == NEIGHBOR_QUERY]
    publication = [params for query, params in statements if query == PUBLICATION_QUERY]
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


async def test_warm_retrieval_plans_runs_every_statement_in_order():
    repository = Repository()
    await warm_retrieval_plans(repository)
    assert repository.calls == plan_warmup_statements()


class RecordingDriver:
    def __init__(self):
        self.calls = []

    async def execute_query(self, query, parameters_=None, database_=None):
        self.calls.append((query.text, query.timeout, parameters_, database_))
        return [], None, None


async def test_repository_runs_warmup_statement_unchanged_with_its_own_budget(settings):
    # 仅替换数据库 I/O；占位参数只匹配 plan-warmup 范围，读不到任何租户数据。
    repository = object.__new__(GraphRepository)
    repository.settings = settings
    repository.driver = RecordingDriver()
    query, params = plan_warmup_statements()[0]
    await repository.warm_plan(query, **params)
    assert repository.driver.calls == [(query, PLAN_WARMUP_TIMEOUT_SECONDS, params, settings.neo4j_database)]


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
