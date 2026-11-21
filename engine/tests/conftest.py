"""所有文档和凭据均现场合成，不读取用户或公司数据。"""

import base64
import io
import secrets

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from synthetic_issuer import SyntheticIssuer

from rag_engine.config import Settings

# 整个测试进程共用一个本机回环上的合成 Keycloak；每个应用实例经真实 HTTP 各取一次它的公钥。
ISSUER = SyntheticIssuer().__enter__()
AUTH_SETTINGS = {"keycloak_issuer": ISSUER.issuer, "jwks_url": ISSUER.jwks_url}


@pytest.fixture
def settings():
    return Settings(
        **AUTH_SETTINGS,
        qwen_api_key=secrets.token_urlsafe(32),
        qwen_base_url="https://models.example.test/compatible-mode/v1",
        qdrant_url="https://vectors.example.test",
        embedding_dimension=3,
    )


def auth(settings, tenant="tenant-a"):
    """rag（azp github-rag）调引擎的请求头：合成签发方现签的客户端凭证令牌与当前租户。"""
    assert settings.jwks_url == ISSUER.jwks_url
    return {"Authorization": "Bearer " + ISSUER.token("github-rag"), "X-Tenant-Id": tenant}


def scrape_auth():
    """Prometheus（azp github-prometheus）抓 /metrics 的请求头；/metrics 不要租户头。"""
    return {"Authorization": "Bearer " + ISSUER.token("github-prometheus")}


def pdf_bytes(text="A synthetic knowledge document.", count=1):
    writer = PdfWriter()
    for _ in range(count):
        page = writer.add_blank_page(width=300, height=300)
        if text:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
            )
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 10 200 Td ({text}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def parse_payload(data=None, filename="guide.pdf", limit=6000):
    return {
        "sourceName": filename,
        "title": "Synthetic guide",
        "maxChunkCharacters": limit,
        "contentBase64": base64.b64encode(data if data is not None else pdf_bytes()).decode(),
    }
