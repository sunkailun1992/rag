"""私有基础 OCR：限制请求与实际子进程，明确不提供表格结构或版面分析。"""

import asyncio
import json
import os
import secrets
import sys

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

CAPABILITIES = {"textRecognition": True, "tableStructure": False, "layoutAnalysis": False}
MAX_BODY_BYTES = 28 * 1024 * 1024


def create_app() -> FastAPI:
    key = os.environ.get("RAG_OCR_API_KEY", "")
    if len(key) < 32:
        raise ValueError("RAG_OCR_API_KEY must contain at least 32 characters")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    semaphore = asyncio.Semaphore(1)

    @app.get("/health")
    async def health():
        return {"status": "up", "capabilities": CAPABILITIES}

    @app.post("/layout-parsing")
    async def parse(request: Request):
        expected = "Bearer " + key
        if not secrets.compare_digest(
            request.headers.get("authorization", "").encode(), expected.encode()
        ):
            return JSONResponse({"errorCode": "UNAUTHORIZED"}, status_code=401)
        if semaphore.locked():
            return JSONResponse({"errorCode": "OCR_BUSY"}, status_code=429)
        async with semaphore:
            body = bytearray()
            try:
                async with asyncio.timeout(10):
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > MAX_BODY_BYTES:
                            return JSONResponse({"errorCode": "FILE_TOO_LARGE"}, status_code=413)
            except TimeoutError:
                return JSONResponse({"errorCode": "BODY_TIMEOUT"}, status_code=408)
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict) or set(payload) - {
                    "file",
                    "fileType",
                    "visualize",
                }:
                    raise ValueError("unexpected fields")
                if not isinstance(payload.get("file"), str):
                    raise ValueError("missing content")
                if type(payload.get("fileType")) is not int or payload["fileType"] not in {0, 1}:
                    raise ValueError("invalid type")
            except (ValueError, TypeError):
                return JSONResponse({"errorCode": "INVALID_REQUEST"}, status_code=422)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "ocr_worker.py",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
            )
            try:
                output, _ = await asyncio.wait_for(process.communicate(bytes(body)), timeout=50)
                if process.returncode != 0 or len(output) > 8 * 1024 * 1024:
                    return JSONResponse({"errorCode": "OCR_RESOURCE_LIMIT"}, status_code=422)
                result = json.loads(output)
                if "error" in result:
                    return JSONResponse({"errorCode": result["error"]}, status_code=422)
                return result
            except TimeoutError:
                return JSONResponse({"errorCode": "OCR_TIMEOUT"}, status_code=504)
            except (ValueError, TypeError):
                return JSONResponse({"errorCode": "OCR_FAILED"}, status_code=502)
            finally:
                if process.returncode is None:
                    process.kill()
                await process.wait()

    return app
