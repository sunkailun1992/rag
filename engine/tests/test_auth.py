"""引擎只认 Keycloak 签给自己的客户端凭证令牌，再按路径核对调用方。

令牌必须是 RS256，签发方等于配置的 Keycloak 地址，受众含 github-rag-engine，未过期；
/v1/** 只给 rag（azp github-rag），/metrics 只给 Prometheus（azp github-prometheus）。
签发方是本机回环上的合成 Keycloak（scripts/synthetic_issuer.py）：公钥经真实 HTTP 取得，
签名与校验都走 PyJWT；每个用例先用一枚有效令牌确认通路，再看被改坏的那一枚被拒。
"""

import base64
import hashlib
import hmac
import json
import logging
import socket

import jwt
from conftest import ISSUER
from fastapi.testclient import TestClient
from synthetic_issuer import SyntheticIssuer

from rag_engine.app import create_app
from rag_engine.observability import SAFE_TEMPLATES

INVALID = 'Bearer error="invalid_token"'
INSUFFICIENT = 'Bearer error="insufficient_scope"'


def call(client, token, path="/v1/openapi.json", scheme="Bearer"):
    headers = {"X-Tenant-Id": "tenant-a"} if path.startswith("/v1/") else {}
    if token is not None:
        headers["Authorization"] = scheme + " " + token
    return client.get(path, headers=headers)


def segment(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def without(claim: str) -> dict:
    claims = jwt.decode(ISSUER.token(), options={"verify_signature": False})
    claims.pop(claim)
    return claims


def assert_rejected(response, status=401, challenge=INVALID):
    assert response.status_code == status
    assert response.json() == {"error": {"code": "UNAUTHORIZED" if status == 401 else "FORBIDDEN"}}
    assert response.headers["WWW-Authenticate"] == challenge
    assert response.headers["Cache-Control"] == "no-store"


def test_valid_rag_token_allowed(settings):
    with TestClient(create_app(settings)) as client:
        response = call(client, ISSUER.token("github-rag"))
        # RFC 7235：认证方案名不区分大小写。
        lower = call(client, ISSUER.token(), scheme="bearer")
    assert response.status_code == 200
    assert lower.status_code == 200


def test_missing_token_gets_bare_bearer_challenge(settings):
    with TestClient(create_app(settings)) as client:
        assert call(client, ISSUER.token()).status_code == 200
        missing = call(client, None)
        malformed = call(client, "dXNlcjpwYXNz", scheme="Basic")
    # RFC 6750 §3.1：请求没带令牌时不给错误码，只给认证方案。
    assert_rejected(missing, challenge="Bearer")
    assert_rejected(malformed, challenge="Bearer")


def test_wrong_audience_rejected(settings):
    with TestClient(create_app(settings)) as client:
        assert call(client, ISSUER.token()).status_code == 200
        # 网关转给业务后端的用户令牌受众是 github-api，不能拿来调引擎。
        assert_rejected(call(client, ISSUER.token(audience="github-api")))
        assert_rejected(call(client, ISSUER.token(audience=["github-api", "account"])))


def test_wrong_issuer_rejected(settings):
    with TestClient(create_app(settings)) as client:
        assert call(client, ISSUER.token()).status_code == 200
        assert_rejected(call(client, ISSUER.token(iss="https://keycloak.invalid/realms/master")))
        assert_rejected(call(client, ISSUER.token(iss=ISSUER.issuer + "/")))


def test_wrong_azp_rejected(settings, caplog):
    with caplog.at_level(logging.WARNING, logger="rag_engine"), TestClient(create_app(settings)) as client:
        assert call(client, ISSUER.token()).status_code == 200
        # 签名、签发方、受众都对，只是调用方不是 rag：令牌有效但无权调用，403。
        assert_rejected(call(client, ISSUER.token("github-gateway")), 403, INSUFFICIENT)
        assert_rejected(call(client, ISSUER.sign(without("azp"))), 403, INSUFFICIENT)
    assert "rag_engine_auth_failed code=CLIENT_NOT_ALLOWED" in caplog.text
    assert "rag_engine_auth_failed code=CLIENT_NOT_ALLOWED" in SAFE_TEMPLATES


def test_expired_rejected(settings, caplog):
    with caplog.at_level(logging.WARNING, logger="rag_engine"), TestClient(create_app(settings)) as client:
        assert call(client, ISSUER.token()).status_code == 200
        # 允许 60 秒时钟偏差（与 Spring Security 的 JwtTimestampValidator 一致），过期两分钟必拒。
        assert call(client, ISSUER.token(lifetime=-30)).status_code == 200
        assert_rejected(call(client, ISSUER.token(lifetime=-120)))
        assert_rejected(call(client, ISSUER.sign(without("exp"))))
    assert "rag_engine_auth_failed code=TOKEN_INVALID" in caplog.text
    assert "rag_engine_auth_failed code=TOKEN_INVALID" in SAFE_TEMPLATES


def test_hs256_and_none_rejected(settings):
    claims = jwt.decode(ISSUER.token(), options={"verify_signature": False})
    hs_header = segment({"alg": "HS256", "typ": "JWT", "kid": ISSUER.kid})
    signing_input = hs_header + "." + segment(claims)
    # 算法混淆：拿公开的 RSA 公钥当 HMAC 密钥签名。
    digest = hmac.new(ISSUER.public_pem, signing_input.encode(), hashlib.sha256).digest()
    hs256 = signing_input + "." + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    unsigned = segment({"alg": "none", "typ": "JWT"}) + "." + segment(claims) + "."
    with TestClient(create_app(settings)) as client:
        assert call(client, ISSUER.token()).status_code == 200
        assert_rejected(call(client, hs256))
        assert_rejected(call(client, unsigned))


def test_foreign_key_signature_rejected(settings):
    with SyntheticIssuer() as stranger:
        forged = stranger.token(iss=ISSUER.issuer)
        # 换成本签发方的 kid：取到的是真公钥，签名对不上。
        same_kid = stranger.sign(jwt.decode(forged, options={"verify_signature": False}), kid=ISSUER.kid)
    with TestClient(create_app(settings)) as client:
        assert call(client, ISSUER.token()).status_code == 200
        assert_rejected(call(client, forged))
        assert_rejected(call(client, same_kid))


def test_prometheus_token_only_for_metrics(settings):
    with TestClient(create_app(settings)) as client:
        scrape = call(client, ISSUER.token("github-prometheus"), "/metrics")
        assert scrape.status_code == 200
        assert scrape.headers["Content-Type"].startswith("text/plain")
        assert_rejected(call(client, ISSUER.token("github-rag"), "/metrics"), 403, INSUFFICIENT)
        assert_rejected(call(client, ISSUER.token("github-prometheus")), 403, INSUFFICIENT)
        assert_rejected(call(client, None, "/metrics"), challenge="Bearer")


def test_jwks_unreachable_returns_401_not_500(settings, caplog):
    unreachable = settings.model_copy(update={"jwks_url": f"http://127.0.0.1:{closed_port()}/certs"})
    with caplog.at_level(logging.WARNING, logger="rag_engine"), TestClient(create_app(unreachable)) as client:
        response = call(client, ISSUER.token())
    assert_rejected(response)
    assert "rag_engine_auth_failed code=JWKS_UNAVAILABLE" in caplog.text
    assert "rag_engine_auth_failed code=JWKS_UNAVAILABLE" in SAFE_TEMPLATES


def test_jwks_redirect_is_not_followed(settings, caplog):
    # 公钥地址只由运维配置；跳转可能把取公钥引向别处，按取不到处理。
    moved = settings.model_copy(update={"jwks_url": ISSUER.jwks_url.replace("/certs", "/moved")})
    with caplog.at_level(logging.WARNING, logger="rag_engine"), TestClient(create_app(moved)) as client:
        assert_rejected(call(client, ISSUER.token()))
    assert "rag_engine_auth_failed code=JWKS_UNAVAILABLE" in caplog.text


def test_jwks_fetch_ignores_proxy_environment(settings, monkeypatch):
    # 与引擎其他出站一致（httpx trust_env=False）：取公钥直连，不经环境变量里的代理。
    dead_proxy = f"http://127.0.0.1:{closed_port()}"
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, dead_proxy)
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.setenv(name, "")
    with TestClient(create_app(settings)) as client:
        assert call(client, ISSUER.token()).status_code == 200
