"""独立审查反例：验证最终字节日志与真实预算/任务取消，全部仅合成数据。"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import pytest
from conftest import auth, parse_payload
from test_api import retrieval_body
from test_observability import memory_observability

from rag_engine.app import create_app


def test_production_uvicorn_stderr_is_jsonl_and_stdout_stays_empty(settings):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    address = listener.getsockname()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("RAG_ENGINE_")}
    environment.update(
        RAG_ENGINE_API_KEY=settings.api_key.get_secret_value(),
        RAG_ENGINE_OBSERVABILITY_ENVIRONMENT="review-test",
    )
    # 最终字节回归无需未消费PIPE，小容量pipe会阻塞应用而不是仅捕获日志。
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "rag_engine.app:create_app",
                "--factory",
                "--fd",
                str(listener.fileno()),
                "--workers",
                "1",
                "--limit-concurrency",
                "16",
                "--no-access-log",
                "--no-use-colors",
            ],
            pass_fds=(listener.fileno(),),
            env=environment,
            stdout=stdout_file,
            stderr=stderr_file,
        )
        listener.close()
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{address[1]}", trust_env=False, timeout=2
            ) as client:
                deadline = time.monotonic() + 8
                while True:
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    assert process.poll() is None and time.monotonic() < deadline
                    time.sleep(0.02)
                response = client.post(
                    "/v1/parses", headers=auth(settings), json=parse_payload(b"invalid PDF")
                )
                assert response.status_code == 422
            with socket.create_connection(address, timeout=2) as connection:
                connection.sendall(b"not-http\r\n\r\n")
                assert b"400" in connection.recv(4096)
        finally:
            process.terminate()
            process.wait(timeout=8)
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout, stderr = stdout_file.read(), stderr_file.read()
        assert stdout == b""
        events = [json.loads(line) for line in stderr.decode().splitlines()]
        assert {event["event"] for event in events} >= {
            "http.server.exchange",
            "rag_operation_finished",
            "rag_diagnostic",
            "rag_dependency_diagnostic",
        }
        assert all(
            event["service"] == {"name": "rag-engine", "version": "0.2.5", "environment": "review-test"}
            for event in events
        )


def test_duplicate_diagnostic_keys_cannot_survive_final_stderr():
    script = """
import logging
from rag_engine.config import Settings
from rag_engine.observability import Observability
obs = Observability(Settings(api_key="x"*32))
logging.getLogger("rag_engine").warning("rag_graph_worker_diagnostic data=%s",
    '{"stage":"synthetic-secret","stage":"INPUT","exception":"OTHER","frames":[]}')
logging.getLogger("rag_engine").warning("rag_graph_worker_diagnostic data=%s",
    '{"stage":"INPUT","exception":"OTHER","frames":[],"extra":"synthetic-secret"}')
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, check=True, timeout=8)
    assert result.stdout == b""
    assert b"synthetic-secret" not in result.stderr
    assert len([json.loads(line) for line in result.stderr.splitlines()]) == 2


@pytest.mark.parametrize("route,phase", [("/v1/parses", "parse"), ("/v1/retrievals", "retrieval")])
@pytest.mark.parametrize("outcome", ["timeout", "cancelled"])
async def test_top_level_phase_distinguishes_actual_budget_from_external_cancel(
    settings, monkeypatch, route, phase, outcome
):
    from prometheus_client import generate_latest

    from rag_engine import app as app_module

    entered = asyncio.Event()

    async def slow(*_args, **_kwargs):
        entered.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(app_module, "parse_document" if phase == "parse" else "retrieve", slow)
    obs, exporter = memory_observability(settings)
    settings.parse_timeout_seconds = 1
    settings.retrieval_timeout_seconds = 0.1
    app = create_app(settings, observability=obs)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            task = asyncio.create_task(
                client.post(
                    route,
                    headers=auth(settings),
                    json=parse_payload() if phase == "parse" else retrieval_body(),
                )
            )
            await asyncio.wait_for(entered.wait(), 2)
            if outcome == "cancelled":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                response = await task
                assert response.status_code == 504
    metrics = generate_latest(obs.registry).decode()
    assert f'rag_engine_stage_duration_seconds_count{{result="{outcome}",stage="{phase}"}} 1.0' in metrics
    assert f'result="{outcome}",route="{route}"' in metrics
    phase_spans = [span for span in exporter.get_finished_spans() if span.name == "rag." + phase]
    assert len(phase_spans) == 1 and phase_spans[0].attributes["rag.result"] == outcome


@pytest.mark.parametrize("outcome", ["timeout", "cancelled"])
async def test_parent_worker_phase_distinguishes_budget_from_external_cancel(settings, monkeypatch, outcome):
    from prometheus_client import generate_latest

    from rag_engine import graph_jobs

    entered = asyncio.Event()

    class Repository:
        claimed = False

        async def claim(self):
            if self.claimed:
                raise asyncio.CancelledError()
            self.claimed = True
            return {
                "job": {"buildId": "synthetic", "tenant": "synthetic", "requestJson": "{}"},
                "remaining": 0.05,
            }

        async def finish_failed(self, *_args, **_kwargs):
            pass

        async def cleanup(self, *_args):
            pass

    async def worker(*_args):
        entered.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(graph_jobs, "run_worker", worker)
    obs, exporter = memory_observability(settings)
    manager = graph_jobs.GraphManager(settings, None, Repository(), observability=obs)
    task = asyncio.create_task(manager.pump())
    await asyncio.wait_for(entered.wait(), 2)
    if outcome == "cancelled":
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    obs.shutdown()
    metrics = generate_latest(obs.registry).decode()
    assert f'result="{outcome}",stage="worker_run"' in metrics
    spans = [span for span in exporter.get_finished_spans() if span.name == "rag.worker_run"]
    assert len(spans) == 1 and spans[0].attributes["rag.result"] == outcome
