#!/usr/bin/env python3
"""启动本机真实引擎执行 Java 合同测试；不连接 Nacos、模型、OCR 或 Qdrant。"""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.request


def main():
    root = Path(__file__).resolve().parents[1]
    python = root / "engine/.venv/bin/python"
    if not python.exists():
        raise SystemExit("先在 engine/ 执行 uv sync --frozen")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    key = secrets.token_urlsafe(48)
    # 排除调用者可能已有的真实服务配置，测试只能使用合成文件。
    environment = {k: v for k, v in os.environ.items() if not k.startswith("RAG_ENGINE_")}
    environment.update(RAG_ENGINE_API_KEY=key, RAG_ENGINE_LIVE_KEY=key,
                       RAG_ENGINE_LIVE_URL=f"http://127.0.0.1:{port}", RAG_ENGINE_PARSE_WORKERS="1")
    log = root / "build/engine-contract/server.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as output:
        process = subprocess.Popen([str(python), "-m", "uvicorn", "rag_engine.app:create_app", "--factory",
                                    "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
                                   cwd=root / "engine", env=environment, stdout=output, stderr=output)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(100):
                if process.poll() is not None:
                    raise SystemExit("引擎启动失败，检查 build/engine-contract/server.log")
                try:
                    with opener.open(environment["RAG_ENGINE_LIVE_URL"] + "/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise SystemExit("引擎未就绪")
            result = subprocess.run([str(root / "gradlew"), "engineContractTest", "--no-daemon"],
                                    cwd=root, env=environment, check=False)
            raise SystemExit(result.returncode)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
