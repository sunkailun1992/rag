"""后台调度的每次 Neo4j 往返都有客户端上限：服务端事务超时只在连接仍有回应时生效，
失效连接会让 await 永远挂起，心跳、领取随之静默停摆。数据库与连接均为合成替身。"""

import asyncio
import json
import logging

import pytest

from rag_engine import graph_jobs, graph_repository
from rag_engine.graph_jobs import GraphManager
from rag_engine.graph_repository import GraphRepository


class SilentDriver:
    """模拟服务端永不回应的连接：请求发出后既无结果也无错误。"""

    def __init__(self):
        self.transaction = SilentTransaction()
        self.session_exited = False

    async def execute_query(self, query, parameters_=None, database_=None):
        await asyncio.Event().wait()

    def session(self, database=None):
        return SilentSession(self)


class SilentSession:
    def __init__(self, driver):
        self.driver = driver

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        self.driver.session_exited = True

    async def begin_transaction(self, timeout=None):
        return self.driver.transaction


class SilentTransaction:
    def __init__(self):
        self.exited = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        self.exited = True

    async def run(self, *_args, **_kwargs):
        await asyncio.Event().wait()

    async def commit(self):
        pass


def bounded_repository(settings, monkeypatch):
    settings.graph_query_timeout_seconds = 0.1
    monkeypatch.setattr(graph_repository, "CLIENT_GRACE_SECONDS", 0.05)
    # 仅替换数据库 I/O，执行真实的 rows/transaction。
    repository = object.__new__(GraphRepository)
    repository.settings = settings
    repository.owner = "synthetic-owner"
    repository.driver = SilentDriver()
    return repository


@pytest.mark.asyncio
async def test_query_on_a_connection_that_never_answers_fails_instead_of_hanging(settings, monkeypatch):
    repository = bounded_repository(settings, monkeypatch)
    loop = asyncio.get_running_loop()
    started = loop.time()
    # 外层守卫只保证缺少客户端上限时测试失败而不是永远挂起。
    async with asyncio.timeout(3):
        with pytest.raises(TimeoutError):
            await repository.rows("MATCH (r:RagGraphRuntime {key:'worker'}) RETURN r.key AS key")
    assert loop.time() - started < 1


@pytest.mark.asyncio
async def test_transaction_on_a_connection_that_never_answers_fails_and_releases_the_session(
    settings, monkeypatch
):
    repository = bounded_repository(settings, monkeypatch)
    loop = asyncio.get_running_loop()
    started = loop.time()
    async with asyncio.timeout(3):
        with pytest.raises(TimeoutError):
            async with repository.transaction() as tx:
                await tx.run("MATCH (j:RagGraphJob {status:'QUEUED'}) RETURN j.buildId AS buildId")
    assert loop.time() - started < 1
    # 超时沿事务与会话退出，连接交还驱动处置，不留在池里继续挂起。
    assert repository.driver.transaction.exited is True
    assert repository.driver.session_exited is True


class HeartbeatRepository:
    def __init__(self, timeouts):
        # timeouts 为前几次心跳超时；None 表示一直超时。
        self.timeouts = timeouts
        self.heartbeats = 0
        self.renewed = asyncio.Event()

    async def start(self):
        pass

    async def stop(self):
        pass

    async def heartbeat(self):
        self.heartbeats += 1
        if self.timeouts is None or self.heartbeats <= self.timeouts:
            # 等同 rows() 的客户端上限到点。
            await asyncio.sleep(0.15)
            raise TimeoutError
        self.renewed.set()

    async def claim(self):
        await asyncio.Event().wait()


def short_lease(settings, monkeypatch):
    settings.graph_enabled = True
    # 只缩短测试时钟；生产配置的租约下限仍是 10 秒。
    settings.graph_lease_seconds = 1
    settings.graph_query_timeout_seconds = 0.1
    monkeypatch.setattr(graph_repository, "CLIENT_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(graph_jobs, "HEARTBEAT_RETRY_SECONDS", 0.01)


@pytest.mark.asyncio
async def test_heartbeat_timeout_is_retried_before_the_lease_expires_without_failing_closed(
    settings, monkeypatch, caplog
):
    short_lease(settings, monkeypatch)
    repository = HeartbeatRepository(timeouts=1)
    manager = GraphManager(settings, None, repository)
    await manager.start()
    try:
        async with asyncio.timeout(3):
            await repository.renewed.wait()
        assert repository.heartbeats == 2
        assert manager.is_ready() is True
        assert not [record for record in caplog.records if "GRAPH_WORKER_LEASE_LOST" in record.getMessage()]
        retries = [
            record for record in caplog.records if "rag_graph_worker_heartbeat_retry" in record.getMessage()
        ]
        assert len(retries) == 1 and retries[0].levelno == logging.WARNING
        event = json.loads(retries[0].getMessage())
        assert event["diagnostic"] == "rag_graph_worker_heartbeat_retry code=GRAPH_STORE_UNAVAILABLE"
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_heartbeat_that_keeps_timing_out_fails_closed_before_the_lease_expires(
    settings, monkeypatch, caplog
):
    short_lease(settings, monkeypatch)
    repository = HeartbeatRepository(timeouts=None)
    manager = GraphManager(settings, None, repository)
    loop = asyncio.get_running_loop()
    started = loop.time()
    await manager.start()
    try:
        async with asyncio.timeout(3):
            await manager.tasks[0]
        assert loop.time() - started < settings.graph_lease_seconds + 0.5
        assert repository.heartbeats >= 2
        assert manager.is_ready() is False
        records = [record for record in caplog.records if "GRAPH_WORKER_LEASE_LOST" in record.getMessage()]
        assert len(records) == 1 and records[0].levelno == logging.ERROR
        assert json.loads(records[0].getMessage())["failure_class"] == "TimeoutError"
    finally:
        await manager.stop()
