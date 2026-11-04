"""用已知排序答案和真实本机 HTTP 验证评估器，不调用真实模型。"""
import contextlib
import io
import json
import math
import os
import secrets
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

from evaluation.export_corpus import export
from evaluation.runner import (EvaluationError, collect_http, evaluate, main,
                               normalize_response, read_jsonl, validate_dataset)

ROOT = Path(__file__).resolve().parents[2]


def corpus():
    return [
        {"id": "a", "sourceName": "a.docx", "tenantId": "scope-a", "knowledgeBase": "tech", "title": "A", "text": "Alpha"},
        {"id": "b", "sourceName": "b.docx", "tenantId": "scope-a", "knowledgeBase": "tech", "title": "B", "text": "Beta"},
        {"id": "c", "sourceName": "c.docx", "tenantId": "scope-a", "knowledgeBase": "tech", "title": "C", "text": "Gamma"},
        {"id": "private", "sourceName": "private.docx", "tenantId": "scope-b", "knowledgeBase": "tech", "title": "Private", "text": "Private"},
    ]


def question(identity="query", relevance=None):
    relevance = {"a": 2, "b": 1} if relevance is None else relevance
    return {"id": identity, "query": "synthetic question", "tenantId": "scope-a", "knowledgeBase": "tech",
            "relevance": relevance, "expectedEmpty": not relevance}


def prediction(sources, identity="query", **extra):
    return {"id": identity, "sources": [{"sourceId": source} for source in sources], **extra}


class EvaluationTests(unittest.TestCase):
    def test_known_ranking_metrics_keep_duplicate_positions(self):
        report = evaluate([question()], [prediction(["c", "a", "a", "b"])], corpus(), 3)
        self.assertEqual(report["recallAtK"], .5)
        self.assertEqual(report["mrrAtK"], .5)
        expected_ndcg = (3 / math.log2(3)) / (3 + 1 / math.log2(3))
        self.assertAlmostEqual(report["ndcgAtK"], expected_ndcg)
        self.assertEqual(report["falseNegativeCount"], 0)

    def test_all_relevant_results_have_perfect_scores(self):
        report = evaluate([question()], [prediction(["a", "b"])], corpus(), 6)
        self.assertEqual((report["recallAtK"], report["mrrAtK"], report["ndcgAtK"]), (1, 1, 1))

    def test_error_and_missing_responses_are_not_successful_empty_results(self):
        questions = [question("a", {}), question("b", {}), question("c", {})]
        predictions = [prediction([], "a", httpStatus=503), prediction([], "b")]
        report = evaluate(questions, predictions, corpus(), 3)
        self.assertEqual(report["errorCount"], 2)
        self.assertEqual(report["missingPredictionCount"], 1)
        self.assertEqual(report["correctEmptyCount"], 1)
        self.assertIsNone(report["mrrAtK"])

    def test_empty_positive_result_and_negative_false_positive_are_distinct(self):
        report = evaluate([question("a"), question("b", {})],
                          [prediction([], "a"), prediction(["a"], "b")], corpus(), 3)
        self.assertEqual(report["falseNegativeCount"], 1)
        self.assertEqual(report["falsePositiveCount"], 1)
        self.assertEqual(report["mrrAtK"], 0)

    def test_scope_leak_beyond_top_k_is_still_detected(self):
        report = evaluate([question()], [prediction(["a", "b", "private"])], corpus(), 2)
        self.assertEqual(report["recallAtK"], 1)
        self.assertEqual(report["scopeLeakCount"], 1)

    def test_payload_scope_mismatch_and_unknown_source_are_detected(self):
        data = {"id": "query", "sources": [{"sourceId": "a", "tenantId": "scope-b"}, {"sourceId": "unknown"}]}
        report = evaluate([question()], [data], corpus(), 3)
        self.assertEqual(report["scopeLeakCount"], 1)
        self.assertEqual(report["unknownSourceCount"], 1)

    def test_latency_uses_all_requests_and_interpolation(self):
        questions = [question(str(index)) for index in range(4)]
        predictions = [prediction(["a"], str(index), latencyMs=latency, httpStatus=503 if index == 3 else 200)
                       for index, latency in enumerate([100, 200, 300, 400])]
        report = evaluate(questions, predictions, corpus(), 3)
        self.assertEqual(report["latencyP50Ms"], 250)
        self.assertEqual(report["latencyP95Ms"], 385)
        self.assertEqual(report["errorCount"], 1)

    def test_java_and_engine_response_formats_resolve_same_source(self):
        sources = {"a.docx": "a"}
        java = {"success": True, "data": {"enabled": True, "chunks": [{"source": "a.docx / 第1页"}]}}
        engine = {"hits": [{"payload": {"source_name": "a.docx", "tenant_id": "scope-a", "knowledge_base": "tech"}}]}
        self.assertEqual(normalize_response(java, sources)[0]["sourceId"], "a")
        self.assertEqual(normalize_response(engine, sources)[0]["sourceId"], "a")

    def test_disabled_or_unavailable_cannot_pass_as_empty(self):
        for response in ({"enabled": False, "chunks": []}, {"chunks": [], "message": "RAG retrieval is temporarily unavailable"},
                         {"success": False, "data": {"chunks": []}}, {}):
            with self.subTest(response=response):
                report = evaluate([question(relevance={})], [{"id": "query", "response": response}], corpus(), 3)
                self.assertEqual(report["errorCount"], 1)
                self.assertEqual(report["correctEmptyCount"], 0)

    def test_malformed_normalized_result_is_counted_as_failure(self):
        report = evaluate([question()], [{"id": "query", "sources": ["malformed"]}], corpus(), 3)
        self.assertEqual(report["errorCount"], 1)
        self.assertEqual(report["recallAtK"], 0)

    def test_invalid_and_duplicate_inputs_fail(self):
        with self.assertRaises(EvaluationError):
            evaluate([question()], [prediction([]), prediction([])], corpus(), 3)
        with self.assertRaises(EvaluationError):
            evaluate([question()], [prediction([], identity="other")], corpus(), 3)
        with self.assertRaises(EvaluationError):
            evaluate([question()], [prediction([], latencyMs=float("nan"))], corpus(), 3)
        with self.assertRaises(EvaluationError):
            validate_dataset([question(relevance={"private": 1})], corpus())

    def test_committed_dataset_has_sixty_consistent_synthetic_questions(self):
        documents = read_jsonl(ROOT / "evaluation/corpus.jsonl")
        questions = read_jsonl(ROOT / "evaluation/questions.jsonl")
        validate_dataset(questions, documents)
        self.assertEqual(len(questions), 60)
        self.assertEqual(sum(bool(item["relevance"]) for item in questions), 52)
        self.assertTrue(all(document["synthetic"] for document in documents))
        self.assertEqual(sum(item["category"] == "tenant-isolation" for item in questions), 4)

    def test_docx_export_keeps_scope_and_source_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            export(corpus(), Path(directory))
            path = Path(directory) / "scope-a/tech/a.docx"
            with ZipFile(path) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
                self.assertIn("Alpha", "".join(root.itertext()))
                self.assertIn("[Content_Types].xml", archive.namelist())
            self.assertTrue((Path(directory) / "scope-b/tech/private.docx").is_file())
            evil = corpus()
            evil[0]["sourceName"] = "../outside.docx"
            with self.assertRaises(EvaluationError):
                export(evil, Path(directory))

    def test_cli_latency_gate_requires_every_question_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, data in (("corpus", corpus()), ("questions", [question()]),
                               ("predictions", [prediction(["a", "b"])])):
                (root / (name + ".jsonl")).write_text("".join(json.dumps(row) + "\n" for row in data))
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["--corpus", str(root / "corpus.jsonl"), "--dataset", str(root / "questions.jsonl"),
                             "--predictions", str(root / "predictions.jsonl"), "--output", str(root / "result.json"),
                             "--max-p95-ms", "5000"])
            self.assertEqual(code, 1)
            report = json.loads((root / "result.json").read_text())
            self.assertFalse(report["gate"]["checks"]["latencyBudget"])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.token = secrets.token_urlsafe(24)
        calls = self.calls

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, body, dict(self.headers)))
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/target")
                    self.end_headers()
                    return
                data = json.dumps({"hits": [{"payload": {"source_name": "a.docx", "tenant_id": "scope-a", "knowledge_base": "tech"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_real_http_collects_sanitized_predictions(self):
        rows = collect_http([question()], corpus(), self.endpoint + "/v1/retrievals", "engine", self.token, 6, 2)
        self.assertEqual(rows[0]["sources"][0]["sourceId"], "a")
        path, body, headers = self.calls[0]
        self.assertEqual(body["knowledgeBase"], "tech")
        self.assertEqual(headers["Authorization"], "Bearer " + self.token)
        self.assertEqual(headers["X-Tenant-Id"], "scope-a")
        serialized = json.dumps(rows)
        self.assertNotIn(self.token, serialized)
        self.assertNotIn("synthetic question", serialized)
        self.assertNotIn("source_name", serialized)

    def test_redirect_is_not_followed(self):
        rows = collect_http([question()], corpus(), self.endpoint + "/redirect", "engine", self.token, 6, 2)
        self.assertEqual(rows[0]["httpStatus"], 302)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(rows[0]["error"], "http_error")

    def test_token_in_endpoint_is_rejected_before_request(self):
        with self.assertRaises(EvaluationError):
            collect_http([question()], corpus(), self.endpoint + "/v1?token=example", "engine", self.token, 6, 2)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
