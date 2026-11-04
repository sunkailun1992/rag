"""所有文档和凭据均现场合成，不读取用户或公司数据。"""

import base64
import io
import secrets

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from rag_engine.config import Settings


@pytest.fixture
def settings():
    return Settings(
        api_key=secrets.token_urlsafe(32),
        qwen_api_key=secrets.token_urlsafe(32),
        qwen_base_url="https://models.example.test/compatible-mode/v1",
        qdrant_url="https://vectors.example.test",
        embedding_dimension=3,
    )


def auth(settings, tenant="tenant-a"):
    return {"Authorization": "Bearer " + settings.api_key.get_secret_value(), "X-Tenant-Id": tenant}


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
