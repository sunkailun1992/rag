"""逐页基础文字 OCR；没有模型、缺页或空页都明确失败。"""

import base64
import contextlib
import io
import json
import math
import os
import sys
from pathlib import Path

from PIL import Image

MAX_PIXELS = 8_000_000
MAX_PAGES = 25
MAX_BLOCKS = 10_000
MODEL_NAMES = ("PP-OCRv5_mobile_det", "PP-OCRv5_mobile_rec")
CAPABILITIES = {"textRecognition": True, "tableStructure": False, "layoutAnalysis": False}


def pipeline(model_root: str):
    from paddleocr import PaddleOCR

    root = Path(model_root)
    for name in MODEL_NAMES:
        if not (root / name / "inference.json").is_file():
            raise ValueError("OCR_MODELS_NOT_AVAILABLE")
    return PaddleOCR(
        text_detection_model_name=MODEL_NAMES[0],
        text_detection_model_dir=str(root / MODEL_NAMES[0]),
        text_recognition_model_name=MODEL_NAMES[1],
        text_recognition_model_dir=str(root / MODEL_NAMES[1]),
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_recognition_batch_size=1,
        text_det_limit_side_len=1280,
        text_det_limit_type="max",
        device="cpu",
        enable_mkldnn=False,
        cpu_threads=2,
    )


def pages(raw: bytes, file_type: int):
    if file_type == 0:
        import pypdfium2 as pdfium

        if not raw.startswith(b"%PDF-"):
            raise ValueError("INVALID_PDF")
        with pdfium.PdfDocument(raw) as document:
            if not 0 < len(document) <= MAX_PAGES:
                raise ValueError("PAGE_LIMIT_EXCEEDED")
            for page in document:
                width, height = page.get_size()
                scale = 120 / 72
                if width * height * scale * scale > MAX_PIXELS:
                    raise ValueError("PIXEL_LIMIT_EXCEEDED")
                bitmap = page.render(scale=scale)
                try:
                    yield bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
                    page.close()
    else:
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"PNG", "JPEG"}:
                raise ValueError("INVALID_IMAGE")
            if image.width * image.height > MAX_PIXELS:
                raise ValueError("PIXEL_LIMIT_EXCEEDED")
            image.load()
            yield image.convert("RGB")


def convert_result(result: dict, page_index: int) -> dict:
    texts = result["rec_texts"]
    polygons = result["rec_polys"]
    scores = result["rec_scores"]
    if not len(texts) == len(polygons) == len(scores):
        raise ValueError("OCR_INVALID_RESULT")
    blocks = []
    for text, polygon, score in zip(texts, polygons, scores, strict=True):
        if not isinstance(text, str) or not math.isfinite(float(score)):
            raise ValueError("OCR_INVALID_RESULT")
        if not text.strip() or float(score) < 0.5:
            continue
        if len(polygon) != 4 or any(len(point) != 2 for point in polygon):
            raise ValueError("OCR_INVALID_RESULT")
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
        if any(not math.isfinite(value) or value < 0 for value in xs + ys):
            raise ValueError("OCR_INVALID_RESULT")
        blocks.append(
            {
                "block_label": "ocr-text",
                "block_content": str(text),
                "block_bbox": [min(xs), min(ys), max(xs), max(ys)],
            }
        )
    if not blocks:
        raise ValueError("OCR_PAGE_HAS_NO_TEXT")
    return {"prunedResult": {"page_index": page_index, "parsing_res_list": blocks}}


def recognize(payload: dict) -> dict:
    import numpy as np

    raw = base64.b64decode(payload["file"], validate=True)
    if not raw or len(raw) > 20 * 1024 * 1024:
        raise ValueError("FILE_SIZE_EXCEEDED")
    ocr = pipeline(os.environ.get("RAG_OCR_MODEL_ROOT", "/opt/models"))
    parsed = []
    total_text = 0
    total_blocks = 0
    for index, image in enumerate(pages(raw, payload["fileType"])):
        try:
            results = ocr.predict(np.asarray(image))
            if len(results) != 1:
                raise ValueError("OCR_INVALID_RESULT")
            page = convert_result(results[0], index)
            total_text += sum(
                len(b["block_content"]) for b in page["prunedResult"]["parsing_res_list"]
            )
            total_blocks += len(page["prunedResult"]["parsing_res_list"])
            if total_blocks > MAX_BLOCKS:
                raise ValueError("BLOCK_LIMIT_EXCEEDED")
            if total_text > 1_000_000:
                raise ValueError("TEXT_LIMIT_EXCEEDED")
            parsed.append(page)
        finally:
            image.close()
    if not parsed:
        raise ValueError("DOCUMENT_HAS_NO_TEXT")
    return {
        "errorCode": 0,
        "result": {"layoutParsingResults": parsed, "capabilities": CAPABILITIES},
    }


if __name__ == "__main__":
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (45, 46))
    payload = json.load(sys.stdin)
    try:
        # Paddle/PaddleX may print initialization details; never mix them with JSON or API logs.
        with contextlib.redirect_stdout(sys.stderr):
            result = recognize(payload)
    except Exception as error:
        allowed = {
            "OCR_MODELS_NOT_AVAILABLE",
            "INVALID_PDF",
            "INVALID_IMAGE",
            "PAGE_LIMIT_EXCEEDED",
            "PIXEL_LIMIT_EXCEEDED",
            "OCR_INVALID_RESULT",
            "OCR_PAGE_HAS_NO_TEXT",
            "FILE_SIZE_EXCEEDED",
            "TEXT_LIMIT_EXCEEDED",
            "DOCUMENT_HAS_NO_TEXT",
            "BLOCK_LIMIT_EXCEEDED",
        }
        code = str(error) if str(error) in allowed else "OCR_FAILED"
        result = {"error": code}
    print(json.dumps(result, ensure_ascii=False))
