#!/usr/bin/env python3
"""启动本机真实引擎执行 Java 合同测试；不连接 Nacos、Keycloak、模型、OCR 或 Qdrant。

引擎只收 Keycloak 签的令牌，这里先用引擎虚拟环境起合成签发方（engine/scripts/synthetic_issuer.py）：
它在本机回环上提供 JWKS，并签一枚受众 github-rag-engine、azp github-rag 的 RS256 令牌；
引擎按这个签发方真实取公钥、验签名，Java 合同测试从 RAG_ENGINE_LIVE_TOKEN 取这枚令牌调用。
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request


def stop(process, close_stdin=False):
    if close_stdin and process.stdin:
        process.stdin.close()
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main():
    root = Path(__file__).resolve().parents[1]
    python = root / "engine/.venv/bin/python"
    if not python.exists():
        raise SystemExit("先在 engine/ 执行 uv sync --frozen")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    # 合成签发方一直服务到标准输入关闭；第一行是签发方地址、公钥地址与令牌。
    issuer = subprocess.Popen([str(python), "-I", str(root / "engine/scripts/synthetic_issuer.py")],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert issuer.stdout is not None
        line = issuer.stdout.readline()
        if not line:
            raise SystemExit("合成签发方启动失败")
        synthetic = json.loads(line)
        # 排除调用者可能已有的真实服务配置，测试只能使用合成文件与合成签发方。
        environment = {k: v for k, v in os.environ.items() if not k.startswith("RAG_ENGINE_")}
        environment.update(RAG_ENGINE_KEYCLOAK_ISSUER=synthetic["issuer"], RAG_ENGINE_JWKS_URL=synthetic["jwks_url"],
                           RAG_ENGINE_LIVE_TOKEN=synthetic["token"], RAG_ENGINE_LIVE_URL=f"http://127.0.0.1:{port}",
                           RAG_ENGINE_PARSE_WORKERS="1")
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
                stop(process)
    finally:
        stop(issuer, close_stdin=True)


if __name__ == "__main__":
    main()
