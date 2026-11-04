"""保留来源的 PDF/OCR 解析；任何页失败都拒绝整个文档，不返回静默缺页的成功。"""

import asyncio
import base64
import binascii
import json
import math
import os
import re
import sys

import httpx

from rag_engine.config import Settings
from rag_engine.errors import EngineError
from rag_engine.http import post_json
from rag_engine.models import Chunk, ParseRequest, ParseResponse


async def run_worker(payload: dict, settings: Settings) -> dict:
    # 限制 BLAS 线程，避免导入 LlamaIndex/numpy 时放大子进程虚拟内存与 CPU。
    environment = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "rag_engine.worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=environment,
    )
    try:
        output, _ = await asyncio.wait_for(
            process.communicate(json.dumps(payload).encode()),
            min(settings.parse_timeout_seconds, 25),
        )
        if process.returncode != 0:
            raise EngineError("PARSER_RESOURCE_LIMIT", 422)
        result = json.loads(output)
        if "error" in result:
            raise EngineError(result["error"], 422)
        return result["result"]
    except (json.JSONDecodeError, KeyError) as error:
        raise EngineError("PARSER_FAILED", 422) from error
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


async def inspect_in_worker(request: ParseRequest, settings: Settings) -> dict:
    payload = request.model_dump()
    payload.update(
        operation="inspect", maxPages=settings.max_pages, maxTextCharacters=settings.max_text_characters
    )
    return await run_worker(payload, settings)


async def split_in_worker(blocks: list[dict], request: ParseRequest, settings: Settings) -> list[Chunk]:
    # 切片也属于 CPU 任务；取消/超时必须终止真正的进程，不能让后台线程继续占用资源。
    result = await run_worker(
        {
            "operation": "split",
            "blocks": blocks,
            "request": {**request.model_dump(exclude={"contentBase64"}), "contentBase64": "AAAA"},
            "maxTextCharacters": settings.max_text_characters,
            "maxChunks": settings.max_chunks,
        },
        settings,
    )
    return [Chunk.model_validate(chunk) for chunk in result["chunks"]]


def split_blocks(blocks: list[dict], request: ParseRequest, settings: Settings) -> list[Chunk]:
    from llama_index.core.node_parser import SentenceSplitter

    # 与 Java 的字符上限同义；不触发 tiktoken/NLTK 的运行时模型下载。
    splitter = SentenceSplitter(
        chunk_size=request.maxChunkCharacters,
        chunk_overlap=min(100, request.maxChunkCharacters // 8),
        tokenizer=list,
        chunking_tokenizer_fn=lambda value: re.findall(r"[^。！？\n]+[。！？\n]*|[。！？\n]+", value),
    )
    chunks = []
    total = 0
    for block in blocks:
        text = block["text"]
        total += len(text)
        if total > settings.max_text_characters:
            raise EngineError("TEXT_LIMIT_EXCEEDED", 422)
        for index, content in enumerate(splitter.split_text(text)):
            if not content.strip():
                continue
            if len(content) > request.maxChunkCharacters:
                raise EngineError("CHUNK_LIMIT_EXCEEDED", 422)
            metadata = {
                "page": block["page"],
                "block_type": block["type"],
                "bbox": block.get("bbox") or None,
                "source_name": request.sourceName,
            }
            if "ocr_capabilities" in block:
                metadata["ocr_capabilities"] = block["ocr_capabilities"]
            chunks.append(
                Chunk(
                    chunkType=block["type"],
                    sectionPath=f"第{block['page']}页",
                    locator=f"第{block['page']}页 / 区块{block['index']} / 切片{index + 1}",
                    content=content,
                    metadata=metadata,
                )
            )
            if len(chunks) > settings.max_chunks:
                raise EngineError("CHUNK_LIMIT_EXCEEDED", 422)
    if not chunks:
        raise EngineError("DOCUMENT_HAS_NO_TEXT", 422)
    return chunks


def ocr_blocks(response: dict, expected_pages: int) -> list[dict]:
    try:
        if not isinstance(response, dict) or response.get("errorCode", 0) != 0:
            raise ValueError("OCR error")
        pages = response["result"]["layoutParsingResults"]
        if not isinstance(pages, list) or len(pages) != expected_pages:
            raise ValueError("missing page")
        capabilities = response.get("capabilities", response["result"].get("capabilities"))
        if capabilities is not None and (
            not isinstance(capabilities, dict)
            or set(capabilities) != {"textRecognition", "tableStructure", "layoutAnalysis"}
            or any(type(value) is not bool for value in capabilities.values())
        ):
            raise ValueError("invalid OCR capabilities")
        result = []
        seen_pages = set()
        for position, page in enumerate(pages):
            parsed = page["prunedResult"]
            page_index = parsed.get("page_index")
            if page_index is not None and type(page_index) is not int:
                raise ValueError("invalid page index")
            page_number = position + 1 if page_index is None else page_index + 1
            if page_number in seen_pages or not 1 <= page_number <= expected_pages:
                raise ValueError("invalid page")
            seen_pages.add(page_number)
            blocks = parsed["parsing_res_list"]
            if not isinstance(blocks, list):
                raise ValueError("invalid blocks")
            page_start = len(result)
            for index, block in enumerate(blocks):
                text = block["block_content"]
                box = block.get("block_bbox", [])
                if not isinstance(text, str) or not isinstance(box, list) or len(box) not in {0, 4}:
                    raise ValueError("invalid block")
                if box and any(type(value) not in {int, float} or not math.isfinite(value) for value in box):
                    raise ValueError("invalid bbox")
                if box and (min(box) < 0 or box[2] < box[0] or box[3] < box[1]):
                    raise ValueError("invalid bbox ordering")
                label = block["block_label"]
                if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", label):
                    raise ValueError("invalid block label")
                if text.strip():
                    result.append(
                        {
                            "page": page_number,
                            "index": index + 1,
                            "text": text,
                            "type": label,
                            "bbox": box,
                        }
                    )
            if capabilities is not None:
                for block in result[page_start:]:
                    block["ocr_capabilities"] = capabilities
            if len(result) == page_start:
                raise EngineError("OCR_PAGE_HAS_NO_TEXT", 422)
        return result
    except (KeyError, TypeError, ValueError) as error:
        raise EngineError("OCR_INVALID_RESPONSE") from error


async def parse_document(
    request: ParseRequest, settings: Settings, client: httpx.AsyncClient
) -> ParseResponse:
    try:
        raw = base64.b64decode(request.contentBase64, validate=True)
    except (ValueError, binascii.Error) as error:
        raise EngineError("INVALID_BASE64", 422) from error
    if not raw or len(raw) > settings.max_file_bytes:
        raise EngineError("FILE_SIZE_EXCEEDED", 413)
    extension = request.sourceName.rsplit(".", 1)[-1].lower()
    if extension not in {"pdf", "png", "jpg", "jpeg"}:
        raise EngineError("UNSUPPORTED_FORMAT", 415)
    document = await inspect_in_worker(request, settings)
    if document["needsOcr"]:
        if not settings.ocr_url:
            raise EngineError("OCR_NOT_CONFIGURED", 503)
        headers = {}
        if settings.ocr_api_key.get_secret_value():
            headers["Authorization"] = "Bearer " + settings.ocr_api_key.get_secret_value()
        response = await post_json(
            client,
            settings.ocr_url + "/layout-parsing",
            {"file": request.contentBase64, "fileType": 0 if extension == "pdf" else 1, "visualize": False},
            headers,
            settings.max_upstream_bytes,
        )
        blocks = ocr_blocks(response, len(document["pages"]))
    else:
        blocks = [
            {"page": page["page"], "index": 1, "text": page["text"], "type": "pdf-text"}
            for page in document["pages"]
        ]
    chunks = await split_in_worker(blocks, request, settings)
    return ParseResponse(
        title=request.title.strip() or request.sourceName.rsplit(".", 1)[0],
        sourceName=request.sourceName,
        sourceType=extension,
        chunks=chunks,
    )
