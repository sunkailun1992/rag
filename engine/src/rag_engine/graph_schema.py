"""模型wire schema与无损格式适配；不执行字段修正或接受判定。"""

import json

from pydantic import BaseModel, ConfigDict, Field

QUOTE_DESCRIPTION = (
    "An EXACT contiguous verbatim quote copied from original source, NOT a canonical or "
    "assembled entity label. Preserve all intervening words, qualifiers, numbers and punctuation."
)


class GraphTripletOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    subjectQuote: str = Field(min_length=1, max_length=160, description=QUOTE_DESCRIPTION)
    predicate: str = Field(
        min_length=1,
        max_length=160,
        description="A short factual relationship explicitly stated in the original source.",
    )
    objectQuote: str = Field(min_length=1, max_length=160, description=QUOTE_DESCRIPTION)


class GraphExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    triples: list[GraphTripletOutput] = Field(max_length=32)


class StructuredOutputError(ValueError):
    """仅携带固定原因，避免ValidationError包含模型字段原值。"""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def response_format(maximum: int) -> dict:
    if type(maximum) is not int or not 1 <= maximum <= 32:
        raise StructuredOutputError("TRIPLE_COUNT")
    schema = GraphExtractionOutput.model_json_schema()
    schema["properties"]["triples"]["maxItems"] = maximum
    return {
        "type": "json_schema",
        "json_schema": {"name": "graph_triplets", "strict": True, "schema": schema},
    }


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("non-JSON numeric constant")


def structured_to_triplet_array(output: str, maximum: int) -> str:
    """先完整验证envelope/键/数量，再逐字段原值映射；字段guard留给原parse_fn。"""
    if type(maximum) is not int or not 1 <= maximum <= 32:
        raise StructuredOutputError("TRIPLE_COUNT")
    if not isinstance(output, str) or len(output) > 32768:
        raise StructuredOutputError("OUTPUT_TYPE_OR_SIZE")
    try:
        value = json.loads(output, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, TypeError, RecursionError):
        raise StructuredOutputError("JSON_SYNTAX") from None
    if not isinstance(value, dict) or set(value) != {"triples"}:
        raise StructuredOutputError("RESULT_STRUCTURE")
    triples = value["triples"]
    if not isinstance(triples, list):
        raise StructuredOutputError("TRIPLE_LIST_TYPE")
    if len(triples) > maximum:
        raise StructuredOutputError("TRIPLE_COUNT")
    if any(
        not isinstance(item, dict) or set(item) != {"subjectQuote", "predicate", "objectQuote"}
        for item in triples
    ):
        raise StructuredOutputError("TRIPLE_SHAPE")
    # 不先Pydantic校验字段，否则会吞掉原capture_triplets所需的精确错误反馈。
    # 字段类型/长度/控制字符/原文grounding均由既有完整守卫检验，绝不coerce/drop/truncate。
    values = [[item["subjectQuote"], item["predicate"], item["objectQuote"]] for item in triples]
    try:
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    except (ValueError, TypeError, RecursionError):
        raise StructuredOutputError("RESULT_STRUCTURE") from None
