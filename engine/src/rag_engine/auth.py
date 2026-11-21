"""Keycloak 访问令牌校验：引擎只认 Keycloak 签给自己（受众 github-rag-engine）的客户端凭证令牌。

依据 RFC 8725（JWT 最佳实践）与 RFC 9068（JWT 访问令牌），校验项与 Spring Security 资源服务器一致：
只收 RS256（不认 HS*、none，杜绝算法混淆）；签发方与配置逐字相等；受众含 github-rag-engine；
必须带 exp，允许 60 秒时钟偏差；最后按路径核对 azp（令牌签给了哪个客户端）。
公钥用 PyJWT 的 PyJWKClient 取并缓存 5 分钟；遇到未知 kid 才强制刷新，两次强制刷新至少隔 30 秒，
伪造 kid 打不穿 Keycloak。任何一项不过都拒绝：令牌无效 401，令牌有效但调用方不对 403。
"""

import asyncio
import enum
import http.client
import json
import logging
import re
import urllib.request
from urllib.error import HTTPError

import jwt

logger = logging.getLogger("rag_engine")

AUDIENCE = "github-rag-engine"
API_CLIENT = "github-rag"
METRICS_CLIENT = "github-prometheus"
ALGORITHMS = ["RS256"]
# 与 Spring Security JwtTimestampValidator 的默认时钟偏差一致。
LEEWAY_SECONDS = 60
# 公钥在编排内网，正常毫秒级；取不到时快速失败，不占满检索的 3 秒预算。
JWKS_TIMEOUT_SECONDS = 2
# 与 Nimbus（Spring Security 取公钥用的库）默认的 JWKS 响应上限一致。
JWKS_MAX_BYTES = 51200
# 与 Spring Security DefaultBearerTokenResolver 相同：方案名不区分大小写，令牌只许 RFC 6750 b64token 字符。
BEARER = re.compile(rb"bearer (?P<token>[A-Za-z0-9\-._~+/]+=*)", re.IGNORECASE)


class Verdict(enum.Enum):
    VALID = "valid"
    # 没带 Bearer 令牌：401，按 RFC 6750 §3.1 只给认证方案、不给错误码。
    MISSING = "missing"
    # 令牌无效（格式、签名、算法、签发方、受众、有效期）或取不到公钥：401 invalid_token。
    INVALID = "invalid"
    # 令牌有效，但不是这个路径允许的客户端：403 insufficient_scope。
    FORBIDDEN = "forbidden"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_arguments, **_options):
        return None


class DirectJWKClient(jwt.PyJWKClient):
    """取公钥只直连运维配置的地址：不读环境变量里的代理（与引擎其他出站的 httpx trust_env=False 一致），
    不跟随跳转，响应超过上限按取不到处理。缓存、未知 kid 刷新与冷却仍由 PyJWKClient 负责，
    它注明 fetch_data 可由子类覆盖。"""

    def fetch_data(self):
        request = urllib.request.Request(self.uri, headers=self.headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=self.timeout) as response:
                body = response.read(JWKS_MAX_BYTES + 1)
            if len(body) > JWKS_MAX_BYTES:
                raise ValueError("JWKS response too large")
            return json.loads(body)
        except (OSError, http.client.HTTPException, ValueError) as error:
            if isinstance(error, HTTPError):
                error.close()
            raise jwt.PyJWKClientConnectionError("JWKS unavailable") from error


class TokenVerifier:
    """校验 Authorization 头里的 Bearer 令牌；签名与公钥查询在线程里做，不阻塞事件循环。"""

    def __init__(self, issuer: str, jwks_url: str):
        self.issuer = issuer
        self.keys = DirectJWKClient(jwks_url, cache_jwk_set=True, lifespan=300, timeout=JWKS_TIMEOUT_SECONDS)

    async def verify(self, authorization: list[bytes], client: str) -> Verdict:
        """authorization 为请求里全部 Authorization 头的值，client 为该路径允许的 azp。"""
        if not authorization:
            return Verdict.MISSING
        if len(authorization) > 1:
            return rejected("TOKEN_INVALID")
        # 不是 Bearer 方案（如 Basic）按没带令牌处理；是 Bearer 但格式不对按无效令牌（与 Spring 相同）。
        if authorization[0][:6].lower() != b"bearer":
            return Verdict.MISSING
        match = BEARER.fullmatch(authorization[0])
        if match is None:
            return rejected("TOKEN_INVALID")
        return await asyncio.to_thread(self._check, match.group("token").decode("ascii"), client)

    def _check(self, token: str, client: str) -> Verdict:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return rejected("TOKEN_INVALID")
        # 先挡住 HS*、none：既不按它们验签，也不拿它们去查公钥。
        if header.get("alg") not in ALGORITHMS:
            return rejected("TOKEN_INVALID")
        try:
            key = self.keys.get_signing_key(header.get("kid"))
        except jwt.PyJWKClientConnectionError:
            return rejected("JWKS_UNAVAILABLE")
        except jwt.PyJWTError:
            return rejected("TOKEN_INVALID")
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=ALGORITHMS,
                audience=AUDIENCE,
                issuer=self.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError:
            return rejected("TOKEN_INVALID")
        if claims.get("azp") != client:
            logger.warning(DIAGNOSTICS["CLIENT_NOT_ALLOWED"])
            return Verdict.FORBIDDEN
        return Verdict.VALID


# 固定模板、不带参数（观测层只放行登记过的模板），不含令牌、声明与地址。
DIAGNOSTICS = {
    code: f"rag_engine_auth_failed code={code}"
    for code in ("TOKEN_INVALID", "JWKS_UNAVAILABLE", "CLIENT_NOT_ALLOWED")
}


def rejected(code: str) -> Verdict:
    """记一条固定码的诊断，返回 401。"""
    logger.warning(DIAGNOSTICS[code])
    return Verdict.INVALID
