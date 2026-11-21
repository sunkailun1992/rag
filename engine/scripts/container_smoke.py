"""Linux 硬限额容器实测：真实令牌校验、真实 PDF 子进程及 LlamaIndex 切片。

容器启动时 RAG_ENGINE_JWKS_URL 指向本脚本在容器内起的合成签发方（固定端口 18081），
RAG_ENGINE_KEYCLOAK_ISSUER 等于它的签发方地址；引擎经回环 HTTP 取公钥、用镜像里的 PyJWT 验 RS256 签名。
"""

import base64
import io
import json
import time
import urllib.error
import urllib.request

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from synthetic_issuer import SyntheticIssuer

JWKS_PORT = 18081

writer = PdfWriter()
page = writer.add_blank_page(width=300, height=300)
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
stream.set_data(b"BT /F1 12 Tf 30 250 Td (Container parser acceptance text) Tj ET")
page[NameObject("/Contents")] = writer._add_object(stream)
output = io.BytesIO()
writer.write(output)
for _attempt in range(100):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8091/health", timeout=1) as response:
            assert response.status == 200
        break
    except OSError:
        time.sleep(0.1)
else:
    raise RuntimeError("container health unavailable")
body = json.dumps(
    {
        "sourceName": "acceptance.pdf",
        "title": "Container acceptance",
        "contentBase64": base64.b64encode(output.getvalue()).decode(),
        "maxChunkCharacters": 1000,
    }
).encode()


def parse(headers):
    request = urllib.request.Request(
        "http://127.0.0.1:8091/v1/parses",
        data=body,
        headers={"Content-Type": "application/json", "X-Tenant-Id": "container-acceptance", **headers},
    )
    return urllib.request.urlopen(request, timeout=60)


with SyntheticIssuer(port=JWKS_PORT) as issuer:
    for denied, status in [
        ({}, 401),
        ({"Authorization": "Bearer " + issuer.token("github-prometheus")}, 403),
    ]:
        try:
            parse(denied).close()
            raise AssertionError("request without a github-rag token reached the parser")
        except urllib.error.HTTPError as error:
            error.close()
            assert error.code == status, error.code
    with parse({"Authorization": "Bearer " + issuer.token("github-rag")}) as response:
        payload = json.load(response)
        assert response.status == 200
        assert "Container parser acceptance text" in payload["chunks"][0]["content"]
print("Linux container: RS256 token check, real PDF and LlamaIndex split passed")
