"""以显式相关性标注评估检索；保存结果不包含查询、正文或凭据。"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class EvaluationError(ValueError):
    """输入不完整或接口响应不符合评估契约。"""


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except (ValueError, TypeError) as error:
                raise EvaluationError(f"invalid JSONL at line {number}") from error
            if not isinstance(row, dict):
                raise EvaluationError(f"JSONL object required at line {number}")
            rows.append(row)
    if not rows:
        raise EvaluationError("empty input")
    return rows


def indexed(rows: list[dict], label: str) -> dict[str, dict]:
    result = {}
    for row in rows:
        identity = row.get("id")
        if not isinstance(identity, str) or not identity or identity in result:
            raise EvaluationError(f"missing or duplicate {label} id")
        result[identity] = row
    return result


def validate_dataset(questions: list[dict], corpus: list[dict]) -> dict[str, str]:
    documents = indexed(corpus, "corpus")
    indexed(questions, "question")
    source_map = {}
    for document in corpus:
        if not all(isinstance(document.get(key), str) and document[key].strip()
                   for key in ("tenantId", "knowledgeBase")):
            raise EvaluationError("corpus requires tenantId and knowledgeBase")
        name = document.get("sourceName")
        if not isinstance(name, str) or not name or name in source_map:
            raise EvaluationError("missing or duplicate corpus sourceName")
        source_map[name] = document["id"]
    for question in questions:
        if not all(isinstance(question.get(key), str) and question[key].strip()
                   for key in ("query", "tenantId", "knowledgeBase")):
            raise EvaluationError("question requires query, tenantId and knowledgeBase")
        relevance = question.get("relevance")
        if not isinstance(relevance, dict):
            raise EvaluationError("question relevance must be a mapping")
        if not isinstance(question.get("expectedEmpty"), bool):
            raise EvaluationError("question expectedEmpty must be boolean")
        if question["expectedEmpty"] != (not relevance):
            raise EvaluationError("expectedEmpty contradicts relevance")
        for identity, grade in relevance.items():
            if identity not in documents or type(grade) is not int or not 1 <= grade <= 3:
                raise EvaluationError("relevance contains unknown document or invalid grade")
            document = documents[identity]
            if (document["tenantId"], document["knowledgeBase"]) != (
                    question["tenantId"], question["knowledgeBase"]):
                raise EvaluationError("relevant document is outside the question scope")
        forbidden = question.get("forbiddenSources", [])
        if not isinstance(forbidden, list) or any(not isinstance(item, str) or item not in documents for item in forbidden):
            raise EvaluationError("invalid forbiddenSources")
        if set(forbidden) & set(relevance):
            raise EvaluationError("relevant source is also forbidden")
    return source_map


def normalize_response(response: dict, source_map: dict[str, str]) -> list[dict]:
    """兼容 Java ApiResponse/RPC DTO 与 Python 引擎；失败不能冒充正确空召回。"""
    if not isinstance(response, dict):
        raise EvaluationError("response must be an object")
    if "success" in response:
        if response["success"] is not True:
            raise EvaluationError("java application response failed")
        response = response.get("data")
        if not isinstance(response, dict):
            raise EvaluationError("java response data missing")
    if response.get("enabled") is False:
        raise EvaluationError("retrieval is disabled")
    if response.get("message") == "RAG retrieval is temporarily unavailable":
        raise EvaluationError("retrieval is unavailable")
    if "chunks" in response:
        rows = response["chunks"]
    elif "hits" in response:
        rows = response["hits"]
    else:
        raise EvaluationError("response has no chunks or hits")
    if not isinstance(rows, list):
        raise EvaluationError("retrieval items must be a list")
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            raise EvaluationError("invalid retrieval item")
        payload = row.get("payload", {})
        if not isinstance(payload, dict):
            raise EvaluationError("invalid hit payload")
        source_name = payload.get("source_name") or row.get("source", "")
        if not isinstance(source_name, str):
            raise EvaluationError("invalid source name")
        # Java 的 source 会附带“ / 段落/工作表位置”；按完整文件名匹配，不依赖运行时 UUID。
        source_name = source_name.split(" / ", 1)[0]
        identity = source_map.get(source_name, "__unknown_source__")
        normalized.append({
            "sourceId": identity,
            "tenantId": payload.get("tenant_id"),
            "knowledgeBase": payload.get("knowledge_base"),
        })
    return normalized


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def evaluate(questions: list[dict], predictions: list[dict], corpus: list[dict], k: int) -> dict:
    if k < 1:
        raise EvaluationError("k must be positive")
    source_map = validate_dataset(questions, corpus)
    predicted = indexed(predictions, "prediction")
    unknown_ids = set(predicted) - {row["id"] for row in questions}
    if unknown_ids:
        raise EvaluationError("predictions contain question ids outside the selected dataset")
    documents = indexed(corpus, "corpus")
    details, recalls, reciprocal_ranks, ndcgs, latencies = [], [], [], [], []
    failures = missing = false_positives = false_negatives = leaks = unknown_sources = 0
    negative_count = 0
    for question in questions:
        prediction = predicted.get(question["id"])
        failed = prediction is None
        missing += int(prediction is None)
        hits = []
        latency = None
        if prediction is not None:
            latency = prediction.get("latencyMs")
            if latency is not None:
                if type(latency) not in (int, float) or not math.isfinite(latency) or latency < 0:
                    raise EvaluationError("invalid latencyMs")
                latencies.append(float(latency))
            status = prediction.get("httpStatus", 200)
            if type(status) is not int:
                raise EvaluationError("httpStatus must be an integer")
            failed = bool(prediction.get("error")) or not 200 <= status < 300
            try:
                if "sources" in prediction:
                    hits = prediction["sources"]
                    if not isinstance(hits, list) or any(
                            not isinstance(hit, dict) or not isinstance(hit.get("sourceId"), str)
                            for hit in hits):
                        raise EvaluationError("invalid normalized sources")
                elif not failed:
                    hits = normalize_response(prediction.get("response"), source_map)
            except EvaluationError:
                failed = True
                hits = []
        failures += int(failed)
        # 即使响应失败，也检查已经保存的命中，避免错误状态遮住泄漏证据。
        forbidden = set(question.get("forbiddenSources", []))
        scope_leak = False
        for hit in hits:
            identity = hit["sourceId"]
            document = documents.get(identity)
            if document is None:
                unknown_sources += 1
            if identity in forbidden or (document is not None and (
                    document["tenantId"] != question["tenantId"]
                    or document["knowledgeBase"] != question["knowledgeBase"])):
                scope_leak = True
            if hit.get("tenantId") not in (None, question["tenantId"]):
                scope_leak = True
            if hit.get("knowledgeBase") not in (None, question["knowledgeBase"]):
                scope_leak = True
        leaks += int(scope_leak)
        selected = [] if failed else [hit["sourceId"] for hit in hits[:k]]
        relevance = question["relevance"]
        row = {"id": question["id"], "failed": failed, "scopeLeak": scope_leak,
               "retrievedSourceIds": [hit["sourceId"] for hit in hits], "latencyMs": latency}
        if relevance:
            found, dcg, rr = set(), 0.0, 0.0
            for rank, identity in enumerate(selected, 1):
                if identity in relevance and identity not in found:
                    found.add(identity)
                    dcg += (2 ** relevance[identity] - 1) / math.log2(rank + 1)
                    if not rr:
                        rr = 1.0 / rank
            ideal = sum((2 ** grade - 1) / math.log2(rank + 1)
                        for rank, grade in enumerate(sorted(relevance.values(), reverse=True)[:k], 1))
            recall, ndcg = len(found) / len(relevance), dcg / ideal
            recalls.append(recall)
            reciprocal_ranks.append(rr)
            ndcgs.append(ndcg)
            false_negatives += int(not found)
            row.update({"recall": recall, "reciprocalRank": rr, "ndcg": ndcg})
        else:
            negative_count += 1
            false_positives += int(bool(selected))
            row["correctEmpty"] = not failed and not hits
        details.append(row)
    return {
        "schemaVersion": 1, "k": k, "questionCount": len(questions),
        "positiveCount": len(recalls), "negativeCount": negative_count,
        "recallAtK": statistics.mean(recalls) if recalls else None,
        "mrrAtK": statistics.mean(reciprocal_ranks) if reciprocal_ranks else None,
        "ndcgAtK": statistics.mean(ndcgs) if ndcgs else None,
        "falseNegativeCount": false_negatives, "falsePositiveCount": false_positives,
        "correctEmptyCount": sum(row.get("correctEmpty", False) for row in details),
        "errorCount": failures, "missingPredictionCount": missing,
        "scopeLeakCount": leaks, "unknownSourceCount": unknown_sources,
        "latencySampleCount": len(latencies), "latencyP50Ms": percentile(latencies, .5),
        "latencyP95Ms": percentile(latencies, .95), "details": details,
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 不向另一主机转发评估账号 Token。
        return None


def collect_http(questions: list[dict], corpus: list[dict], endpoint: str,
                 api: str, token: str, k: int, timeout: float) -> list[dict]:
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username \
            or parsed.password or parsed.query or parsed.fragment:
        raise EvaluationError("endpoint must be an explicit HTTP(S) URL without credentials or query")
    if not token or "\n" in token or "\r" in token:
        raise EvaluationError("RAG_EVAL_TOKEN must be set to a valid token")
    source_map = validate_dataset(questions, corpus)
    opener = urllib.request.build_opener(NoRedirect())
    predictions = []
    for question in questions:
        body = {"query": question["query"], "topK": k,
                "collectionName" if api == "java" else "knowledgeBase": question["knowledgeBase"]}
        if api == "engine":
            body.update({"candidateCount": max(k, 30), "similarityThreshold": .2,
                         "maxContextCharacters": 12000})
        request = urllib.request.Request(endpoint, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": "Bearer " + token,
                                                  "X-Tenant-Id": question["tenantId"]})
        started = time.monotonic()
        row = {"id": question["id"], "sources": [], "httpStatus": 0}
        try:
            with opener.open(request, timeout=timeout) as response:
                row["httpStatus"] = response.status
                content = response.read(2 * 1024 * 1024 + 1)
                if len(content) > 2 * 1024 * 1024:
                    raise EvaluationError("response exceeds limit")
                row["sources"] = normalize_response(json.loads(content), source_map)
        except urllib.error.HTTPError as error:
            row["httpStatus"] = error.code
            row["error"] = "http_error"
            error.close()
        except (OSError, ValueError, urllib.error.URLError):
            row["error"] = "transport_or_contract_error"
        row["latencyMs"] = round((time.monotonic() - started) * 1000, 3)
        predictions.append(row)
    return predictions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/questions.jsonl"))
    parser.add_argument("--corpus", type=Path, default=Path("evaluation/corpus.jsonl"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--predictions", type=Path, help="离线 JSONL 响应，不发请求")
    mode.add_argument("--endpoint", help="显式执行检索请求，会调用配置的模型")
    parser.add_argument("--api", choices=("java", "engine"), default="java")
    parser.add_argument("--tenant", help="只评估指定测试租户；HTTP 模式必填")
    parser.add_argument("--save-predictions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--k", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--min-recall", type=float)
    parser.add_argument("--min-mrr", type=float)
    parser.add_argument("--max-p95-ms", type=float)
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.k <= 20 or not 0 < args.timeout <= 60:
            raise EvaluationError("k must be 1..20 and timeout must be 0..60 seconds")
        for value in (args.min_recall, args.min_mrr):
            if value is not None and not 0 <= value <= 1:
                raise EvaluationError("quality thresholds must be 0..1")
        if args.max_p95_ms is not None and (not math.isfinite(args.max_p95_ms) or args.max_p95_ms <= 0):
            raise EvaluationError("max-p95-ms must be finite and positive")
        if args.save_predictions and not args.endpoint:
            raise EvaluationError("save-predictions is only available in HTTP mode")
        corpus = read_jsonl(args.corpus)
        questions = read_jsonl(args.dataset)
        if args.tenant:
            questions = [question for question in questions if question.get("tenantId") == args.tenant]
        if not questions:
            raise EvaluationError("no questions selected")
        if args.endpoint:
            if not args.tenant:
                raise EvaluationError("HTTP mode requires --tenant matching a dedicated test account")
            predictions = collect_http(questions, corpus, args.endpoint, args.api,
                                       os.environ.get("RAG_EVAL_TOKEN", ""), args.k, args.timeout)
        else:
            predictions = read_jsonl(args.predictions)
        report = evaluate(questions, predictions, corpus, args.k)
        report["provenance"] = {
            "evaluatedAt": datetime.now(timezone.utc).isoformat(),
            "datasetSha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
            "corpusSha256": hashlib.sha256(args.corpus.read_bytes()).hexdigest(),
            "mode": "http-" + args.api if args.endpoint else "offline",
            "tenantFilter": args.tenant,
        }
        checks = {
            "completeResponses": report["errorCount"] == 0,
            "noScopeLeaks": report["scopeLeakCount"] == 0,
            "knownSources": report["unknownSourceCount"] == 0,
            "correctEmptyResponses": report["correctEmptyCount"] == report["negativeCount"],
        }
        for key, metric, limit in (("minimumRecall", "recallAtK", args.min_recall),
                                   ("minimumMrr", "mrrAtK", args.min_mrr)):
            if limit is not None:
                checks[key] = report[metric] is not None and report[metric] >= limit
        if args.max_p95_ms is not None:
            checks["latencyBudget"] = (report["latencySampleCount"] == report["questionCount"]
                                       and report["latencyP95Ms"] is not None
                                       and report["latencyP95Ms"] <= args.max_p95_ms)
        report["gate"] = {"passed": all(checks.values()), "checks": checks}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.save_predictions:
            args.save_predictions.parent.mkdir(parents=True, exist_ok=True)
            # HTTP 模式的规范化结果没有查询/正文；离线原文不重新导出。
            args.save_predictions.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                                     for row in predictions), encoding="utf-8")
        summary = {key: value for key, value in report.items() if key != "details"}
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if report["gate"]["passed"] else 1
    except (EvaluationError, OSError) as error:
        # 文件路径可含本机信息，错误只输出类型，不输出服务响应/正文/凭据。
        print(f"evaluation failed: {error if isinstance(error, EvaluationError) else type(error).__name__}", file=sys.stderr)
        return 2
