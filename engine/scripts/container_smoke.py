"""Linux 硬限额容器实测：真实 PDF 子进程及 LlamaIndex 切片。"""

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

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
request = urllib.request.Request(
    "http://127.0.0.1:8091/v1/parses",
    data=json.dumps(
        {
            "sourceName": "acceptance.pdf",
            "title": "Container acceptance",
            "contentBase64": base64.b64encode(output.getvalue()).decode(),
            "maxChunkCharacters": 1000,
        }
    ).encode(),
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + os.environ["RAG_ENGINE_API_KEY"],
        "X-Tenant-Id": "container-acceptance",
    },
)
with urllib.request.urlopen(request, timeout=60) as response:
    payload = json.load(response)
    assert response.status == 200
    assert "Container parser acceptance text" in payload["chunks"][0]["content"]
print("Linux container: real PDF and LlamaIndex split passed")
