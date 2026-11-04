"""无模型下载的中英文稀疏编码；Java 摄取必须使用完全相同的 lexical-v1 契约。"""

import hashlib
import math
import re
import unicodedata
from collections import Counter

SPARSE_MODEL = "lexical-v1"
_TOKEN_RUN = re.compile(r"[a-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]+")


def encode_sparse(text: str) -> dict:
    """NFKC/小写、ASCII 词与中文单/双字；SHA-256 uint32、log TF、L2 归一化。"""
    counts: Counter[int] = Counter()
    for match in _TOKEN_RUN.finditer(unicodedata.normalize("NFKC", text).lower()):
        value = match.group()
        if value[0].isascii():
            tokens = ["w:" + value]
        else:
            tokens = ["c:" + char for char in value]
            tokens += ["b:" + value[index : index + 2] for index in range(len(value) - 1)]
        for token in tokens:
            counts[int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:4], "big")] += 1
    indices = sorted(counts)
    weights = [1 + math.log(counts[index]) for index in indices]
    norm = math.sqrt(sum(weight * weight for weight in weights))
    return {"indices": indices, "values": [weight / norm for weight in weights]}
