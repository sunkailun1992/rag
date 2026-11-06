"""通过真实官方Neo4j适配器验证批量存储与交互查询的事务预算隔离。"""

from contextlib import asynccontextmanager

import neo4j
import pytest
from llama_index.core.graph_stores.types import EntityNode
from neo4j import Record
from pydantic import ValidationError

from rag_engine import graph_extract, graph_repository
from rag_engine.config import Settings


def install_drivers(monkeypatch):
    calls = {"sync": [], "async": [], "transactions": [], "factories": []}

    class SyncDriver:
        def execute_query(self, query, **kwargs):
            calls["sync"].append((query, kwargs))
            rows = [Record({"versions": ["5.26.32"]})] if query.text == "CALL dbms.components()" else []
            return rows, None, None

        def close(self):
            pass

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def commit(self):
            pass

    class Session:
        async def begin_transaction(self, *, timeout):
            calls["transactions"].append(timeout)
            return Transaction()

    class AsyncDriver:
        async def execute_query(self, query, **kwargs):
            calls["async"].append((query, kwargs))
            return [], None, None

        @asynccontextmanager
        async def session(self, **_kwargs):
            yield Session()

    def sync_factory(*_args, **kwargs):
        calls["factories"].append(kwargs)
        return SyncDriver()

    def async_factory(*_args, **kwargs):
        calls["factories"].append(kwargs)
        return AsyncDriver()

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", sync_factory)
    monkeypatch.setattr(graph_repository.AsyncGraphDatabase, "driver", async_factory)
    return calls


@pytest.mark.filterwarnings(
    "ignore:The `dict` method is deprecated:DeprecationWarning:llama_index.graph_stores.neo4j"
)
async def test_official_storage_queries_use_batch_budget_while_repository_keeps_interactive_budget(
    settings, monkeypatch
):
    calls = install_drivers(monkeypatch)
    configured = settings.model_copy(update={"graph_storage_timeout_seconds": 30})
    store = graph_extract.graph_store(configured)
    try:
        store.upsert_nodes([EntityNode(name=f"synthetic-entity-{i}") for i in range(257)])
        store.structured_query("MATCH (c:Chunk) RETURN count(c) AS count")
        repository = graph_repository.GraphRepository(configured, "synthetic-owner")
        await repository.rows("RETURN 1 AS value")
        async with repository.transaction():
            pass
    finally:
        store.close()
    assert calls["sync"] and all(query.timeout == 30 for query, _ in calls["sync"])
    writes = [
        options["parameters_"]["data"] for _, options in calls["sync"] if "data" in options["parameters_"]
    ]
    assert [len(batch) for batch in writes] == [128, 128, 1]
    assert [row["id"] for batch in writes for row in batch] == [f"synthetic-entity-{i}" for i in range(257)]
    assert calls["async"][0][0].timeout == settings.graph_query_timeout_seconds == 3
    assert calls["transactions"] == [3]
    assert configured.graph_build_timeout_seconds == settings.graph_build_timeout_seconds == 300
    assert all(config["max_transaction_retry_time"] == 0 for config in calls["factories"])


@pytest.mark.parametrize("value", [0, -1, 61, float("nan"), float("inf"), -float("inf")])
def test_storage_budget_rejects_unbounded_or_nonpositive_values(value):
    with pytest.raises(ValidationError):
        Settings(api_key="synthetic-service-key-" * 2, graph_storage_timeout_seconds=value)


@pytest.mark.parametrize("value", [1, 30, 60])
def test_storage_budget_is_independent_finite_and_serialized_for_worker(value):
    settings = Settings(api_key="synthetic-service-key-" * 2, graph_storage_timeout_seconds=value)
    assert settings.graph_storage_timeout_seconds == value
    assert settings.graph_query_timeout_seconds == 3
    assert settings.model_dump(mode="json")["graph_storage_timeout_seconds"] == value


def test_storage_budget_default_is_bounded_for_new_workers(settings):
    assert settings.graph_storage_timeout_seconds == 30
    assert settings.graph_query_timeout_seconds == 3
