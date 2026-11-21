"""合成的 Keycloak 签发方，只给测试与验收用。

RSA 密钥在本进程现场生成、不落盘；公钥（JWKS）经本机回环上的真实 HTTP 提供，
与引擎在 NAS 上取 Keycloak 公钥走同一条路径。令牌用 PyJWT 按 RS256 签名，
声明与 Keycloak 客户端凭证令牌同形（iss、aud、azp、exp）。
这些令牌只对指向本签发方 JWKS 的引擎有效，真实部署只认 Keycloak 的公钥。

命令行运行时打印一行 JSON（issuer、jwks_url、token），一直服务到标准输入关闭，
供 Java 对真实引擎的合同测试使用。
"""

import json
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# 保留域名（RFC 2606），解析不到任何真实主机；引擎只拿它与令牌的 iss 比对，不会访问。
ISSUER = "https://keycloak.invalid/realms/github-dev"
AUDIENCE = "github-rag-engine"


class SyntheticIssuer:
    """本机回环上的签发方：/certs 返回 JWKS，/moved 返回指向 /certs 的 302（验证取公钥不跟随跳转）。"""

    def __init__(self, port: int = 0, issuer: str = ISSUER):
        self.issuer = issuer
        self.kid = secrets.token_hex(8)
        self.fetches = 0
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self._key.public_key()))
        jwk.update(kid=self.kid, use="sig", alg="RS256")
        body = json.dumps({"keys": [jwk]}).encode()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/certs":
                    owner.fetches += 1
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/moved":
                    self.send_response(302)
                    self.send_header("Location", "/certs")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self.send_error(404)

            def log_message(self, format, *args):
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.jwks_url = f"http://127.0.0.1:{self._server.server_address[1]}/certs"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "SyntheticIssuer":
        self._thread.start()
        return self

    def __exit__(self, *_exception):
        self._server.shutdown()
        self._server.server_close()

    @property
    def public_pem(self) -> bytes:
        """公钥的 PEM 文本：算法混淆攻击正是拿它当 HMAC 密钥签 HS256。"""
        return self._key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    def token(
        self, azp: str = "github-rag", *, audience: str | list[str] = AUDIENCE, lifetime: int = 300, **claims
    ) -> str:
        """签一个客户端凭证令牌；claims 覆盖或追加声明（如 iss），lifetime 为负即已过期。"""
        now = int(time.time())
        payload = {
            "iss": self.issuer,
            "aud": audience,
            "azp": azp,
            "sub": "service-account-" + azp,
            "typ": "Bearer",
            "iat": now,
            "exp": now + lifetime,
            "jti": secrets.token_hex(8),
            **claims,
        }
        return self.sign(payload)

    def sign(self, claims: dict, *, kid: str | None = None) -> str:
        """用本签发方的私钥按 RS256 签任意声明（造缺声明的令牌）；kid 缺省为本签发方的。"""
        return jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": kid or self.kid})


def main():
    with SyntheticIssuer() as issuer:
        print(
            json.dumps(
                {
                    "issuer": issuer.issuer,
                    "jwks_url": issuer.jwks_url,
                    "token": issuer.token("github-rag", lifetime=3600),
                }
            ),
            flush=True,
        )
        # 父进程关闭标准输入即退出。
        sys.stdin.read()


if __name__ == "__main__":
    main()
