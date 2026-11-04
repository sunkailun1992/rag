"""不可信 PDF/图片在一次性子进程检查；超时由父进程终止，不留后台解析线程。"""

import base64
import io
import json
import sys


def inspect_document(request: dict) -> dict:
    from PIL import Image
    from pypdf import PdfReader

    data = base64.b64decode(request["contentBase64"], validate=True)
    extension = request["sourceName"].rsplit(".", 1)[-1].lower()
    if extension == "pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("INVALID_FILE")
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("ENCRYPTED_PDF")
        count = len(reader.pages)
        if not 1 <= count <= request["maxPages"]:
            raise ValueError("PAGE_LIMIT_EXCEEDED")
        pages = []
        total = 0
        for index, page in enumerate(reader.pages):
            # 展开也被子进程内存/CPU上限包围，不能仅在主进程解析后再检查。
            content = page.get_contents()
            if content is not None and len(content.get_data()) > 8 * 1024 * 1024:
                raise ValueError("PDF_PAGE_TOO_LARGE")
            text = page.extract_text() or ""
            total += len(text)
            if total > request["maxTextCharacters"]:
                raise ValueError("TEXT_LIMIT_EXCEEDED")
            pages.append({"page": index + 1, "text": text, "hasImages": bool(page.images)})
        return {
            "type": "pdf",
            "pages": pages,
            "needsOcr": any(not p["text"].strip() or p["hasImages"] for p in pages),
        }
    expected = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG"}.get(extension)
    if expected is None:
        raise ValueError("UNSUPPORTED_FORMAT")
    Image.MAX_IMAGE_PIXELS = 25_000_000
    with Image.open(io.BytesIO(data)) as picture:
        if (
            picture.format != expected
            or picture.width * picture.height > 25_000_000
            or getattr(picture, "n_frames", 1) != 1
        ):
            raise ValueError("INVALID_IMAGE")
        picture.verify()
    return {"type": extension, "pages": [{"page": 1, "text": ""}], "needsOcr": True}


def main() -> None:
    # macOS 的 RLIMIT_AS 语义与 Linux 不同；生产 Docker 另设硬内存上限。
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (20, 21))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    try:
        payload = json.load(sys.stdin)
        if payload.get("operation") == "split":
            from types import SimpleNamespace

            from rag_engine.models import ParseRequest
            from rag_engine.parsing import split_blocks

            settings = SimpleNamespace(
                max_text_characters=payload["maxTextCharacters"], max_chunks=payload["maxChunks"]
            )
            chunks = split_blocks(payload["blocks"], ParseRequest(**payload["request"]), settings)
            result = {"chunks": [chunk.model_dump() for chunk in chunks]}
        else:
            result = inspect_document(payload)
        print(json.dumps({"result": result}, ensure_ascii=False))
    except Exception as error:
        # 解析异常可能嵌入原文；只能返回约定错误码。
        allowed = {
            "INVALID_FILE",
            "ENCRYPTED_PDF",
            "PAGE_LIMIT_EXCEEDED",
            "PDF_PAGE_TOO_LARGE",
            "TEXT_LIMIT_EXCEEDED",
            "CHUNK_LIMIT_EXCEEDED",
            "DOCUMENT_HAS_NO_TEXT",
            "UNSUPPORTED_FORMAT",
            "INVALID_IMAGE",
        }
        code = str(error) if str(error) in allowed else "INVALID_FILE"
        print(json.dumps({"error": code}))


if __name__ == "__main__":
    main()
