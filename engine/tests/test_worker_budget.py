"""真实子进程验证：慢 split 可以用完整解析预算，总 deadline 仍杀死并回收 worker。"""

import asyncio
import logging
import os
import signal
import sys
import time

import httpx
import pytest
from conftest import auth, parse_payload

from rag_engine import parsing
from rag_engine.app import create_app

# 模拟一次冷 split：完整等待 26 秒后执行真实 worker，不能靠缩放旧 25 秒常量证明修复。
DELAYED_WORKER = """
import io, json, runpy, sys, time
payload = json.load(sys.stdin)
if payload.get('operation') == 'split':
    time.sleep(26)
sys.stdin = io.StringIO(json.dumps(payload))
runpy.run_module('rag_engine.worker', run_name='__main__')
"""


def delayed_real_workers(monkeypatch):
    actual_spawn = asyncio.create_subprocess_exec
    processes = []

    async def spawn(*_args, **kwargs):
        process = await actual_spawn(sys.executable, "-c", DELAYED_WORKER, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(parsing.asyncio, "create_subprocess_exec", spawn)
    return processes


@pytest.mark.asyncio
async def test_split_exceeding_old_25_seconds_finishes_within_api_total_budget(settings, monkeypatch):
    assert settings.parse_timeout_seconds == 55
    processes = delayed_real_workers(monkeypatch)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://engine") as api:
            started = time.monotonic()
            response = await api.post("/v1/parses", json=parse_payload(), headers=auth(settings))
            elapsed = time.monotonic() - started
    assert response.status_code == 200, response.text
    assert response.json()["chunks"][0]["content"] == "A synthetic knowledge document."
    assert 26 <= elapsed < settings.parse_timeout_seconds
    assert len(processes) == 2 and all(process.returncode == 0 for process in processes)


@pytest.mark.asyncio
async def test_api_total_deadline_kills_and_reaps_real_split_worker(settings, monkeypatch, caplog):
    settings.parse_timeout_seconds = 1
    processes = delayed_real_workers(monkeypatch)

    async def inspected(_request, _settings):
        return {"needsOcr": False, "pages": [{"page": 1, "text": "A synthetic knowledge document."}]}

    # 聚焦外层总 deadline 的取消传播，文件检查另由完整 26 秒回归执行。
    monkeypatch.setattr(parsing, "inspect_in_worker", inspected)
    secret_filename = "private-diagnostic-filename.pdf"
    caplog.set_level(logging.WARNING, logger="rag_engine")
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://engine") as api:
            response = await api.post(
                "/v1/parses", json=parse_payload(filename=secret_filename), headers=auth(settings)
            )
    assert response.status_code == 504
    assert response.json() == {"error": {"code": "PARSER_TIMEOUT"}}
    assert len(processes) == 1
    assert processes[0].returncode == -signal.SIGKILL
    with pytest.raises(ProcessLookupError):
        os.kill(processes[0].pid, 0)
    assert "phase=split" in caplog.text and "reason=cancelled" in caplog.text
    assert secret_filename not in caplog.text
    assert "A synthetic knowledge document." not in caplog.text
    assert settings.api_key.get_secret_value() not in caplog.text


@pytest.mark.asyncio
async def test_worker_nonzero_exit_logs_only_controlled_phase_and_exit(settings, monkeypatch, caplog):
    actual_spawn = asyncio.create_subprocess_exec
    secret = "private-child-stderr-not-for-logs"
    processes = []

    async def spawn(*_args, **kwargs):
        process = await actual_spawn(
            sys.executable, "-c", f"import sys; sys.stderr.write('{secret}'); sys.exit(7)", **kwargs
        )
        processes.append(process)
        return process

    monkeypatch.setattr(parsing.asyncio, "create_subprocess_exec", spawn)
    caplog.set_level(logging.WARNING, logger="rag_engine")
    from rag_engine.errors import EngineError

    with pytest.raises(EngineError, match="PARSER_RESOURCE_LIMIT"):
        await parsing.run_worker({"operation": "untrusted-phase"}, settings)
    assert processes[0].returncode == 7
    assert "phase=inspect" in caplog.text and "reason=exit" in caplog.text and "exit_code=7" in caplog.text
    assert "elapsed_ms=" in caplog.text
    assert "untrusted-phase" not in caplog.text and secret not in caplog.text


@pytest.mark.asyncio
async def test_direct_worker_timeout_logs_phase_and_reaps_real_process(settings, monkeypatch, caplog):
    settings.parse_timeout_seconds = 1
    processes = delayed_real_workers(monkeypatch)
    caplog.set_level(logging.WARNING, logger="rag_engine")
    with pytest.raises(TimeoutError):
        await parsing.run_worker({"operation": "split"}, settings)
    assert len(processes) == 1 and processes[0].returncode == -signal.SIGKILL
    with pytest.raises(ProcessLookupError):
        os.kill(processes[0].pid, 0)
    assert "phase=split" in caplog.text and "reason=timeout" in caplog.text
    assert "elapsed_ms=" in caplog.text
