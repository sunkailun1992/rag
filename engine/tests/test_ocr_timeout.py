"""真实本地 HTTP 延迟回归：OCR 可以等待推理，解析总预算仍负责取消整次请求。"""

import asyncio
import io
import json
import time
from contextlib import asynccontextmanager

import httpx
import pytest
from conftest import auth, parse_payload
from PIL import Image

from rag_engine.app import create_app
from rag_engine.http import post_json
from rag_engine.models import ParseRequest
from rag_engine.parsing import parse_document


@asynccontextmanager
async def delayed_ocr_server(delay):
    received = asyncio.Event()
    tasks = set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            await reader.readexactly(length)
            received.set()
            await asyncio.sleep(delay)
            payload = json.dumps(
                {
                    "result": {
                        "layoutParsingResults": [
                            {
                                "prunedResult": {
                                    "page_index": 0,
                                    "parsing_res_list": [
                                        {"block_content": "Synthetic OCR content", "block_label": "ocr-text"}
                                    ],
                                }
                            }
                        ]
                    }
                }
            ).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", received
    finally:
        server.close()
        await server.wait_closed()
        pending = list(tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


def image_request():
    output = io.BytesIO()
    Image.new("RGB", (20, 20), color="white").save(output, format="PNG")
    return parse_payload(output.getvalue(), filename="scan.png")


@pytest.mark.asyncio
async def test_ocr_read_timeout_overrides_short_shared_client_on_real_http(settings):
    async with delayed_ocr_server(0.2) as (url, received):
        settings.ocr_url = url
        async with httpx.AsyncClient(timeout=0.03, trust_env=False) as shared_client:
            parsed = await parse_document(ParseRequest(**image_request()), settings, shared_client)
        assert received.is_set()
        assert parsed.chunks[0].content == "Synthetic OCR content"
        assert parsed.chunks[0].metadata["block_type"] == "ocr-text"


@pytest.mark.asyncio
async def test_other_json_requests_still_inherit_short_client_timeout():
    async with delayed_ocr_server(0.2) as (url, received):
        async with httpx.AsyncClient(timeout=0.03, trust_env=False) as shared_client:
            with pytest.raises(httpx.ReadTimeout):
                await post_json(shared_client, url + "/layout-parsing", {}, {}, 4096)
        assert received.is_set()


@pytest.mark.asyncio
async def test_ocr_override_does_not_extend_api_parse_deadline(settings, monkeypatch):
    from rag_engine import parsing

    async def inspected(_request, _settings):
        return {"needsOcr": True, "pages": [{"page": 1}]}

    # 文件检查与切片另有真实用例；此处隔离网络等待与 API 总预算，避免机器启动速度影响断言。
    monkeypatch.setattr(parsing, "inspect_in_worker", inspected)
    settings.parse_timeout_seconds = 1
    assert settings.retrieval_timeout_seconds == 3
    async with delayed_ocr_server(2.5) as (url, received):
        settings.ocr_url = url
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            app.state.client.timeout = httpx.Timeout(0.03)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://engine") as api:
                started = time.monotonic()
                response = await api.post("/v1/parses", json=image_request(), headers=auth(settings))
                elapsed = time.monotonic() - started
        assert received.is_set()
        assert response.status_code == 504
        assert response.json() == {"error": {"code": "PARSER_TIMEOUT"}}
        assert elapsed < 2
