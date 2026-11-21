"""生成不含运行环境/凭据的内部契约；CI 比较此文件与服务实际 schema。"""

import json
from pathlib import Path

from rag_engine.app import create_app
from rag_engine.config import Settings

# 保留域名（RFC 2606）占位：导出 schema 不取公钥，也不比对任何令牌。
schema = create_app(
    Settings(
        keycloak_issuer="https://keycloak.invalid/realms/github-dev", jwks_url="http://keycloak.invalid/certs"
    )
).openapi()
Path(__file__).resolve().parents[1].joinpath("openapi.json").write_text(
    json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
