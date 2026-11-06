"""合成上游故障验证模型分类、脱敏和与语义纠正共用的请求预算。"""

import asyncio
import json
import ssl

import httpx
import pytest
from llama_index.core.graph_stores.types import KG_NODES_KEY, KG_RELATIONS_KEY
from llama_index.core.schema import TextNode

from rag_engine import errors, graph_extract
from rag_engine.errors import EngineError
from rag_engine.http import post_json

PRIVATE_MARKER = "SYNTHETIC_PRIVATE_UPSTREAM_MARKER"
SOURCE = "A-B方案用于目标乙。"


def wire(subject="A-B方案", object_="目标乙"):
    value = {"triples": [{"subjectQuote": subject, "predicate": "用于", "objectQuote": object_}]}
    return httpx.Response(
        200,
        json={
            "choices": [
                {"finish_reason": "stop", "message": {"content": json.dumps(value, ensure_ascii=False)}}
            ]
        },
    )


def extractor(settings, client):
    return graph_extract.ValidatedSimpleLLMPathExtractor(
        llm=graph_extract.QwenGraphLLM(settings=settings, client=client),
        extract_prompt=graph_extract.EXTRACT_PROMPT,
        parse_fn=graph_extract.parse_triplets,
        max_paths_per_chunk=8,
        num_workers=1,
        raise_on_error=True,
    )


async def test_model_outer_deadline_is_classified_without_exposing_upstream(settings, caplog):
    async def response(_request):
        await asyncio.sleep(1)
        return wire()

    configured = settings.model_copy(update={"graph_llm_timeout_seconds": 0.1})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        llm = graph_extract.QwenGraphLLM(settings=configured, client=client)
        with pytest.raises(Exception) as caught:
            await llm.acomplete(PRIVATE_MARKER)
    assert isinstance(caught.value, errors.GraphModelError)
    assert caught.value.code == "GRAPH_MODEL_TIMEOUT" and caught.value.reason == "MODEL_TIMEOUT"
    assert caught.value.retryable is True
    assert PRIVATE_MARKER not in str(caught.value) + repr(caught.value) + caplog.text


@pytest.mark.parametrize(
    "upstream_status,code,reason,retryable",
    [
        (401, "GRAPH_MODEL_AUTH_FAILED", "MODEL_AUTHENTICATION", False),
        (403, "GRAPH_MODEL_AUTH_FAILED", "MODEL_AUTHENTICATION", False),
        (400, "GRAPH_MODEL_REQUEST_REJECTED", "MODEL_HTTP_REJECTED", False),
        (404, "GRAPH_MODEL_REQUEST_REJECTED", "MODEL_HTTP_REJECTED", False),
        (302, "GRAPH_MODEL_REQUEST_REJECTED", "MODEL_HTTP_REJECTED", False),
        (429, "GRAPH_MODEL_RATE_LIMITED", "MODEL_RATE_LIMIT", True),
        (500, "GRAPH_MODEL_UNAVAILABLE", "MODEL_SERVER_UNAVAILABLE", True),
        (502, "GRAPH_MODEL_UNAVAILABLE", "MODEL_SERVER_UNAVAILABLE", True),
        (503, "GRAPH_MODEL_UNAVAILABLE", "MODEL_SERVER_UNAVAILABLE", True),
        (504, "GRAPH_MODEL_UNAVAILABLE", "MODEL_SERVER_UNAVAILABLE", True),
        (501, "GRAPH_MODEL_REQUEST_REJECTED", "MODEL_HTTP_REJECTED", False),
    ],
)
async def test_http_status_maps_to_fixed_model_policy(settings, upstream_status, code, reason, retryable):
    def response(_request):
        return httpx.Response(upstream_status, text=PRIVATE_MARKER, headers={"x-secret": PRIVATE_MARKER})

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        llm = graph_extract.QwenGraphLLM(settings=settings, client=client)
        with pytest.raises(EngineError) as caught:
            await llm.acomplete("Synthetic input")
    error = caught.value
    assert isinstance(error, errors.GraphModelError)
    assert (error.code, error.reason, error.retryable) == (code, reason, retryable)
    assert error.code in errors.GRAPH_MODEL_CODES and error.reason in errors.GRAPH_MODEL_REASONS
    assert error.reason in errors.VALIDATION_REASONS
    assert PRIVATE_MARKER not in str(error) + repr(error) + repr(vars(error))
    assert not any(k in vars(error) for k in ["request", "response", "url", "body", "headers"])


async def test_shared_http_helper_keeps_public_error_and_only_attaches_integer_status(settings):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(429, text=PRIVATE_MARKER))
    ) as client:
        with pytest.raises(EngineError) as caught:
            await post_json(client, settings.qwen_base_url, {}, {}, 1024)
    assert caught.value.code == "UPSTREAM_REJECTED" and caught.value.status == 502
    assert type(caught.value.upstream_status) is int and caught.value.upstream_status == 429
    assert PRIVATE_MARKER not in repr(vars(caught.value))


@pytest.mark.parametrize(
    "exception_type",
    [
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
        httpx.ConnectError,
        httpx.ReadError,
        httpx.WriteError,
    ],
)
async def test_known_transport_errors_are_classified_and_safe(settings, exception_type):
    def response(request):
        raise exception_type(PRIVATE_MARKER, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(EngineError) as caught:
            await graph_extract.QwenGraphLLM(settings=settings, client=client).acomplete("Synthetic")
    assert isinstance(caught.value, errors.GraphModelError) and caught.value.retryable is True
    expected = (
        "GRAPH_MODEL_TIMEOUT" if issubclass(exception_type, httpx.TimeoutException) else "GRAPH_MODEL_NETWORK"
    )
    assert caught.value.code == expected and PRIVATE_MARKER not in repr(vars(caught.value))


@pytest.mark.parametrize(
    "exception_type",
    [httpx.InvalidURL, httpx.UnsupportedProtocol, httpx.LocalProtocolError, httpx.RemoteProtocolError],
)
async def test_url_or_local_protocol_errors_are_permanent(settings, exception_type):
    calls = []

    def response(_request):
        calls.append(1)
        raise exception_type(PRIVATE_MARKER)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(EngineError) as caught:
            await extractor(settings, client).acall([TextNode(id_="bad-config", text=SOURCE)])
    assert len(calls) == 1 and isinstance(caught.value, errors.GraphModelError)
    assert caught.value.reason == "MODEL_CONFIGURATION" and caught.value.retryable is False
    assert PRIVATE_MARKER not in str(caught.value) + repr(vars(caught.value))


async def test_certificate_validation_error_is_permanent_even_under_connect_error(settings):
    calls = []

    def response(request):
        calls.append(1)
        try:
            raise ssl.SSLCertVerificationError(1, PRIVATE_MARKER)
        except ssl.SSLCertVerificationError as cause:
            raise httpx.ConnectError(PRIVATE_MARKER, request=request) from cause

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(EngineError) as caught:
            await extractor(settings, client).acall([TextNode(id_="tls", text=SOURCE)])
    assert len(calls) == 1 and caught.value.reason == "MODEL_TLS" and caught.value.retryable is False
    assert PRIVATE_MARKER not in str(caught.value) + repr(vars(caught.value))


@pytest.mark.parametrize("status", [302, 307, 401, 403, 400, 404, 501])
async def test_permanent_http_failure_is_not_retried(settings, status):
    calls = []

    def response(_request):
        calls.append(1)
        return httpx.Response(status, text=PRIVATE_MARKER)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(EngineError):
            await extractor(settings, client).acall([TextNode(id_="permanent-http", text=SOURCE)])
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["malformed", "oversize"])
async def test_invalid_or_oversize_upstream_body_is_permanent_and_safe(settings, kind):
    calls = []

    def response(_request):
        calls.append(1)
        return httpx.Response(200, text=PRIVATE_MARKER * (10000 if kind == "oversize" else 1))

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(EngineError) as caught:
            await extractor(settings, client).acall([TextNode(id_="bad-body", text=SOURCE)])
    assert len(calls) == 1 and isinstance(caught.value, errors.GraphModelError)
    expected = "GRAPH_MODEL_RESPONSE_TOO_LARGE" if kind == "oversize" else "GRAPH_MODEL_INVALID_RESPONSE"
    assert caught.value.code == expected and caught.value.retryable is False
    assert PRIVATE_MARKER not in str(caught.value) + repr(vars(caught.value))


async def test_retryable_model_failure_reuses_full_request_without_semantic_feedback(settings):
    requests = []

    def response(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            raise httpx.ReadTimeout(PRIVATE_MARKER, request=request)
        return wire()

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        result = await extractor(settings, client).acall([TextNode(id_="timeout-then-success", text=SOURCE)])
    assert len(requests) == 2 and requests[0] == requests[1]
    assert "Correction required" not in requests[1]["messages"][0]["content"]
    assert len(result[0].metadata[KG_RELATIONS_KEY]) == 1


async def test_slot_request_timeout_retries_same_plan_not_a_full_candidate(settings):
    requests = []

    def response(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return wire(subject="AB方案")
        if len(requests) == 2:
            raise httpx.ReadTimeout(PRIVATE_MARKER, request=request)
        plan_key = body["response_format"]["json_schema"]["schema"]["properties"]["repairKey"]["const"]
        value = {"repairKey": plan_key, "replacements": {"s0": "A-B方案"}}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(value, ensure_ascii=False)}}
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        result = await extractor(settings, client).acall([TextNode(id_="slot-timeout", text=SOURCE)])
    assert len(requests) == 3 and requests[1] == requests[2]
    assert requests[2]["response_format"]["json_schema"]["name"] == "graph_quote_slot_repair"
    assert len(result[0].metadata[KG_RELATIONS_KEY]) == 1


async def test_network_and_semantic_correction_share_three_request_limit(settings):
    requests = []
    source = "工具甲覆盖1~7类，1~2类对应状态乙。"

    def response(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(503, text=PRIVATE_MARKER)
        if len(requests) == 2:
            return wire(subject="工具甲1~2类", object_="状态乙")
        return wire(subject="1~2类", object_="状态乙")

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        result = await extractor(settings, client).acall([TextNode(id_="mixed-budget", text=source)])
    assert len(requests) == 3 and requests[0] == requests[1]
    assert "ENTITY_NOT_IN_SOURCE" in requests[2]["messages"][0]["content"]
    assert "MODEL_SERVER_UNAVAILABLE" not in requests[2]["messages"][0]["content"]
    assert len(result[0].metadata[KG_RELATIONS_KEY]) == 1


async def test_three_model_timeouts_preserve_fixed_last_error_without_fourth_call(settings):
    calls = []

    def response(request):
        calls.append(1)
        raise httpx.ReadTimeout(PRIVATE_MARKER, request=request)

    node = TextNode(id_="timeout-budget", text=SOURCE)
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(EngineError) as caught:
            await extractor(settings, client).acall([node])
    assert len(calls) == 3 and caught.value.code == "GRAPH_MODEL_TIMEOUT"
    assert caught.value.reason == "MODEL_TIMEOUT"
    assert KG_NODES_KEY not in node.metadata and KG_RELATIONS_KEY not in node.metadata


async def test_external_deadline_cancels_model_and_never_starts_another_request(settings):
    calls = []
    active = []

    async def response(_request):
        calls.append(1)
        active.append(True)
        try:
            await asyncio.sleep(1)
            return wire()
        finally:
            active.pop()

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.02):
                await extractor(settings, client).acall([TextNode(id_="cancel-model", text=SOURCE)])
    assert len(calls) == 1 and not active


async def test_cancellation_during_backoff_does_not_start_retry(settings):
    calls = []
    first = asyncio.Event()

    async def response(_request):
        calls.append(1)
        first.set()
        return httpx.Response(429, text=PRIVATE_MARKER)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        task = asyncio.create_task(
            extractor(settings, client).acall([TextNode(id_="cancel-backoff", text=SOURCE)])
        )
        await first.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(calls) == 1


async def test_unexpected_model_call_implementation_error_is_not_retried(settings):
    calls = []

    def response(_request):
        calls.append(1)
        raise RuntimeError(PRIVATE_MARKER)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(RuntimeError):
            await extractor(settings, client).acall([TextNode(id_="source-error", text=SOURCE)])
    assert len(calls) == 1
