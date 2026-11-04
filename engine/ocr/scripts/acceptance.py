"""以实际模型验证合成中文 PNG 和扫描 PDF；可对已部署的私有 OCR 地址执行。"""

import argparse
import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="私有 OCR 根地址；不传则启动本机真实服务")
    parser.add_argument("--output", default=".acceptance/report.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    process = None
    peak = [0]
    stop = threading.Event()
    key = os.environ.get("RAG_OCR_API_KEY", "")
    url = args.url
    if not url:
        # 只有启动本机进程时采样 RSS；容器 --url 验收不依赖开发工具。
        import psutil

        key = secrets.token_urlsafe(48)
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            port = server.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        environment = {
            **os.environ,
            "RAG_OCR_API_KEY": key,
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "ocr_app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ],
            cwd=root,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        def sample():
            while not stop.wait(0.02):
                try:
                    parent = psutil.Process(process.pid)
                    rss = parent.memory_info().rss
                    for child in parent.children(recursive=True):
                        try:
                            rss += child.memory_info().rss
                        except psutil.Error:
                            pass
                    peak[0] = max(peak[0], rss)
                except psutil.Error:
                    pass

        threading.Thread(target=sample, daemon=True).start()
    if len(key) < 32:
        raise ValueError("RAG_OCR_API_KEY must be injected for the deployed endpoint")
    report = {"syntheticOnly": True, "cases": []}
    try:
        with httpx.Client(base_url=url, timeout=60, trust_env=False) as client:
            for _ in range(100):
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if process and process.poll() is not None:
                    raise RuntimeError("OCR process exited before readiness")
                time.sleep(0.1)
            assert client.post("/layout-parsing", json={}).status_code == 401
            for name, kind in [("chinese-text.png", 1), ("chinese-scan.pdf", 0)]:
                started = time.monotonic()
                response = client.post(
                    "/layout-parsing",
                    headers={"Authorization": "Bearer " + key},
                    json={
                        "file": base64.b64encode(
                            (root / "tests/fixtures" / name).read_bytes()
                        ).decode(),
                        "fileType": kind,
                        "visualize": False,
                    },
                )
                assert response.status_code == 200, (name, response.status_code, response.text)
                result = response.json()["result"]
                assert result["capabilities"] == {
                    "textRecognition": True,
                    "tableStructure": False,
                    "layoutAnalysis": False,
                }
                pages = result["layoutParsingResults"]
                assert len(pages) == 1
                blocks = pages[0]["prunedResult"]["parsing_res_list"]
                text = "\n".join(block["block_content"] for block in blocks)
                for expected in [
                    "知识库升级验收",
                    "来源页码",
                    "中文识别",
                    "扫描文件",
                    "12",
                    "8",
                    "正常",
                ]:
                    assert expected in text, (name, expected, text)
                assert all(block["block_label"] == "ocr-text" for block in blocks)
                assert all(len(block["block_bbox"]) == 4 for block in blocks)
                case = {
                    "fixture": name,
                    "elapsedSeconds": round(time.monotonic() - started, 3),
                    "recognizedText": text,
                    "blockCount": len(blocks),
                    "passed": True,
                }
                report["cases"].append(case)
                print(json.dumps(case, ensure_ascii=False), flush=True)
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            stop.set()
        report["peakLocalRssMiB"] = round(peak[0] / 1024**2, 2) if process else None
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print("peakLocalRssMiB", report["peakLocalRssMiB"])


if __name__ == "__main__":
    main()
