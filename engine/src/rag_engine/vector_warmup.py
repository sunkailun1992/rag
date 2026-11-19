"""Qdrant 保温：定期只读触碰集合的全部 payload 与向量索引页。

Qdrant 1.20 的 payload/向量都落在 mmap 文件上，“内存”档也只是启动时预读进系统页缓存；NAS 内存紧时
这些页会被挤出，长时间空闲后的首个检索冷读机械盘（2026-11-19 空闲 4 小时后首查召回 672 ms、
图扩展超出 0.4 秒预算）。每轮只读、结果即弃、不记录任何内容，不计入就绪。
"""

import asyncio
import logging

import httpx

from rag_engine.config import Settings
from rag_engine.http import post_json
from rag_engine.lexical import SPARSE_MODEL, encode_sparse

logger = logging.getLogger("rag_engine")
WARMUP_PAGE_SIZE = 1000
# 至多翻 20 页（2 万点）；更大的集合只保温前面的段，不让一轮无限翻页。
WARMUP_MAX_PAGES = 20


def _headers(settings: Settings) -> dict[str, str]:
    key = settings.qdrant_api_key.get_secret_value()
    return {"api-key": key} if key else {}


async def warm_vectors_once(settings: Settings, client: httpx.AsyncClient) -> None:
    base = f"{settings.qdrant_url}/collections/{settings.collection}"
    headers = _headers(settings)
    offset = None
    for _ in range(WARMUP_MAX_PAGES):
        # 只取一个小字段：Qdrant 仍要读出整条 payload，页被触碰，返回量与暴露面都小。
        body = {"limit": WARMUP_PAGE_SIZE, "with_payload": ["tenant_id"], "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        response = await post_json(
            client, base + "/points/scroll", body, headers, settings.max_upstream_bytes
        )
        offset = response["result"].get("next_page_offset")
        if offset is None:
            break
    probe = [1.0] + [0.0] * (settings.embedding_dimension - 1)
    # 精确检索扫过全部稠密向量；HNSW 检索触碰索引图。各只取 1 条、不带 payload。
    for exact in (True, False):
        await post_json(
            client,
            base + "/points/search",
            {
                "vector": probe,
                "limit": 1,
                "with_payload": False,
                "with_vector": False,
                "params": {"exact": exact},
            },
            headers,
            settings.max_upstream_bytes,
        )
    if settings.hybrid_enabled:
        await post_json(
            client,
            base + "/points/search",
            {
                "vector": {"name": SPARSE_MODEL, "vector": encode_sparse("vector warmup")},
                "limit": 1,
                "with_payload": False,
                "with_vector": False,
            },
            headers,
            settings.max_upstream_bytes,
        )


async def keep_vectors_warm(settings: Settings, client: httpx.AsyncClient, observation) -> None:
    """先等一个间隔再开始：保温针对长时间空闲，启动瞬间的数据通常仍在缓存里。"""
    while True:
        await asyncio.sleep(settings.qdrant_warmup_seconds)
        try:
            with observation.activate(), observation.operation("vector_warmup", root=True):
                await warm_vectors_once(settings, client)
        except Exception:
            # 不记录异常正文与地址；下一周期重试。
            logger.warning("rag_vector_warmup_failed code=VECTOR_STORE_UNAVAILABLE")
