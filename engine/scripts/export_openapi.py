"""生成不含运行环境/凭据的内部契约；CI 比较此文件与服务实际 schema。"""

import json
import secrets
from pathlib import Path

from rag_engine.app import create_app
from rag_engine.config import Settings

schema = create_app(Settings(api_key=secrets.token_urlsafe(32))).openapi()
Path(__file__).resolve().parents[1].joinpath("openapi.json").write_text(
    json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
