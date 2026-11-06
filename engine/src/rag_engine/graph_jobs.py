"""单并发持久任务调度；长构建在可终止进程运行，发布由父进程控制。"""

import asyncio
import contextlib
import json
import logging
import os
import re
import sys
import uuid

from rag_engine.errors import (
    GRAPH_MODEL_CODES,
    GRAPH_MODEL_REASONS,
    VALIDATION_REASONS,
    EngineError,
    GraphModelError,
)
from rag_engine.graph_models import ActiveGraphSource, GraphBuildRequest
from rag_engine.graph_query import aggregate_graph
from rag_engine.graph_repository import GraphRepository
from rag_engine.graph_sources import evidence_excerpt, read_active_sources

logger = logging.getLogger("rag_engine.graph")


async def run_worker(payload, settings, remaining: float):
    config = settings.model_dump(mode="json")
    for name in ["api_key", "qdrant_api_key", "qwen_api_key", "neo4j_password", "ocr_api_key"]:
        config[name] = getattr(settings, name).get_secret_value()
    raw = json.dumps({**payload, "settings": config}, ensure_ascii=False).encode()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "rag_engine.graph_worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
    )
    try:
        async with asyncio.timeout(remaining):
            stdout, _ = await process.communicate(raw)
        if process.returncode != 0 or len(stdout) > 65536:
            raise EngineError("GRAPH_BUILD_FAILED")
        result = json.loads(stdout)
        if not isinstance(result, dict):
            raise EngineError("GRAPH_BUILD_FAILED")
        if "error" in result:
            code = result["error"]
            extraction_codes = {
                "GRAPH_EXTRACTION_INVALID",
                "GRAPH_EXTRACTION_UNGROUNDED",
                "GRAPH_EXTRACTION_LIMIT",
            }
            allowed = (
                extraction_codes
                | GRAPH_MODEL_CODES
                | {
                    "GRAPH_SOURCE_CHANGED",
                    "GRAPH_CONTENT_LIMIT",
                    "GRAPH_READBACK_FAILED",
                    "GRAPH_NOT_CONFIGURED",
                }
            )
            if type(code) is not str or code not in allowed:
                raise EngineError("GRAPH_BUILD_FAILED")
            reason = result.get("reason")
            model_reason = (
                code in GRAPH_MODEL_CODES
                and type(reason) is str
                and reason in GRAPH_MODEL_REASONS
                and GraphModelError(reason).code == code
            )
            extraction_reason = (
                code in extraction_codes
                and type(reason) is str
                and reason in VALIDATION_REASONS - GRAPH_MODEL_REASONS
            )
            if model_reason or extraction_reason:
                source_hash = result.get("sourceHash")
                event = "rag_graph_model_failed" if model_reason else "rag_graph_validation_failed"
                if type(source_hash) is str and re.fullmatch(r"[0-9a-f]{64}", source_hash):
                    logger.warning("%s code=%s reason=%s source_hash=%s", event, code, reason, source_hash)
                else:
                    logger.warning("%s code=%s reason=%s", event, code, reason)
            elif code in GRAPH_MODEL_CODES:
                logger.warning("rag_graph_model_failed code=%s", code)
            raise EngineError(code)
        counts = result["result"]
        if (
            not isinstance(counts, dict)
            or set(counts) != {"processedDocumentCount", "nodeCount", "edgeCount"}
            or any(type(value) is not int or value < 0 for value in counts.values())
        ):
            raise EngineError("GRAPH_BUILD_FAILED")
        return counts
    except TimeoutError as error:
        raise EngineError("GRAPH_BUILD_TIMEOUT", 504) from error
    except (ValueError, KeyError, TypeError) as error:
        raise EngineError("GRAPH_BUILD_FAILED") from error
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


class GraphManager:
    def __init__(self, settings, client, repository=None):
        self.settings, self.client = settings, client
        self.owner = str(uuid.uuid4())
        self.repository = repository or GraphRepository(settings, self.owner)
        self.tasks = []

    async def start(self):
        await self.repository.start()
        self.tasks = [asyncio.create_task(self.heartbeat()), asyncio.create_task(self.pump())]

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.repository.stop()

    async def heartbeat(self):
        try:
            while True:
                await asyncio.sleep(self.settings.graph_lease_seconds / 3)
                await self.repository.heartbeat()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("rag_graph_worker_failed code=GRAPH_WORKER_LEASE_LOST")
            # 租约失效立即取消构建子进程；发布事务另有 owner/lease fencing。
            for task in self.tasks:
                if task is not asyncio.current_task():
                    task.cancel()

    async def pump(self):
        while True:
            try:
                claimed = await self.repository.claim()
                if claimed is None:
                    with contextlib.suppress(Exception):
                        await self.repository.cleanup_pending()
                    await asyncio.sleep(0.5)
                    continue
                job = claimed["job"]
                try:
                    async with asyncio.timeout(claimed["remaining"]):
                        counts = await run_worker(
                            {
                                "buildId": job["buildId"],
                                "tenant": job["tenant"],
                                "request": json.loads(job["requestJson"]),
                            },
                            self.settings,
                            claimed["remaining"],
                        )
                        request = GraphBuildRequest.model_validate_json(job["requestJson"])
                        await read_active_sources(
                            request.activeSources,
                            job["tenant"],
                            request.knowledgeBase,
                            self.settings,
                            self.client,
                        )
                        await self.repository.publish(job, counts)
                except asyncio.CancelledError:
                    with contextlib.suppress(Exception):
                        await self.repository.finish_failed(job, "GRAPH_WORKER_STOPPED", cancelled=True)
                    raise
                except Exception as error:
                    code = (
                        error.code
                        if isinstance(error, EngineError)
                        else (
                            "GRAPH_BUILD_TIMEOUT" if isinstance(error, TimeoutError) else "GRAPH_BUILD_FAILED"
                        )
                    )
                    with contextlib.suppress(Exception):
                        await self.repository.finish_failed(job, code)
                    logger.warning("rag_graph_build_failed code=%s", code)
                with contextlib.suppress(Exception):
                    await self.repository.cleanup(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("rag_graph_scheduler_failed code=GRAPH_STORE_UNAVAILABLE")
                await asyncio.sleep(1)

    async def submit(self, tenant, request):
        # 身份先检验，再入持久队列。等待和后台构建不能当作完成回执。
        async with asyncio.timeout(self.settings.graph_query_timeout_seconds):
            await read_active_sources(
                request.activeSources, tenant, request.knowledgeBase, self.settings, self.client
            )
            return await self.repository.submit(tenant, request, str(uuid.uuid4()))

    async def query(self, tenant, request):
        async with asyncio.timeout(self.settings.graph_query_timeout_seconds):
            await read_active_sources(
                request.activeSources, tenant, request.knowledgeBase, self.settings, self.client
            )
            if request.nodeType not in {None, "", "ENTITY"}:
                rows, entities, truncated = [], [], False
            else:
                rows, entities, truncated = await self.repository.graph_rows(tenant, request)
            identities = {source.pointId: source for source in request.activeSources}
            returned = {}
            for row in rows + entities:
                try:
                    actual = ActiveGraphSource(
                        **{key: row["source"][key] for key in ActiveGraphSource.model_fields}
                    )
                    if actual != identities.get(actual.pointId):
                        raise ValueError()
                    returned[actual.pointId] = actual
                except (ValueError, KeyError, TypeError) as error:
                    raise EngineError("GRAPH_SOURCE_CHANGED", 409) from error
            verified = await read_active_sources(
                list(returned.values()), tenant, request.knowledgeBase, self.settings, self.client
            )
            current_sources = {value.identity.pointId: value for value in verified}
            for row in rows + entities:
                current = current_sources[row["source"]["pointId"]]
                terms = [row["subjectName"]]
                if "objectName" in row:
                    terms.append(row["objectName"])
                row["source"] = {**current.provenance, **evidence_excerpt(current.content, terms)}
            result = aggregate_graph(
                rows,
                entities,
                request.knowledgeBase,
                request.viewMode,
                request.limit,
                self.settings.graph_max_edges,
                tenant,
            )
            result["truncated"] |= truncated
            build = await self.repository.status(tenant, request.knowledgeBase)
            result["build"] = build.model_dump() if build else None
            if len(json.dumps(result, ensure_ascii=False).encode()) > 8 * 1024 * 1024:
                raise EngineError("GRAPH_RESPONSE_TOO_LARGE")
            return result
