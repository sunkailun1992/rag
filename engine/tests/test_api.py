import asyncio
import io
import json

import httpx
import pytest
from conftest import auth, parse_payload, pdf_bytes
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from rag_engine.app import create_app
from rag_engine.config import Settings
from rag_engine.errors import EngineError
from rag_engine.models import ParseRequest
from rag_engine.parsing import ocr_blocks, split_blocks


def test_missing_key_refuses_start(monkeypatch):
    monkeypatch.delenv("RAG_ENGINE_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("headers,status", [({}, 401), ({"Authorization": "Bearer wrong"}, 401)])
def test_unauthorized_rejected_before_parsing(settings, headers, status):
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/parses", content=b"not JSON", headers=headers)
    assert response.status_code == status
    assert "not JSON" not in response.text


def test_tenant_is_required_and_duplicate_header_rejected(settings):
    with TestClient(create_app(settings)) as client:
        headers = auth(settings)
        headers.pop("X-Tenant-Id")
        assert client.post("/v1/parses", json={}, headers=headers).status_code == 400
        doubled = [
            ("Authorization", auth(settings)["Authorization"]),
            ("X-Tenant-Id", "tenant-a"),
            ("X-Tenant-Id", "tenant-b"),
        ]
        assert client.post("/v1/parses", json={}, headers=doubled).status_code == 400


def test_text_pdf_roundtrip_retains_page_source_without_ocr(settings):
    def unexpected(_request):
        raise AssertionError("text PDF must not call models or OCR")

    with TestClient(create_app(settings, httpx.MockTransport(unexpected))) as client:
        response = client.post(
            "/v1/parses", json=parse_payload(count_data := pdf_bytes(count=2)), headers=auth(settings)
        )
    assert count_data
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["sourceType"] == "pdf"
    assert {chunk["metadata"]["page"] for chunk in document["chunks"]} == {1, 2}
    assert all(chunk["metadata"]["source_name"] == "guide.pdf" for chunk in document["chunks"])
    assert "synthetic knowledge" in document["chunks"][0]["content"]
    assert document["chunks"][0]["metadata"]["bbox"] is None


@pytest.mark.parametrize(
    "change,code,status",
    [
        ({"sourceName": "../private.pdf"}, "INVALID_REQUEST", 422),
        ({"sourceName": "unsafe.svg"}, "UNSUPPORTED_FORMAT", 415),
        ({"contentBase64": "%%%%"}, "INVALID_BASE64", 422),
        ({"contentBase64": "bm90IHBkZg=="}, "INVALID_FILE", 422),
        ({"sourceName": "secret.pdf", "tenantId": "other"}, "INVALID_REQUEST", 422),
    ],
)
def test_parse_rejects_invalid_requests_without_echo(settings, change, code, status):
    payload = parse_payload()
    payload.update(change)
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/parses", json=payload, headers=auth(settings))
    assert response.status_code == status
    assert response.json() == {"error": {"code": code}}


def test_blank_pdf_requires_explicit_ocr_configuration(settings):
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/parses", json=parse_payload(pdf_bytes(text="")), headers=auth(settings))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "OCR_NOT_CONFIGURED"


def test_pdf_page_limit_rejects_whole_document(settings):
    settings.max_pages = 1
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/parses", json=parse_payload(pdf_bytes(count=2)), headers=auth(settings))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PAGE_LIMIT_EXCEEDED"


def test_stream_body_limit_is_enforced_without_content_length(settings):
    settings.max_file_bytes = 1024
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/parses",
            content=iter([b" " * 3000, b" " * 3000]),
            headers={**auth(settings), "Content-Type": "application/json"},
        )
    assert response.status_code == 413


def test_ocr_image_roundtrip_preserves_bbox(settings):
    settings.ocr_url = "https://ocr.example.test"
    output = io.BytesIO()
    Image.new("RGB", (20, 20)).save(output, format="PNG")

    def upstream(request):
        body = json.loads(request.content)
        assert request.url.path == "/layout-parsing"
        assert body["fileType"] == 1
        return httpx.Response(
            200,
            json={
                "result": {
                    "layoutParsingResults": [
                        {
                            "prunedResult": {
                                "page_index": None,
                                "parsing_res_list": [
                                    {
                                        "block_label": "table",
                                        "block_content": "容量：2GB",
                                        "block_bbox": [1, 2, 10, 12],
                                    }
                                ],
                            }
                        }
                    ]
                }
            },
        )

    with TestClient(create_app(settings, httpx.MockTransport(upstream))) as client:
        response = client.post(
            "/v1/parses", json=parse_payload(output.getvalue(), "scan.png"), headers=auth(settings)
        )
    assert response.status_code == 200, response.text
    chunk = response.json()["chunks"][0]
    assert chunk["metadata"]["bbox"] == [1, 2, 10, 12]
    assert chunk["chunkType"] == "table"
    assert chunk["content"] == "容量：2GB"


def test_ocr_missing_page_is_not_success():
    with pytest.raises(EngineError, match="OCR_INVALID_RESPONSE"):
        ocr_blocks({"result": {"layoutParsingResults": []}}, 2)


def test_llamaindex_split_keeps_all_content_and_overlaps(settings):
    text = "这是一个可重复的公开测试段落。" * 80
    chunks = split_blocks(
        [{"text": text, "page": 1, "type": "text", "index": 1}],
        ParseRequest(**parse_payload(limit=256)),
        settings,
    )
    assert len(chunks) > 1
    assert all(len(chunk.content) <= 256 for chunk in chunks)
    assert sum(len(chunk.content) for chunk in chunks) > len(text)


def hit(tenant="tenant-a", index=1):
    return {
        "id": str(index),
        "score": 0.8,
        "payload": {
            "tenant_id": tenant,
            "knowledge_base": "general",
            "document_status": "ACTIVE",
            "embedding_model": "text-embedding-v4",
            "embedding_dimension": 3,
            "content": "Synthetic knowledge",
            "content_sha256": str(index),
            "document_id": "document-a",
            "chunk_id": "chunk-a",
            "source_name": "guide.pdf",
            "source_locator": "page 1",
            "document_title": "Guide",
        },
    }


def retrieval_body():
    return {
        "query": "synthetic question",
        "knowledgeBase": "general",
        "topK": 1,
        "candidateCount": 10,
        "similarityThreshold": 0.2,
        "maxContextCharacters": 12000,
    }


def test_retrieval_keeps_filter_and_uses_compatible_rerank_url(settings):
    calls = []

    def upstream(request):
        body = json.loads(request.content)
        calls.append(request.url.path)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})
        if request.url.path.endswith("/search"):
            assert body["filter"]["must"] == [
                {"key": "tenant_id", "match": {"value": "tenant-a"}},
                {"key": "knowledge_base", "match": {"value": "general"}},
                {"key": "document_status", "match": {"value": "ACTIVE"}},
            ]
            return httpx.Response(200, json={"result": [hit()]})
        assert request.url.path == "/compatible-api/v1/reranks"
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]})

    with TestClient(create_app(settings, httpx.MockTransport(upstream))) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "dense"
    assert response.json()["hits"][0]["score"] == 0.9
    assert response.json()["degraded"] is False
    assert len(calls) == 3


@pytest.mark.parametrize("bad", ["tenant", "embedding", "status"])
def test_retrieval_rejects_upstream_boundary_violation(settings, bad):
    def upstream(request):
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200, json={"data": [{"embedding": [0.1] if bad == "embedding" else [1, 2, 3]}]}
            )
        value = hit(tenant="other" if bad == "tenant" else "tenant-a")
        if bad == "status":
            value["payload"]["document_status"] = "DELETED"
        return httpx.Response(200, json={"result": [value]})

    with TestClient(create_app(settings, httpx.MockTransport(upstream))) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
    assert response.status_code == 502
    assert "Synthetic" not in response.text


def test_rerank_failure_falls_back_to_dense(settings):
    def upstream(request):
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [1, 2, 3]}]})
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"result": [hit()]})
        return httpx.Response(503, text="secret upstream detail")

    with TestClient(create_app(settings, httpx.MockTransport(upstream))) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
    assert response.status_code == 200
    assert response.json()["degraded"] is True
    assert response.json()["hits"][0]["score"] == 0.8


def test_total_retrieval_deadline(settings):
    settings.retrieval_timeout_seconds = 0.1

    async def upstream(_request):
        await asyncio.sleep(0.3)
        return httpx.Response(200, json={})

    with TestClient(create_app(settings, httpx.MockTransport(upstream))) as client:
        response = client.post("/v1/retrievals", json=retrieval_body(), headers=auth(settings))
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "RETRIEVAL_TIMEOUT"


def test_public_health_does_not_expose_configuration(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json() == {"status": "up"}
        assert client.get("/v1/openapi.json").status_code == 401
        assert client.get("/v1/openapi.json", headers=auth(settings)).status_code == 200


def test_encrypted_pdf_is_rejected(settings):
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes())))
    writer.encrypt("synthetic-password")
    output = io.BytesIO()
    writer.write(output)
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/parses", json=parse_payload(output.getvalue()), headers=auth(settings))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ENCRYPTED_PDF"


def test_splitter_does_not_download_models(settings, monkeypatch):
    from llama_index.core.utils import globals_helper

    def forbidden():
        raise AssertionError("implicit model download")

    monkeypatch.setattr(globals_helper, "wait_for_nltk_check", forbidden)
    chunks = split_blocks(
        [{"text": "不下载模型也能切片。", "page": 1, "type": "text", "index": 1}],
        ParseRequest(**parse_payload()),
        settings,
    )
    assert chunks[0].content == "不下载模型也能切片。"


@pytest.mark.asyncio
async def test_cancelled_parser_kills_child(settings, monkeypatch):
    from rag_engine import parsing

    class Process:
        returncode = None
        killed = False
        waited = False

        async def communicate(self, _input):
            raise asyncio.CancelledError()

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.waited = True

    process = Process()

    async def spawn(*_args, **_kwargs):
        return process

    monkeypatch.setattr(parsing.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(asyncio.CancelledError):
        await parsing.inspect_in_worker(ParseRequest(**parse_payload()), settings)
    assert process.killed and process.waited


@pytest.mark.parametrize("box", [[1, 2, float("nan"), 4], [True, 2, 3, 4], [-1, 0, 3, 4], [3, 2, 1, 4]])
def test_ocr_rejects_invalid_coordinates(box):
    payload = {
        "result": {
            "layoutParsingResults": [
                {
                    "prunedResult": {
                        "page_index": 0,
                        "parsing_res_list": [
                            {"block_label": "text", "block_content": "safe", "block_bbox": box}
                        ],
                    }
                }
            ]
        }
    }
    with pytest.raises(EngineError, match="OCR_INVALID_RESPONSE"):
        ocr_blocks(payload, 1)


def test_configuration_validation_never_echoes_secret():
    sample = "private-config-value"
    with pytest.raises(ValidationError) as caught:
        Settings(api_key=sample)
    assert sample not in str(caught.value)
    with pytest.raises(ValidationError) as caught:
        Settings(api_key="x" * 32, qdrant_url="https://user:private-password@example.test")
    assert "private-password" not in str(caught.value)


def test_openapi_file_matches_runtime_and_declares_security(settings):
    from pathlib import Path

    stored = json.loads(Path(__file__).resolve().parents[1].joinpath("openapi.json").read_text())
    actual = create_app(settings).openapi()
    assert stored == actual
    operation = actual["paths"]["/v1/parses"]["post"]
    assert operation["security"] == [{"ServiceBearer": []}]
    assert operation["parameters"][0]["name"] == "X-Tenant-Id"
    assert operation["parameters"][0]["required"] is True
    assert operation["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "EngineError"
    )


def test_ocr_blank_page_is_explicit_failure():
    payload = {
        "result": {"layoutParsingResults": [{"prunedResult": {"page_index": 0, "parsing_res_list": []}}]}
    }
    with pytest.raises(EngineError, match="OCR_PAGE_HAS_NO_TEXT"):
        ocr_blocks(payload, 1)


def test_pdf_raster_image_with_text_still_needs_ocr():
    import base64

    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    from rag_engine.worker import inspect_document

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes(text="Only the header is searchable."))))
    picture = DecodedStreamObject()
    picture.set_data(b"\x00\x00\x00")
    picture.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(1),
            NameObject("/Height"): NumberObject(1),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    writer.pages[0]["/Resources"][NameObject("/XObject")] = DictionaryObject(
        {NameObject("/Img"): writer._add_object(picture)}
    )
    output = io.BytesIO()
    writer.write(output)
    inspected = inspect_document(
        {
            "sourceName": "mixed.pdf",
            "contentBase64": base64.b64encode(output.getvalue()).decode(),
            "maxPages": 100,
            "maxTextCharacters": 1000,
        }
    )
    assert inspected["needsOcr"] is True
