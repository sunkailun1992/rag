"""只有 Java 门面可访问的内部 API；租户身份来自门面，不能直接面向浏览器开放。"""

import asyncio
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from rag_engine.config import Settings
from rag_engine.errors import EngineError
from rag_engine.graph_jobs import GraphManager
from rag_engine.graph_models import (
    GraphBuild,
    GraphBuildRequest,
    GraphDeactivation,
    GraphQueryRequest,
    GraphResponse,
)
from rag_engine.models import ParseRequest, ParseResponse, RetrievalRequest, RetrievalResponse
from rag_engine.parsing import parse_document
from rag_engine.retrieval import retrieve

logger = logging.getLogger("rag_engine")


def error_response(code: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code}}, status_code=status, headers={"Cache-Control": "no-store"})


class RequestBoundary:
    """在 JSON 反序列化前验证服务凭据与体积，防止无权限大请求消耗解析资源。"""

    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/v1/"):
            await self.app(scope, receive, send)
            return
        headers = scope["headers"]
        auth = [v for k, v in headers if k.lower() == b"authorization"]
        tenants = [v for k, v in headers if k.lower() == b"x-tenant-id"]
        expected = ("Bearer " + self.settings.api_key.get_secret_value()).encode()
        if len(auth) != 1 or not secrets.compare_digest(auth[0], expected):
            await error_response("UNAUTHORIZED", 401)(scope, receive, send)
            return
        if len(tenants) != 1 or not re.fullmatch(rb"[A-Za-z0-9_-]{1,64}", tenants[0]):
            await error_response("INVALID_TENANT", 400)(scope, receive, send)
            return
        scope.setdefault("state", {})["tenant"] = tenants[0].decode("ascii")
        if scope["method"] in {"POST", "PUT", "PATCH"}:
            content_types = [v for k, v in headers if k.lower() == b"content-type"]
            if len(content_types) != 1 or content_types[0].split(b";", 1)[0] != b"application/json":
                await error_response("JSON_REQUIRED", 415)(scope, receive, send)
                return
            maximum = 4 * ((self.settings.max_file_bytes + 2) // 3) + 4096
            if scope["path"].startswith("/v1/graphs/"):
                maximum = 8 * 1024 * 1024
            elif scope["path"] != "/v1/parses":
                maximum = 65536
            buffer = bytearray()
            try:
                async with asyncio.timeout(10):
                    while True:
                        event = await receive()
                        if event["type"] == "http.disconnect":
                            return
                        buffer.extend(event.get("body", b""))
                        if len(buffer) > maximum:
                            await error_response("REQUEST_TOO_LARGE", 413)(scope, receive, send)
                            return
                        if not event.get("more_body", False):
                            break
            except TimeoutError:
                await error_response("REQUEST_TIMEOUT", 408)(scope, receive, send)
                return
            sent = False

            async def bounded_receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": bytes(buffer), "more_body": False}
                return await receive()

            await self.app(scope, bounded_receive, send)
        else:
            await self.app(scope, receive, send)


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(
            timeout=10,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        ) as client:
            app.state.client = client
            app.state.parse_slots = asyncio.Semaphore(settings.parse_workers)
            app.state.retrieval_slots = asyncio.Semaphore(16)
            app.state.graph = None
            if settings.graph_enabled and all(
                [
                    settings.neo4j_url,
                    settings.neo4j_password.get_secret_value(),
                    settings.qdrant_url,
                    settings.qwen_base_url,
                    settings.qwen_api_key.get_secret_value(),
                ]
            ):
                manager = GraphManager(settings, client)
                try:
                    await manager.start()
                    app.state.graph = manager
                except Exception:
                    logger.warning("rag_graph_failed code=GRAPH_STORE_UNAVAILABLE")
                    await manager.repository.driver.close()
            try:
                yield
            finally:
                if app.state.graph:
                    await app.state.graph.stop()

    app = FastAPI(
        title="RAG Engine Internal API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(RequestBoundary, settings=settings)

    @app.exception_handler(EngineError)
    async def engine_failure(_request: Request, exception: EngineError):
        logger.warning("rag_engine_failed code=%s status=%d", exception.code, exception.status)
        return error_response(exception.code, exception.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_request: Request, _exception: RequestValidationError):
        # Pydantic 原始 errors 里含 input，可能是正文、base64 或密钥，不对外返回。
        return error_response("INVALID_REQUEST", 422)

    @app.get("/health")
    async def health():
        return {"status": "up"}

    @app.get("/v1/openapi.json", include_in_schema=False)
    async def openapi():
        return app.openapi()

    @app.post("/v1/parses", response_model=ParseResponse)
    async def parses(body: ParseRequest, request: Request):
        started = time.monotonic()
        try:
            await asyncio.wait_for(app.state.parse_slots.acquire(), timeout=0.01)
        except TimeoutError as error:
            raise EngineError("PARSER_BUSY", 429) from error
        try:
            async with asyncio.timeout(settings.parse_timeout_seconds):
                return await parse_document(body, settings, app.state.client)
        except TimeoutError as error:
            raise EngineError("PARSER_TIMEOUT", 504) from error
        except httpx.HTTPError as error:
            raise EngineError("OCR_UNAVAILABLE", 503) from error
        finally:
            app.state.parse_slots.release()
            logger.info("rag_parse_finished elapsed_ms=%d", (time.monotonic() - started) * 1000)

    @app.post("/v1/retrievals", response_model=RetrievalResponse)
    async def retrievals(body: RetrievalRequest, request: Request):
        started = time.monotonic()
        try:
            await asyncio.wait_for(app.state.retrieval_slots.acquire(), timeout=0.01)
        except TimeoutError as error:
            raise EngineError("RETRIEVAL_BUSY", 429) from error
        try:
            async with asyncio.timeout(settings.retrieval_timeout_seconds):
                return await retrieve(
                    body,
                    request.state.tenant,
                    settings,
                    app.state.client,
                    graph_repository=app.state.graph.repository if app.state.graph else None,
                )
        except TimeoutError as error:
            raise EngineError("RETRIEVAL_TIMEOUT", 504) from error
        except httpx.HTTPError as error:
            raise EngineError("RETRIEVAL_UNAVAILABLE", 503) from error
        finally:
            app.state.retrieval_slots.release()
            logger.info("rag_retrieval_finished elapsed_ms=%d", (time.monotonic() - started) * 1000)

    def graph_manager():
        if app.state.graph is None:
            raise EngineError("GRAPH_NOT_CONFIGURED", 503)
        return app.state.graph

    async def graph_operation(operation):
        try:
            async with asyncio.timeout(settings.graph_query_timeout_seconds):
                return await operation
        except EngineError:
            raise
        except TimeoutError as error:
            raise EngineError("GRAPH_REQUEST_TIMEOUT", 504) from error
        except Exception as error:
            raise EngineError("GRAPH_STORE_UNAVAILABLE", 503) from error

    @app.post("/v1/graphs/builds", response_model=GraphBuild)
    async def graph_builds(body: GraphBuildRequest, request: Request):
        return await graph_operation(graph_manager().submit(request.state.tenant, body))

    @app.post("/v1/graphs/queries", response_model=GraphResponse, response_model_exclude_unset=True)
    async def graph_queries(body: GraphQueryRequest, request: Request):
        return await graph_operation(graph_manager().query(request.state.tenant, body))

    @app.post("/v1/graphs/deactivations")
    async def graph_deactivations(body: GraphDeactivation, request: Request):
        return await graph_operation(
            graph_manager().repository.deactivate(
                request.state.tenant,
                body.documentId,
                body.documentRevision,
            )
        )

    @app.get("/v1/graphs/builds/{buildId}", response_model=GraphBuild)
    async def graph_status(buildId: str, knowledgeBase: str, request: Request):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", buildId) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", knowledgeBase
        ):
            raise EngineError("INVALID_REQUEST", 422)
        return await graph_operation(
            graph_manager().repository.status(request.state.tenant, knowledgeBase, buildId)
        )

    def contract_schema():
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
            schema.setdefault("components", {})["securitySchemes"] = {
                "ServiceBearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "仅 Java 门面持有的服务凭据，不接受用户 JWT。",
                }
            }
            schema["components"].setdefault("schemas", {})["EngineError"] = {
                "type": "object",
                "required": ["error"],
                "additionalProperties": False,
                "properties": {
                    "error": {
                        "type": "object",
                        "required": ["code"],
                        "additionalProperties": False,
                        "properties": {"code": {"type": "string"}},
                    }
                },
            }
            for path, operations in schema["paths"].items():
                if not path.startswith("/v1/"):
                    continue
                for operation in operations.values():
                    operation["security"] = [{"ServiceBearer": []}]
                    operation.setdefault("parameters", []).append(
                        {
                            "name": "X-Tenant-Id",
                            "in": "header",
                            "required": True,
                            "description": "来自 Java 已验证身份的当前租户，不能使用前端任意传值。",
                            "schema": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
                        }
                    )
                    for status in [400, 401, 408, 413, 415, 422, 429, 502, 503, 504]:
                        operation["responses"][str(status)] = {
                            "description": "安全错误码，不含请求正文、上游响应或配置值。",
                            "content": {
                                "application/json": {"schema": {"$ref": "#/components/schemas/EngineError"}}
                            },
                        }
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = contract_schema
    return app
