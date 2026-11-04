"""显式使用真实模型比较 dense/hybrid；仅写入本次唯一临时集合，结束核对删除。"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import importlib.metadata
import json
import logging
import math
import re
import secrets
import signal
import statistics
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from evaluation.runner import (
    evaluate,
    normalize_response,
    percentile,
    read_jsonl,
    validate_dataset,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation/questions.jsonl"
CORPUS = ROOT / "evaluation/corpus.jsonl"
COLLECTION_PATTERN = re.compile(r"rag_eval_[0-9a-f]{32}")


class LiveError(Exception):
    """只携带可公开的稳定错误码，不拼接上游正文或配置。"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def provenance() -> dict:
    digest = hashlib.sha256()
    for path in sorted((ROOT / "engine/src/rag_engine").glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=False).stdout.strip()
    return {
        "datasetSha256": file_hash(DATASET), "corpusSha256": file_hash(CORPUS),
        "engineSourceSha256": digest.hexdigest(), "engineLockSha256": file_hash(ROOT / "engine/uv.lock"),
        "gitHead": revision if re.fullmatch(r"[0-9a-f]{40,64}", revision) else None,
    }


def load_fixture() -> tuple[list[dict], list[dict]]:
    # 不提供任意文件参数，避免把真实文档误发到付费模型。
    corpus, questions = read_jsonl(CORPUS), read_jsonl(DATASET)
    validate_dataset(questions, corpus)
    if len(corpus) != 28 or len(questions) != 60 or any(row.get("synthetic") is not True for row in corpus):
        raise LiveError("SYNTHETIC_FIXTURE_CHANGED")
    if {row["tenantId"] for row in corpus} != {"eval-a", "eval-b"}:
        raise LiveError("SYNTHETIC_SCOPE_CHANGED")
    for document in corpus:
        if not isinstance(document.get("text"), str) or not 0 < len(document["text"]) <= 6000:
            raise LiveError("INVALID_SYNTHETIC_DOCUMENT")
    return corpus, questions


def export_langfuse(path: Path, questions: list[dict], hashes: dict) -> None:
    """Langfuse UI CSV 的三个可映射字段；仅导出合成题，不调用云端。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["input", "expected_output", "metadata"])
        writer.writeheader()
        for question in questions:
            values = {
                "input": {key: question[key] for key in ("query", "tenantId", "knowledgeBase")},
                "expected_output": {
                    "relevance": question["relevance"], "expectedEmpty": question["expectedEmpty"],
                    "forbiddenSources": question.get("forbiddenSources", []),
                },
                "metadata": {
                    "questionId": question["id"], "category": question.get("category"),
                    "synthetic": True, "evaluationLevel": "source-document",
                    "datasetSha256": hashes["datasetSha256"], "corpusSha256": hashes["corpusSha256"],
                },
            }
            writer.writerow({key: json.dumps(value, ensure_ascii=False) for key, value in values.items()})


def save_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def new_plan(corpus: list[dict], questions: list[dict], args) -> dict:
    return {
        "schemaVersion": 1, "status": "planned", "startedAt": utc_now(),
        "temporaryCollection": "rag_eval_" + uuid.uuid4().hex,
        "provenance": provenance(),
        "scope": "synthetic-regression-only",
        "setup": {
            "documentCount": len(corpus), "questionCount": len(questions),
            "documentEmbeddingBatches": math.ceil(len(corpus) / 10),
            "documentEmbeddingsBuiltOnce": True, "queryEmbeddingRequestLimit": len(questions) * 2,
            "rerankRequestLimit": len(questions) * 2,
        },
        "configuration": {"topK": args.k, "candidateCount": args.candidates,
                          "similarityThreshold": args.threshold, "retrievalTimeoutSeconds": 3.0,
                          "rerankTimeoutSeconds": 0.8, "pairOrder": "alternating-dense-first-hybrid-first"},
        "limitations": [
            "Synthetic source-level retrieval regression, not real-user relevance or OCR evaluation.",
            "Calls the actual Python retrieval implementation; Java HTTP/RPC and network overhead are excluded.",
            "No generated answers: faithfulness and answer correctness are not measured.",
            "One paired pass is not a statistically significant improvement or load test.",
        ],
        "cleanup": {"required": False, "attempted": False, "verifiedAbsent": False},
    }


def load_settings(collection: str):
    from rag_engine.config import Settings

    if not COLLECTION_PATTERN.fullmatch(collection):
        raise LiveError("UNSAFE_COLLECTION_NAME")
    # 显式覆盖 collection 和预算；忽略已有业务 collection / hybrid 开关环境值。
    # 此脚本直接调用内部函数，不监听端口；随机服务 key 只满足 Settings 契约。
    settings = Settings(api_key=secrets.token_urlsafe(32), collection=collection,
                        retrieval_timeout_seconds=3.0, rerank_timeout_seconds=0.8, hybrid_enabled=False)
    if not settings.qdrant_url or not settings.qwen_base_url or not settings.qwen_api_key.get_secret_value():
        raise LiveError("REQUIRED_ENVIRONMENT_MISSING")
    return settings


def safe_error(error: BaseException) -> str:
    from rag_engine.errors import EngineError

    if isinstance(error, asyncio.CancelledError | KeyboardInterrupt):
        return "INTERRUPTED"
    if isinstance(error, TimeoutError):
        return "RETRIEVAL_TIMEOUT"
    if isinstance(error, LiveError | EngineError) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", str(error)):
        return str(error)
    return "EVALUATION_OPERATION_FAILED"


async def request_json(client, method: str, url: str, headers: dict, body: dict | None = None):
    from rag_engine.http import request_json as engine_request_json

    return await engine_request_json(client, method, url, body, headers, 16 * 1024 * 1024)


async def status_code(client, method: str, url: str, headers: dict) -> int:
    # 存在性检查不读取响应正文，不将错误页保存或输出。
    async with client.stream(method, url, headers=headers) as response:
        return response.status_code


async def embed_corpus(client, settings, corpus: list[dict], setup: dict) -> list[list[float]]:
    vectors = []
    setup["documentEmbeddingRequestsCompleted"] = 0
    for start in range(0, len(corpus), 10):
        batch = corpus[start:start + 10]
        response = await request_json(client, "POST", settings.qwen_base_url + "/embeddings",
                                      {"Authorization": "Bearer " + settings.qwen_api_key.get_secret_value()}, {
                                          "model": settings.embedding_model,
                                          "input": [document["text"] for document in batch],
                                          "dimensions": settings.embedding_dimension, "encoding_format": "float",
                                      })
        try:
            rows = response["data"]
            if not isinstance(rows, list) or len(rows) != len(batch):
                raise ValueError("count")
            indexed = {}
            for row in rows:
                index, vector = row["index"], row["embedding"]
                if type(index) is not int or index in indexed or not 0 <= index < len(batch):
                    raise ValueError("index")
                if (not isinstance(vector, list) or len(vector) != settings.embedding_dimension
                        or any(type(value) not in (float, int) or not math.isfinite(value) for value in vector)
                        or not any(vector)):
                    raise ValueError("vector")
                indexed[index] = vector
            vectors.extend(indexed[index] for index in range(len(batch)))
        except (KeyError, TypeError, ValueError) as error:
            raise LiveError("DOCUMENT_EMBEDDING_INVALID_RESPONSE") from error
        setup["documentEmbeddingRequestsCompleted"] += 1
    return vectors


def points_for(corpus: list[dict], vectors: list[list[float]], settings) -> list[dict]:
    from rag_engine.lexical import SPARSE_MODEL, encode_sparse

    if len(vectors) != len(corpus):
        raise LiveError("DOCUMENT_EMBEDDING_COUNT_MISMATCH")
    points = []
    for document, vector in zip(corpus, vectors, strict=True):
        sparse = encode_sparse(document["text"])
        if not sparse["indices"]:
            raise LiveError("SYNTHETIC_DOCUMENT_HAS_NO_LEXICAL_TOKENS")
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, "rag-eval:" + document["id"]))
        payload = {
            "tenant_id": document["tenantId"], "knowledge_base": document["knowledgeBase"],
            "document_status": "ACTIVE", "document_id": identity, "chunk_id": identity,
            "document_title": document["title"], "source_name": document["sourceName"],
            "source_locator": "synthetic-document", "document_version": 1,
            "content": document["text"], "content_sha256": hashlib.sha256(document["text"].encode()).hexdigest(),
            "embedding_model": settings.embedding_model, "embedding_dimension": settings.embedding_dimension,
            "sparse_model": SPARSE_MODEL,
        }
        points.append({"id": identity, "vector": {"": vector, SPARSE_MODEL: sparse}, "payload": payload})
    return points


async def run_one(client, settings, question: dict, source_map: dict, configuration: dict) -> dict:
    from rag_engine.models import RetrievalRequest
    from rag_engine.retrieval import retrieve

    started = time.monotonic()
    row = {"id": question["id"], "sources": []}
    try:
        # 与 FastAPI 入口相同的总预算，包括 embedding、readiness、召回、RRF 和 rerank。
        async with asyncio.timeout(settings.retrieval_timeout_seconds):
            response = await retrieve(RetrievalRequest(
                query=question["query"], knowledgeBase=question["knowledgeBase"],
                topK=configuration["topK"], candidateCount=configuration["candidateCount"],
                similarityThreshold=configuration["similarityThreshold"], maxContextCharacters=12000,
            ), question["tenantId"], settings, client)
        sources = normalize_response(response.model_dump(), source_map)
        for source, hit in zip(sources, response.hits, strict=True):
            # 只保存稳定来源映射和有限数值，不保存检索正文；原排序不变。
            source["score"] = hit.score
        row.update({"sources": sources,
                    "mode": response.mode, "degraded": response.degraded})
    except Exception as error:  # noqa: BLE001 - 单题失败计入报告，禁止原始异常泄露输入。
        row["error"] = safe_error(error)
    row["latencyMs"] = round((time.monotonic() - started) * 1000, 3)
    return row


def score_summary(scores: list[float]) -> dict:
    return {
        "sampleCount": len(scores), "min": min(scores) if scores else None,
        "p05": percentile(scores, .05), "p25": percentile(scores, .25),
        "p50": percentile(scores, .5), "p75": percentile(scores, .75), "p95": percentile(scores, .95),
        "max": max(scores) if scores else None, "mean": statistics.mean(scores) if scores else None,
    }


def top_score_distribution(questions: list[dict], predictions: list[dict]) -> dict:
    """只统计实际返回的第一条；空召回不伪造 0，降级排序不混入 rerank 相关度。"""
    rows = {row["id"]: row for row in predictions}
    selected = {
        "positive": [question for question in questions if question["relevance"]],
        "negative": [question for question in questions if not question["relevance"]],
        "no-answer": [question for question in questions if question.get("category") == "no-answer"],
        "tenant-isolation": [question for question in questions if question.get("category") == "tenant-isolation"],
    }
    groups = {}
    for name, items in selected.items():
        non_degraded, degraded = [], []
        empty = failed = missing_score = 0
        for question in items:
            row = rows.get(question["id"])
            if row is None or row.get("error"):
                failed += 1
                continue
            sources = row.get("sources", [])
            if not sources:
                empty += 1
                continue
            value = sources[0].get("score")
            if type(value) not in (float, int) or not math.isfinite(value):
                missing_score += 1
                continue
            (degraded if row.get("degraded") else non_degraded).append(float(value))
        groups[name] = {"questionCount": len(items), "emptyResponseCount": empty,
                        "errorCount": failed, "missingScoreCount": missing_score,
                        "nonDegraded": score_summary(non_degraded), "degraded": score_summary(degraded)}
    return {
        "definition": "Score of the first returned hit, after engine filtering; not the pre-filter candidate maximum.",
        "limitations": "Empty/error/missing-score responses have no numeric score; degraded scores are separate and may use a different ranking scale.",
        "groups": groups,
    }


def comparison_report(questions: list[dict], corpus: list[dict], predictions: dict, configuration: dict) -> dict:
    results = {}
    for mode, rows in predictions.items():
        result = evaluate(questions, rows, corpus, configuration["topK"])
        result["degradedCount"] = sum(row.get("degraded", False) for row in rows)
        result["timeoutCount"] = sum(row.get("error") == "RETRIEVAL_TIMEOUT" for row in rows)
        result["blockedScopeViolationCount"] = sum(row.get("error") == "TENANT_ISOLATION_VIOLATION"
                                                  for row in rows)
        result["errorCodes"] = dict(Counter(row["error"] for row in rows if row.get("error")))
        result["modeMismatchCount"] = sum(row.get("mode") != mode for row in rows if not row.get("error"))
        result["topScoreDistribution"] = top_score_distribution(questions, rows)
        results[mode] = result
    dense, hybrid = results["dense"], results["hybrid"]
    metrics = ("recallAtK", "mrrAtK", "ndcgAtK", "latencyP50Ms", "latencyP95Ms")
    delta = {key: hybrid[key] - dense[key] if hybrid[key] is not None and dense[key] is not None else None
             for key in metrics}
    checks = {
        "completeResponses": all(value["errorCount"] == 0 for value in results.values()),
        "noScopeLeaks": all(value["scopeLeakCount"] == 0 for value in results.values()),
        "knownSources": all(value["unknownSourceCount"] == 0 for value in results.values()),
        "correctEmptyResponses": all(value["correctEmptyCount"] == value["negativeCount"]
                                     for value in results.values()),
        "allReranksCompleted": all(value["degradedCount"] == 0 for value in results.values()),
        "expectedRetrievalMode": all(value["modeMismatchCount"] == 0 for value in results.values()),
        "hybridRecallNoRegression": delta["recallAtK"] is not None and delta["recallAtK"] >= -1e-12,
        "hybridMrrNoRegression": delta["mrrAtK"] is not None and delta["mrrAtK"] >= -1e-12,
        "hybridNdcgNoRegression": delta["ndcgAtK"] is not None and delta["ndcgAtK"] >= -1e-12,
    }
    return {"results": results, "hybridMinusDense": delta, "acceptance": {"checks": checks}}


async def execute_comparison(settings, corpus: list[dict], questions: list[dict], report: dict,
                             output: Path, transport=None) -> dict:
    import httpx
    from rag_engine.lexical import SPARSE_MODEL
    from rag_engine.sparse_index import SparseIndex, scoped_filter

    if not COLLECTION_PATTERN.fullmatch(settings.collection) or settings.collection != report["temporaryCollection"]:
        raise LiveError("UNSAFE_COLLECTION_NAME")
    base = settings.qdrant_url + "/collections/" + settings.collection
    headers = {"api-key": settings.qdrant_api_key.get_secret_value()} if settings.qdrant_api_key.get_secret_value() else {}
    source_map = validate_dataset(questions, corpus)
    report["status"] = "running"
    report["configuration"].update({"embeddingModel": settings.embedding_model,
                                    "embeddingDimension": settings.embedding_dimension,
                                    "rerankModel": settings.rerank_model,
                                    "rerankScoreThreshold": settings.rerank_score_threshold,
                                    "sparseModel": SPARSE_MODEL})
    report["predictions"] = {"dense": [], "hybrid": []}
    save_report(output, report)
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False, transport=transport) as client:
        try:
            if await status_code(client, "GET", base, headers) != 404:
                raise LiveError("TEMPORARY_COLLECTION_NOT_ABSENT")
            vectors = await embed_corpus(client, settings, corpus, report["setup"])
            # 写前落盘清理对象；即使创建成功后丢失响应，也在 finally 清理这个唯一名称。
            report["cleanup"]["required"] = True
            save_report(output, report)
            await request_json(client, "PUT", base, headers, {
                "vectors": {"size": settings.embedding_dimension, "distance": "Cosine"},
                "sparse_vectors": {SPARSE_MODEL: {"modifier": "idf"}},
            })
            result = await request_json(client, "PUT", base + "/points?wait=true", headers,
                                        {"points": points_for(corpus, vectors, settings)})
            if result.get("result", {}).get("status") != "completed":
                raise LiveError("SYNTHETIC_WRITE_INCOMPLETE")
            index = SparseIndex(settings, client)
            scopes = Counter((document["tenantId"], document["knowledgeBase"]) for document in corpus)
            counts = {}
            for (tenant, knowledge_base), expected in scopes.items():
                count = await index.count(scoped_filter(tenant, knowledge_base))
                if count != expected:
                    raise LiveError("SYNTHETIC_READBACK_COUNT_MISMATCH")
                await index.require_ready(tenant, knowledge_base)
                counts[tenant + "/" + knowledge_base] = count
            report["setup"]["readbackCounts"] = counts
            report["setup"]["sparseReadyForAllScopes"] = True
            modes = {"dense": settings.model_copy(update={"hybrid_enabled": False}),
                     "hybrid": settings.model_copy(update={"hybrid_enabled": True})}
            for number, question in enumerate(questions):
                for mode in (("dense", "hybrid") if number % 2 == 0 else ("hybrid", "dense")):
                    row = await run_one(client, modes[mode], question, source_map, report["configuration"])
                    report["predictions"][mode].append(row)
                # 每题落盘，进程被外部终止时仍有已完成部分和清理对象；没有 query / content。
                save_report(output, report)
            report["status"] = "completed"
        except (Exception, asyncio.CancelledError, KeyboardInterrupt) as error:  # noqa: BLE001 - 必须记录失败并清理。
            report["status"] = "failed"
            report["errorCode"] = safe_error(error)
        finally:
            if report["cleanup"]["required"]:
                report["cleanup"]["attempted"] = True
                try:
                    # 清理不受单题 3 秒预算影响。仍遵守 30 秒上限，不删其他名称。
                    async with asyncio.timeout(30):
                        deletion = await status_code(client, "DELETE", base, headers)
                        absence = await status_code(client, "GET", base, headers)
                    report["cleanup"].update({"deleteStatus": deletion, "readbackStatus": absence,
                                                "verifiedAbsent": deletion in (200, 202, 204, 404) and absence == 404})
                except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001 - 清理失败不能隐藏或泄露响应。
                    report["cleanup"]["errorCode"] = safe_error(error)
            report.update(comparison_report(questions, corpus, report["predictions"], report["configuration"]))
            report["acceptance"]["checks"]["temporaryCollectionRemoved"] = report["cleanup"]["verifiedAbsent"]
            report["acceptance"]["checks"]["runCompleted"] = report["status"] == "completed"
            report["acceptance"]["passed"] = all(report["acceptance"]["checks"].values())
            report["finishedAt"] = utc_now()
            save_report(output, report)
    return report


async def with_interrupt_cleanup(*args, **kwargs):
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        return await execute_comparison(*args, **kwargs)
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="确认真实模型费用与本次临时集合写入/清理")
    parser.add_argument("--output", type=Path, default=ROOT / "build/evaluation-live/report.json")
    parser.add_argument("--langfuse-csv", type=Path, default=ROOT / "build/evaluation-live/langfuse-dataset.csv")
    parser.add_argument("--k", type=int, default=6)
    parser.add_argument("--candidates", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=.2)
    args = parser.parse_args(argv)
    report = None
    try:
        if not 1 <= args.k <= 20 or not args.k <= args.candidates <= 100 or not 0 <= args.threshold <= 1:
            raise LiveError("INVALID_EVALUATION_PARAMETERS")
        if args.output.resolve() == args.langfuse_csv.resolve():
            raise LiveError("OUTPUT_PATH_COLLISION")
        corpus, questions = load_fixture()
        report = new_plan(corpus, questions, args)
        export_langfuse(args.langfuse_csv, questions, report["provenance"])
        if not args.apply:
            save_report(args.output, report)
            print(json.dumps({"status": "planned", "networkRequests": 0, "documents": len(corpus),
                              "questions": len(questions), "requiresApply": True}))
            return 0
        # CLI 不开启第三方日志，避免调试环境输出请求 URL、正文或凭据。
        logging.disable(logging.CRITICAL)
        save_report(args.output, report)
        settings = load_settings(report["temporaryCollection"])
        report["provenance"]["dependencies"] = {
            name: importlib.metadata.version(name) for name in ("llama-index-core", "httpx", "pydantic")
        }
        report = asyncio.run(with_interrupt_cleanup(settings, corpus, questions, report, args.output))
        print(json.dumps({"status": report["status"], "passed": report["acceptance"]["passed"],
                          "cleanup": report["cleanup"], "temporaryCollection": report["temporaryCollection"]}))
        return 0 if report["acceptance"]["passed"] else 1
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 - CLI 边界不输出含私有环境值的异常。
        # 不能输出 Pydantic/HTTP/OS 异常字符串，其中可能含环境配置或本机路径。
        if report is not None and report["status"] == "planned":
            report.update({"status": "failed", "errorCode": "LIVE_EVALUATION_SETUP_FAILED", "finishedAt": utc_now()})
            try:
                save_report(args.output, report)
            except OSError:
                pass
        print(json.dumps({"status": "failed", "errorCode": "LIVE_EVALUATION_SETUP_FAILED"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
