"""只凭活动身份从既有 Qdrant 回读；不改变向量或 collection。"""

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass

import httpx

from rag_engine.config import Settings
from rag_engine.errors import EngineError
from rag_engine.graph_models import ActiveGraphSource
from rag_engine.http import post_json


def digest(*values) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def source_key(source: ActiveGraphSource) -> str:
    return digest(source.model_dump())


def document_key(tenant: str, kb: str, document: str) -> str:
    return digest(tenant, kb, document)


def content_fingerprint(value: str) -> str:
    """与 Java normalizeForHash 一致，保持已有 Office 语义去重身份。"""
    text = unicodedata.normalize("NFKC", value).replace("\r\n", "\n")
    # Java MULTILINE 还识别独立 CR/NEL/Unicode 行分隔；默认 \d/\s 是 ASCII。
    start = r"(?:\A|(?<=[\n\r\x85\u2028\u2029]))"
    end = r"(?=\Z|[\n\r\x85\u2028\u2029])"
    text = re.sub(
        start + r"位置: ([^\n\r\x85\u2028\u2029]+?) / 第[0-9]+行(?: / 片段[0-9]+)?" + end,
        r"位置: \1",
        text,
    )
    text = re.sub(start + r"- (序号|行号|记录编号):[ \t\n\x0b\f\r]*[^\n]*(?:\n|" + end + ")", "", text)
    text = re.sub(r"[\t ]+", " ", text).strip("".join(chr(value) for value in range(33)))
    return hashlib.sha256(text.encode()).hexdigest()


def safe_source_metadata(source) -> dict:
    if not isinstance(source, dict):
        return {}
    result = {}
    page = source.get("page")
    if type(page) in {int, float} and math.isfinite(page) and 0 < page <= 2_147_483_647 and int(page) == page:
        result["page"] = int(page)
    block = source.get("block_type")
    if isinstance(block, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", block):
        result["block_type"] = block
    box = source.get("bbox")
    if (
        isinstance(box, list)
        and len(box) == 4
        and all(type(value) in {int, float} and math.isfinite(value) and value >= 0 for value in box)
        and box[2] >= box[0]
        and box[3] >= box[1]
    ):
        result["bbox"] = box
    capabilities = source.get("ocr_capabilities")
    if isinstance(capabilities, dict):
        approved = {
            key: capabilities[key]
            for key in ["textRecognition", "tableStructure", "layoutAnalysis"]
            if type(capabilities.get(key)) is bool
        }
        if approved:
            result["ocr_capabilities"] = approved
    return result


def evidence_excerpt(content: str, terms: list[str], maximum: int = 1200) -> dict:
    """按展示实体取局部摘录；跨远距离实体保留两个窗口并明确省略。"""
    text = content
    normalized = False
    positions = [(text.casefold().find(term.casefold()), len(term)) for term in terms if term]
    if any(position < 0 for position, _ in positions):
        normalized = True
        text = " ".join(unicodedata.normalize("NFKC", content).casefold().split())
        positions = [
            (text.find(" ".join(unicodedata.normalize("NFKC", term).casefold().split())), len(term))
            for term in terms
            if term
        ]
    if not positions or any(position < 0 for position, _ in positions):
        raise EngineError("GRAPH_EXTRACTION_UNGROUNDED", 502)
    first = min(position for position, _ in positions)
    last = max(position + length for position, length in positions)
    if last - first <= maximum:
        start = max(0, min(first - maximum // 4, last - maximum))
        excerpt = text[start : start + maximum]
    else:
        half = (maximum - 5) // 2
        left = max(0, first - half // 4)
        right = max(0, last - half * 3 // 4)
        excerpt = text[left : left + half] + "\n…\n" + text[right : right + half]
    return {
        "evidence": excerpt,
        "evidenceTruncated": len(excerpt) < len(text),
        "evidenceNormalized": normalized,
    }


@dataclass
class VerifiedSource:
    identity: ActiveGraphSource
    content: str
    provenance: dict


async def read_active_sources(
    sources: list[ActiveGraphSource], tenant: str, kb: str, settings: Settings, client: httpx.AsyncClient
) -> list[VerifiedSource]:
    if not settings.qdrant_url:
        raise EngineError("GRAPH_NOT_CONFIGURED", 503)
    result = []
    total_characters = 0
    fields = {
        "documentId": "document_id",
        "documentCode": "document_code",
        "documentVersion": "document_version",
        "documentRevision": "document_revision",
        "chunkId": "chunk_id",
        "contentSha256": "content_sha256",
    }
    for start in range(0, len(sources), 64):
        batch = sources[start : start + 64]
        response = await post_json(
            client,
            f"{settings.qdrant_url}/collections/{settings.collection}/points",
            {"ids": [source.pointId for source in batch], "with_payload": True, "with_vector": False},
            {"api-key": settings.qdrant_api_key.get_secret_value()}
            if settings.qdrant_api_key.get_secret_value()
            else {},
            min(settings.max_upstream_bytes, 2 * 1024 * 1024),
        )
        try:
            values = response["result"]
            if not isinstance(values, list) or len(values) != len(batch):
                raise ValueError()
            points = {str(point["id"]): point["payload"] for point in values}
            if len(points) != len(batch) or set(points) != {source.pointId for source in batch}:
                raise ValueError()
            for source in batch:
                payload = points[source.pointId]
                if (
                    payload.get("tenant_id") != tenant
                    or payload.get("knowledge_base") != kb
                    or payload.get("document_status") != "ACTIVE"
                    or any(
                        type(payload.get(field)) is not type(getattr(source, key))
                        or payload.get(field) != getattr(source, key)
                        for key, field in fields.items()
                    )
                ):
                    raise ValueError()
                content = payload.get("content")
                if not isinstance(content, str) or not 1 <= len(content) <= 6000:
                    raise ValueError()
                if content_fingerprint(content) != source.contentSha256:
                    raise ValueError()
                total_characters += len(content)
                if total_characters > 12_000_000:
                    raise EngineError("GRAPH_CONTENT_LIMIT", 413)
                provenance = source.model_dump()
                for origin, target in {
                    "source_name": "sourceName",
                    "source_type": "sourceType",
                    "source_locator": "sourceLocator",
                    "chunk_type": "chunkType",
                    "section_path": "sectionPath",
                }.items():
                    if isinstance(payload.get(origin), str):
                        provenance[target] = payload[origin][:512]
                provenance["evidence"] = content[:1200]
                provenance["evidenceTruncated"] = len(content) > 1200
                metadata = safe_source_metadata(payload.get("source_metadata"))
                if metadata:
                    provenance["sourceMetadata"] = metadata
                locators = payload.get("duplicate_locators")
                if isinstance(locators, list):
                    provenance["duplicateLocators"] = [
                        value[:512] for value in locators[:8] if isinstance(value, str)
                    ]
                duplicate_metadata = payload.get("duplicate_source_metadata")
                if isinstance(duplicate_metadata, list):
                    provenance["duplicateSourceMetadata"] = [
                        safe_source_metadata(value) for value in duplicate_metadata[:8]
                    ]
                occurrences = payload.get("duplicate_occurrence_count")
                if type(occurrences) is int and occurrences > 0:
                    provenance["duplicateOccurrenceCount"] = occurrences
                result.append(VerifiedSource(source, content, provenance))
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise EngineError("GRAPH_SOURCE_CHANGED", 409) from error
    return result
