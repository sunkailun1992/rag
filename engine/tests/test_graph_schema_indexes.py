"""启动行为确保同名实体检索有租户/知识库复合索引，并保留租约失败边界。"""

import re

import pytest

from rag_engine.errors import EngineError
from rag_engine.graph_repository import GraphRepository


def repository(settings, *, fail_index=False, lease=True):
    calls = []
    # 仅替换数据库I/O；执行真实start，不连接开发库或获取全局租约。
    result = object.__new__(GraphRepository)
    result.settings = settings
    result.owner = "synthetic-owner"

    async def rows(query, **params):
        calls.append((query, params))
        if query.startswith("CREATE INDEX rag_graph_entity_canonical ") and fail_index:
            raise RuntimeError("Synthetic index creation failure")
        if "SET r.owner=$owner,r.leaseUntil" in query:
            return [{"key": "worker"}] if lease else []
        return []

    result.rows = rows
    return result, calls


async def test_start_creates_scoped_canonical_index_before_worker_lease_and_is_idempotent(settings):
    value, calls = repository(settings)
    await value.start()
    await value.start()
    ddl = [query for query, _ in calls if query.startswith("CREATE INDEX rag_graph_entity_canonical ")]
    assert len(ddl) == 2
    pattern = (
        r"CREATE INDEX rag_graph_entity_canonical IF NOT EXISTS FOR \(n:__Entity__\) "
        r"ON \(n\.tenant_id,n\.knowledge_base,n\.canonical_name\)"
    )
    assert all(re.fullmatch(pattern, query) for query in ddl)
    first_index = next(i for i, (q, _) in enumerate(calls) if q in ddl)
    first_lease = next(i for i, (q, _) in enumerate(calls) if "SET r.owner=$owner,r.leaseUntil" in q)
    assert first_index < first_lease
    assert not any("DROP " in query for query, _ in calls)


async def test_failed_canonical_index_does_not_claim_or_recover_graph_jobs(settings):
    value, calls = repository(settings, fail_index=True)
    with pytest.raises(RuntimeError, match="Synthetic index creation failure"):
        await value.start()
    assert not any("SET r.owner=$owner,r.leaseUntil" in query for query, _ in calls)
    assert not any("GRAPH_WORKER_RESTARTED" in query for query, _ in calls)


async def test_schema_addition_keeps_existing_worker_lease_guard(settings):
    value, calls = repository(settings, lease=False)
    with pytest.raises(EngineError, match="GRAPH_WORKER_ALREADY_RUNNING"):
        await value.start()
    assert any(query.startswith("CREATE INDEX rag_graph_entity_canonical ") for query, _ in calls)
    assert not any("GRAPH_WORKER_RESTARTED" in query for query, _ in calls)
