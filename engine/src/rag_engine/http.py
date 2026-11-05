"""有大小上限的上游 JSON 调用，禁用代理环境变量与重定向。"""

import json
from typing import Any

import httpx

from rag_engine.errors import EngineError


async def post_json(
    client: httpx.AsyncClient,
    url: str,
    body: dict,
    headers: dict[str, str],
    maximum: int,
    *,
    timeout: httpx.Timeout | None = None,
) -> Any:
    return await request_json(client, "POST", url, body, headers, maximum, timeout=timeout)


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    body: dict | None,
    headers: dict[str, str],
    maximum: int,
    *,
    timeout: httpx.Timeout | None = None,
) -> Any:
    # 未指定时完全继承 client 默认值；不能传 timeout=None，那会禁用 HTTPX 超时。
    options = {} if timeout is None else {"timeout": timeout}
    async with client.stream(method, url, json=body, headers=headers, **options) as response:
        if not 200 <= response.status_code < 300:
            raise EngineError("UPSTREAM_REJECTED")
        buffer = bytearray()
        async for block in response.aiter_bytes():
            buffer.extend(block)
            if len(buffer) > maximum:
                raise EngineError("UPSTREAM_RESPONSE_TOO_LARGE")
        try:
            return json.loads(buffer)
        except (ValueError, UnicodeDecodeError) as error:
            raise EngineError("UPSTREAM_INVALID_RESPONSE") from error
