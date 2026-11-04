"""临时集合生命周期及真实 engine 管线的可控协议回归；绝不调用付费接口。"""
import argparse
import asyncio
import contextlib
import csv
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation import live_compare

ENGINE_AVAILABLE = importlib.util.find_spec("rag_engine") is not None


def arguments():
    return argparse.Namespace(k=2, candidates=5, threshold=.2)


class LiveExportTests(unittest.TestCase):
    def test_default_dry_run_does_not_load_settings_or_use_network(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            csv_path = Path(directory) / "dataset.csv"
            with (patch.object(live_compare, "load_settings", side_effect=AssertionError("network configuration")),
                  contextlib.redirect_stdout(io.StringIO()) as stdout):
                result = live_compare.main(["--output", str(output), "--langfuse-csv", str(csv_path)])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(stdout.getvalue())["networkRequests"], 0)
            report = json.loads(output.read_text())
            self.assertEqual(report["setup"]["documentEmbeddingBatches"], 3)
            self.assertFalse(report["cleanup"]["required"])
            self.assertNotIn("query", report)
            with csv_path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 60)
            self.assertEqual(json.loads(rows[0]["metadata"])["questionId"], "q001")
            self.assertEqual(json.loads(rows[0]["expected_output"])["relevance"], {"cache-v1": 2})
            self.assertEqual(json.loads(rows[0]["input"])["tenantId"], "eval-a")

    def test_mixed_failure_and_negative_does_not_pass_comparison(self):
        corpus, questions = live_compare.load_fixture()
        negative = next(question for question in questions if question["expectedEmpty"])
        predictions = {"dense": [{"id": negative["id"], "sources": [], "latencyMs": 3001,
                                  "error": "RETRIEVAL_TIMEOUT"}],
                       "hybrid": [{"id": negative["id"], "sources": [], "latencyMs": 20,
                                   "mode": "hybrid", "degraded": True}]}
        result = live_compare.comparison_report([negative], corpus, predictions, {"topK": 2})
        self.assertEqual(result["results"]["dense"]["correctEmptyCount"], 0)
        self.assertEqual(result["results"]["dense"]["timeoutCount"], 1)
        self.assertFalse(result["acceptance"]["checks"]["allReranksCompleted"])

    def test_score_distribution_does_not_mix_empty_errors_or_degraded_scales(self):
        _, questions = live_compare.load_fixture()
        selected = questions[:3] + [row for row in questions if row["expectedEmpty"]][:3]
        predictions = [
            {"id": selected[0]["id"], "sources": [{"score": .4}, {"score": .99}], "degraded": False},
            {"id": selected[1]["id"], "sources": [{"score": .8}], "degraded": False},
            {"id": selected[2]["id"], "sources": [{"score": .03}], "degraded": True},
            {"id": selected[3]["id"], "sources": [], "degraded": False},
            {"id": selected[4]["id"], "sources": [], "error": "RETRIEVAL_TIMEOUT"},
            {"id": selected[5]["id"], "sources": [{"sourceId": "legacy-no-score"}]},
        ]
        groups = live_compare.top_score_distribution(selected, predictions)["groups"]
        self.assertEqual(groups["positive"]["nonDegraded"]["sampleCount"], 2)
        self.assertAlmostEqual(groups["positive"]["nonDegraded"]["p50"], .6)
        self.assertAlmostEqual(groups["positive"]["nonDegraded"]["p95"], .78)
        self.assertEqual(groups["positive"]["nonDegraded"]["max"], .8)
        self.assertEqual(groups["positive"]["degraded"]["sampleCount"], 1)
        self.assertEqual(groups["negative"]["emptyResponseCount"], 1)
        self.assertEqual(groups["negative"]["errorCount"], 1)
        self.assertEqual(groups["negative"]["missingScoreCount"], 1)
        self.assertIsNone(groups["negative"]["nonDegraded"]["min"])


@unittest.skipUnless(ENGINE_AVAILABLE, "live protocol tests require the existing engine/.venv")
class LiveProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        from rag_engine.config import Settings

        self.httpx = httpx
        self.corpus, self.questions = live_compare.load_fixture()
        self.questions = self.questions[:2]
        self.report = live_compare.new_plan(self.corpus, self.questions, arguments())
        self.settings = Settings(api_key="test-only-service-key-never-used-on-network", embedding_dimension=3,
                                 qdrant_url="http://qdrant.test", qwen_base_url="https://qwen.test/v1",
                                 qwen_api_key="test-only-model-secret", collection=self.report["temporaryCollection"])
        self.exists = False
        self.points = []
        self.calls = []
        self.embedding_inputs = []
        self.inject_failure = False
        self.inject_collision = False
        self.cleanup_stuck = False
        self.slow_embedding = False
        self.cancel_query = False
        self.lost_create_response = False
        self.foreign_hit = False
        self.directory = tempfile.TemporaryDirectory()
        self.output = Path(self.directory.name) / "report.json"

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def handler(self, request):
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        body = json.loads(request.content) if request.content else {}
        base = "/collections/" + self.settings.collection
        if request.url.host == "qwen.test":
            self.assertEqual(request.headers["Authorization"], "Bearer test-only-model-secret")
            if path.endswith("/embeddings"):
                self.embedding_inputs.extend(body["input"])
                if self.cancel_query and len(body["input"]) == 1:
                    raise asyncio.CancelledError()
                if self.slow_embedding and len(body["input"]) == 1:
                    await asyncio.sleep(.3)
                # API 允许乱序 batch，脚本必须按 index 对齐，不能按响应数组顺序猜。
                return self.httpx.Response(200, json={"data": [
                    {"index": index, "embedding": [1., float(index + 1), 0.]}
                    for index in reversed(range(len(body["input"])))
                ]})
            if path.endswith("/reranks"):
                return self.httpx.Response(200, json={"results": [
                    {"index": index, "relevance_score": .9 - index * .1}
                    for index in range(min(body["top_n"], len(body["documents"])))
                ]})
        # 所有 Qdrant 请求必须只落到本次唯一名称，不访问 ai_chat_files / 其他 collection。
        self.assertEqual(request.url.host, "qdrant.test")
        self.assertTrue(path == base or path.startswith(base + "/"))
        if path == base:
            if method == "GET":
                if not self.exists and not self.inject_collision:
                    return self.httpx.Response(404)
                return self.httpx.Response(200, json={"result": {"config": {"params": {
                    "vectors": {"size": 3, "distance": "Cosine"},
                    "sparse_vectors": {"lexical-v1": {"modifier": "idf"}},
                }}}})
            if method == "PUT":
                self.assertFalse(self.exists)
                self.exists = True
                if self.lost_create_response:
                    raise self.httpx.ReadError("synthetic response lost")
                return self.httpx.Response(200, json={"result": True})
            if method == "DELETE":
                if not self.cleanup_stuck:
                    self.exists = False
                return self.httpx.Response(200)
        if path == base + "/points" and method == "PUT":
            if self.inject_failure:
                return self.httpx.Response(503, text="private upstream error test-only-model-secret")
            self.points = body["points"]
            return self.httpx.Response(200, json={"result": {"status": "completed"}})
        if path == base + "/points/count":
            query_filter = body["filter"]
            count = 0 if "must_not" in query_filter else len(self.scoped_points(query_filter))
            return self.httpx.Response(200, json={"result": {"count": count}})
        if path == base + "/points/search":
            rows = self.scoped_points(body["filter"])
            if self.foreign_hit:
                rows = [point for point in self.points if point["payload"]["tenant_id"] == "eval-b"]
            return self.httpx.Response(200, json={"result": [
                {"id": point["id"], "score": .9 - index * .1, "payload": point["payload"]}
                for index, point in enumerate(rows[:body["limit"]])
            ]})
        raise AssertionError("unexpected synthetic transport route")

    def scoped_points(self, query_filter):
        return [point for point in self.points if all(
            point["payload"].get(item["key"]) == item["match"]["value"] for item in query_filter["must"])]

    async def execute(self):
        return await live_compare.execute_comparison(self.settings, self.corpus, self.questions, self.report,
                                                     self.output, self.httpx.MockTransport(self.handler))

    async def test_shared_embeddings_real_engine_modes_and_verified_cleanup(self):
        report = await self.execute()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["configuration"]["rerankScoreThreshold"], self.settings.rerank_score_threshold)
        self.assertTrue(report["cleanup"]["verifiedAbsent"])
        self.assertEqual(report["cleanup"]["readbackStatus"], 404)
        self.assertEqual(len(self.embedding_inputs), 28 + 2 * 2)
        self.assertEqual(self.embedding_inputs[:28], [row["text"] for row in self.corpus])
        self.assertEqual(self.points[0]["vector"][""], [1., 1., 0.])
        self.assertEqual(self.points[9]["vector"][""], [1., 10., 0.])
        self.assertEqual(report["setup"]["readbackCounts"], {"eval-a/synthetic-tech": 26, "eval-b/synthetic-tech": 2})
        self.assertEqual(report["results"]["dense"]["recallAtK"], 1)
        self.assertEqual(report["predictions"]["dense"][0]["sources"][0]["score"], .9)
        self.assertEqual(report["predictions"]["dense"][0]["sources"][1]["score"], .8)
        self.assertEqual(report["results"]["dense"]["topScoreDistribution"]["groups"]
                         ["positive"]["nonDegraded"]["sampleCount"], 2)
        self.assertEqual(report["results"]["hybrid"]["scopeLeakCount"], 0)
        self.assertTrue(report["acceptance"]["checks"]["expectedRetrievalMode"])
        encoded = self.output.read_text()
        for forbidden in ("test-only-model-secret", "http://qdrant.test", self.questions[0]["query"], self.corpus[0]["text"]):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(self.calls[-2:], [("DELETE", "/collections/" + self.settings.collection),
                                          ("GET", "/collections/" + self.settings.collection)])

    async def test_write_failure_still_cleans_up_without_returning_upstream_body(self):
        self.inject_failure = True
        report = await self.execute()
        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["cleanup"]["verifiedAbsent"])
        self.assertEqual(report["results"]["dense"]["missingPredictionCount"], 2)
        self.assertNotIn("private upstream", self.output.read_text())

    async def test_lost_creation_response_still_cleans_unique_collection(self):
        self.lost_create_response = True
        report = await self.execute()
        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["cleanup"]["verifiedAbsent"])

    async def test_cancellation_records_interruption_and_cleans_unique_collection(self):
        self.cancel_query = True
        report = await self.execute()
        self.assertEqual(report["errorCode"], "INTERRUPTED")
        self.assertTrue(report["cleanup"]["verifiedAbsent"])

    async def test_preexisting_collection_is_never_written_or_deleted(self):
        self.inject_collision = True
        report = await self.execute()
        self.assertEqual(report["errorCode"], "TEMPORARY_COLLECTION_NOT_ABSENT")
        self.assertFalse(report["cleanup"]["required"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.embedding_inputs, [])

    async def test_delete_success_without_404_is_not_successful_cleanup(self):
        self.cleanup_stuck = True
        report = await self.execute()
        self.assertEqual(report["cleanup"]["readbackStatus"], 200)
        self.assertFalse(report["cleanup"]["verifiedAbsent"])
        self.assertFalse(report["acceptance"]["passed"])

    async def test_real_pipeline_timeout_is_counted_and_cleanup_runs(self):
        self.slow_embedding = True
        self.settings.retrieval_timeout_seconds = .1
        report = await self.execute()
        self.assertEqual(report["results"]["dense"]["timeoutCount"], 2)
        self.assertEqual(report["results"]["hybrid"]["timeoutCount"], 2)
        self.assertTrue(report["cleanup"]["verifiedAbsent"])

    async def test_foreign_hit_is_rejected_by_real_engine_not_called_correct_empty(self):
        self.foreign_hit = True
        report = await self.execute()
        self.assertEqual(report["results"]["dense"]["errorCodes"], {"TENANT_ISOLATION_VIOLATION": 2})
        self.assertEqual(report["results"]["hybrid"]["errorCount"], 2)
        self.assertFalse(report["acceptance"]["checks"]["completeResponses"])
        self.assertTrue(report["cleanup"]["verifiedAbsent"])

    async def test_business_collection_name_is_rejected_before_network(self):
        self.settings.collection = "ai_chat_files"
        with self.assertRaises(live_compare.LiveError):
            await self.execute()
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
