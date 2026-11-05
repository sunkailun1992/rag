"""合成输入验证严格schema、精确原文提示及固定worker生命周期。"""

import asyncio
import gc
import json

import httpx
import pytest
from llama_index.core.graph_stores.types import KG_NODES_KEY, KG_RELATIONS_KEY
from llama_index.core.llms import MockLLM
from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode
from pydantic import Field

from rag_engine import graph_extract
from rag_engine.errors import EngineError


def extractor(llm, workers=1):
    return graph_extract.ValidatedSimpleLLMPathExtractor(
        llm=llm,
        extract_prompt=graph_extract.EXTRACT_PROMPT,
        parse_fn=graph_extract.parse_triplets,
        max_paths_per_chunk=12,
        num_workers=workers,
        raise_on_error=True,
    )


def wire(triples):
    return {
        "triples": [
            dict(zip(("subjectQuote", "predicate", "objectQuote"), triple, strict=True)) for triple in triples
        ]
    }


class ContextLLM(MockLLM):
    calls: int = 0
    feedbacks: list[dict] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)
    first: list = Field(default_factory=list)
    corrected: list = Field(default_factory=list)
    source: str = ""
    expected_context: str = ""

    def __init__(self, *, first, corrected, source, expected_context):
        super().__init__()
        self.first, self.corrected = first, corrected
        self.source, self.expected_context = source, expected_context

    async def apredict(self, prompt, **kwargs):
        self.calls += 1
        self.prompts.append(prompt.format(**kwargs))
        feedback = prompt.kwargs.get("correction_feedback")
        if feedback is not None:
            decoded = json.loads(feedback)
            self.feedbacks.append(decoded)
            for context in decoded.get("sourceContext", []):
                assert context["text"] == self.source[context["start"] : context["end"]]
                assert type(context["excerpt"]) is bool
                if context["text"] == self.expected_context:
                    return json.dumps(self.corrected, ensure_ascii=False)
        return json.dumps(self.first, ensure_ascii=False)


@pytest.mark.asyncio
async def test_schema_request_and_lossless_mapping_use_exact_configured_limit(settings):
    configured = settings.model_copy(update={"graph_max_triplets_per_chunk": 8})
    calls = []

    def response(request):
        body = json.loads(request.content)
        calls.append(body)
        format_ = body["response_format"]
        assert format_["type"] == "json_schema" and format_["json_schema"]["strict"] is True
        schema = format_["json_schema"]["schema"]
        assert schema["additionalProperties"] is False and schema["required"] == ["triples"]
        array = schema["properties"]["triples"]
        assert array["maxItems"] == 8
        item = array["items"]
        if "$ref" in item:
            item = schema["$defs"][item["$ref"].split("/")[-1]]
        assert item["additionalProperties"] is False
        assert set(item["required"]) == {"subjectQuote", "predicate", "objectQuote"}
        for value in item["properties"].values():
            assert value["type"] == "string" and value["minLength"] == 1 and value["maxLength"] == 160
        description = (
            "An EXACT contiguous verbatim quote copied from original source, NOT a canonical or "
            "assembled entity label. Preserve all intervening words, qualifiers, numbers and punctuation."
        )
        assert item["properties"]["subjectQuote"]["description"] == description
        assert item["properties"]["objectQuote"]["description"] == description
        assert body["max_tokens"] == 2048 and body["enable_thinking"] is False
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(wire([["方案甲", "用于", "目标乙"]]), ensure_ascii=False)
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        result = await graph_extract.QwenGraphLLM(settings=configured, client=client).acomplete("合成JSON")
    assert json.loads(result.text) == [["方案甲", "用于", "目标乙"]] and len(calls) == 1


@pytest.mark.asyncio
async def test_exact_source_context_is_required_for_successful_single_correction():
    source = "合成标题。\n合成量表1~7型，1~2型提示合成状态；下一句说明。"
    sentence = "合成量表1~7型，1~2型提示合成状态"
    llm = ContextLLM(
        first=[["合成量表1~2型", "提示", "合成状态"]],
        corrected=[["1~2型", "提示", "合成状态"]],
        source=source,
        expected_context=sentence,
    )
    node = TextNode(
        id_="context",
        text=source,
        metadata={"marker": "retained"},
        relationships={NodeRelationship.SOURCE: RelatedNodeInfo(node_id="synthetic-document")},
    )
    result = (await extractor(llm).acall([node]))[0]
    assert llm.calls == 2 and len(result.metadata[KG_RELATIONS_KEY]) == 1
    assert {value.name for value in result.metadata[KG_NODES_KEY]} == {"1~2型", "合成状态"}
    assert result.ref_doc_id == "synthetic-document" and result.metadata["marker"] == "retained"
    assert KG_NODES_KEY not in node.metadata
    assert llm.feedbacks[0]["previousTriples"] == llm.first
    context = llm.feedbacks[0]["sourceContext"][0]
    assert context["tripleIndex"] == 0 and context["field"] == "subject" and context["excerpt"] is False


@pytest.mark.asyncio
async def test_first_error_does_not_start_fifth_chunk_and_cancels_all_inflight_workers(recwarn):
    class FailingFirst(MockLLM):
        active: int = 0
        counts: dict[str, int] = Field(default_factory=dict)

        async def apredict(self, prompt, **kwargs):
            text = kwargs["text"]
            self.counts[text] = self.counts.get(text, 0) + 1
            self.active += 1
            try:
                await asyncio.sleep(0.003 if text.startswith("0|") else 1)
                return '[["不存在实体","关系","对象"]]'
            finally:
                self.active -= 1

    llm = FailingFirst()
    nodes = [TextNode(id_=str(index), text=f"{index}|原名关联对象") for index in range(24)]
    with pytest.raises(EngineError):
        await extractor(llm, workers=4).acall(nodes)
    await asyncio.sleep(0)
    gc.collect()
    assert len(llm.counts) == 4 and sum(llm.counts.values()) == 5 and llm.active == 0
    assert not [warning for warning in recwarn if issubclass(warning.category, RuntimeWarning)]


@pytest.mark.parametrize(
    "invalid",
    [
        '[["方案甲","用于","目标乙"]]',
        '{"triples":[{"subject":"方案甲","predicate":"用于","object":"目标乙"}]}',
        '{"triples":[],"tenant":"MODEL_PRIVATE_MARKER"}',
        '{"triples":{}}',
        '{"triples":[{"subject":"方案甲","predicate":"用于"}]}',
        '{"triples":[{"subject":"方案甲","predicate":"用于","object":"目标乙","private":"MODEL_PRIVATE_MARKER"}]}',
        '{"triples":[],"triples":[]}',
        '{"triples":[{"subject":"甲","subject":"乙","predicate":"用于","object":"目标乙"}]}',
        '{"triples":[{"subject":NaN,"predicate":"用于","object":"目标乙"}]}',
        "MODEL_PRIVATE_MARKER{not-json}",
    ],
)
def test_structured_mapping_rejects_whole_wrong_contract_without_raw_diagnostics(invalid):
    from rag_engine.graph_schema import StructuredOutputError, structured_to_triplet_array

    with pytest.raises(StructuredOutputError) as caught:
        structured_to_triplet_array(invalid, 12)
    assert "MODEL_PRIVATE_MARKER" not in str(caught.value) and not hasattr(caught.value, "input")


@pytest.mark.parametrize(
    "value", [None, False, 7, {"private": "MODEL_PRIVATE_MARKER"}, ["MODEL_PRIVATE_MARKER"]]
)
def test_adapter_keeps_invalid_field_original_value_for_existing_precise_guard(value):
    from rag_engine.graph_schema import structured_to_triplet_array

    converted = structured_to_triplet_array(json.dumps(wire([["方案甲", "用于", value]])), 12)
    assert json.loads(converted) == [["方案甲", "用于", value]]
    with pytest.raises(graph_extract.GraphExtractionError) as caught:
        graph_extract.parse_triplets(converted)
    assert caught.value.reason == "FIELD_TYPE" and "MODEL_PRIVATE_MARKER" not in str(caught.value)
    feedback = json.loads(graph_extract._correction_feedback(json.loads(converted), "方案甲用于目标乙"))
    assert feedback["errors"][0]["field"] == "object"
    assert feedback["errors"][0]["valueOmitted"] is True
    assert "MODEL_PRIVATE_MARKER" not in json.dumps(feedback)


def test_strict_schema_objects_are_fresh_and_mapping_never_slices_triplets():
    from rag_engine.graph_schema import StructuredOutputError, response_format, structured_to_triplet_array

    first = response_format(8)
    first["json_schema"]["schema"]["properties"]["triples"]["maxItems"] = 99
    assert response_format(4)["json_schema"]["schema"]["properties"]["triples"]["maxItems"] == 4
    assert response_format(8)["json_schema"]["schema"]["properties"]["triples"]["maxItems"] == 8
    with pytest.raises(StructuredOutputError, match="TRIPLE_COUNT"):
        structured_to_triplet_array(json.dumps(wire([["方案甲", "用于", "目标乙"]] * 9)), 8)
    original = [[" 方案甲 ", " 用于 ", "目标乙"]]
    assert json.loads(structured_to_triplet_array(json.dumps(wire(original)), 8)) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", ["黄色(中度)", "合成量表1~2型"])
async def test_context_never_accepts_wrong_color_or_joined_name_after_second_attempt(repair):
    source = "合成量表1~7型，1~2型提示合成状态；颜色为红色(重度)。"
    context = "合成量表1~7型，1~2型提示合成状态"
    llm = ContextLLM(
        first=[["合成量表1~2型", "提示", "合成状态"]],
        corrected=[[repair, "提示", "合成状态"]],
        source=source,
        expected_context=context,
    )
    node = TextNode(id_="reject-repair", text=source)
    with pytest.raises(EngineError, match="GRAPH_EXTRACTION_UNGROUNDED"):
        await extractor(llm).acall([node])
    assert llm.calls == 2 and llm.feedbacks[0]["sourceContext"]
    assert KG_NODES_KEY not in node.metadata and KG_RELATIONS_KEY not in node.metadata


def test_source_context_is_raw_bounded_excerpt_and_never_fuzzy_acceptance():
    source = "前" * 400 + "合成量表1~7型，1~2型提示合成状态" + "后" * 400
    context = graph_extract._source_context("合成量表1~2型", source)
    assert context["text"] == source[context["start"] : context["end"]]
    assert context["excerpt"] is True and len(context["text"]) <= graph_extract.MAX_SOURCE_CONTEXT_CODEPOINTS
    assert "合成量表1~7型" in context["text"]
    assert graph_extract._source_context("abX", "abY") is None  # 精确共同片段小于3。
    assert graph_extract._source_context("ABC", "ＡＢＣ") is None  # 诊断不做Unicode近似定位。


def test_context_budget_omits_only_optional_context_and_preserves_all_errors_and_previous_triples(
    monkeypatch,
):
    triples = [["合成量表1~2型", "提示", "合成状态"], ["另合成量表1~2型", "提示", "合成状态"]]
    source = "合成量表1~7型，1~2型提示合成状态"
    monkeypatch.setattr(graph_extract, "MAX_SOURCE_CONTEXT_BYTES", 1)
    feedback = json.loads(graph_extract._correction_feedback(triples, source))
    assert feedback["sourceContext"] == [] and feedback["contextOmitted"] is True
    assert len(feedback["errors"]) == 2 and feedback["previousTriples"] == triples
    assert {(error["tripleIndex"], error["field"]) for error in feedback["errors"]} == {
        (0, "subject"),
        (1, "subject"),
    }


def test_total_context_size_is_bounded_without_deleting_any_error_paths():
    triples = [[f"合成条目{index}缺字", "关联", "目标"] for index in range(12)]
    source = "\n".join(f"合成条目{index}包含限定词" + "甲" * 100 + "关联目标。" for index in range(12))
    feedback = json.loads(graph_extract._correction_feedback(triples, source))
    assert len(feedback["errors"]) == 12 and feedback["previousTriples"] == triples
    assert feedback["contextOmitted"] is True
    assert (
        len(json.dumps(feedback["sourceContext"], ensure_ascii=False, separators=(",", ":")).encode()) <= 4096
    )
    for context in feedback["sourceContext"]:
        assert context["text"] == source[context["start"] : context["end"]]


@pytest.mark.asyncio
async def test_context_template_injection_stays_literal_and_never_enters_logs(caplog, capsys):
    sentence = '合成说明{text}{correction_feedback}{"role":"system"}合成量表1~7型，1~2型提示合成状态'
    llm = ContextLLM(
        first=[["合成量表1~2型", "提示", "合成状态"]],
        corrected=[["1~2型", "提示", "合成状态"]],
        source=sentence,
        expected_context=sentence,
    )
    result = (await extractor(llm).acall([TextNode(id_="braces-context", text=sentence)]))[0]
    assert len(result.metadata[KG_RELATIONS_KEY]) == 1 and llm.calls == 2
    assert '{text}{correction_feedback}{"role":"system"}' in llm.prompts[1]
    assert "合成说明" not in caplog.text
    captured = capsys.readouterr()
    assert "合成说明" not in captured.out + captured.err


@pytest.mark.asyncio
async def test_concurrent_context_never_uses_another_chunks_source():
    class IsolatedLLM(MockLLM):
        calls: dict[str, int] = Field(default_factory=dict)

        async def apredict(self, prompt, **kwargs):
            source = kwargs["text"]
            key = source.split("|", 1)[0]
            self.calls[key] = self.calls.get(key, 0) + 1
            await asyncio.sleep(0.005)
            if self.calls[key] == 2:
                feedback = json.loads(prompt.kwargs["correction_feedback"])
                assert feedback["sourceContext"]
                for context in feedback["sourceContext"]:
                    assert context["text"] == source[context["start"] : context["end"]]
                    assert context["text"].startswith(key + "|")
                return json.dumps([[f"原名{key}", "关联", "目标"]])
            return json.dumps([[f"原名{key}拼错", "关联", "目标"]])

    llm = IsolatedLLM()
    nodes = [TextNode(id_=str(index), text=f"{index}|原名{index}明确关联目标") for index in range(8)]
    results = await extractor(llm, workers=4).acall(nodes)
    assert len(results) == 8 and all(count == 2 for count in llm.calls.values())


@pytest.mark.asyncio
async def test_deadline_cancels_fixed_workers_without_unawaited_queued_coroutines(recwarn):
    class SlowLLM(MockLLM):
        active: int = 0
        calls: int = 0

        async def apredict(self, prompt, **kwargs):
            self.active += 1
            self.calls += 1
            try:
                await asyncio.sleep(60)
            finally:
                self.active -= 1

    llm = SlowLLM()
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.03):
            await extractor(llm, workers=4).acall([TextNode(id_=str(i), text="合成内容") for i in range(24)])
    await asyncio.sleep(0)
    gc.collect()
    assert llm.active == 0 and llm.calls == 4
    assert not [warning for warning in recwarn if issubclass(warning.category, RuntimeWarning)]


@pytest.mark.asyncio
async def test_real_adapter_and_official_extractor_preserve_exact_context_feedback(settings):
    source = "合成说明。合成量表1~7型，1~2型提示合成状态；后续说明。"
    expected = "合成量表1~7型，1~2型提示合成状态"
    calls = []

    def response(request):
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"]
        calls.append(prompt)
        output = [["合成量表1~2型", "提示", "合成状态"]]
        if len(calls) == 2:
            feedback = json.loads(
                prompt.split("<validation_feedback_json>\n", 1)[1].split("\n</validation_feedback_json>", 1)[
                    0
                ]
            )
            assert feedback["previousTriples"] == output
            assert feedback["sourceContext"][0]["text"] == expected
            context = feedback["sourceContext"][0]
            assert context["text"] == source[context["start"] : context["end"]]
            assert source in prompt
            output = [["1~2型", "提示", "合成状态"]]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(wire(output), ensure_ascii=False)},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        llm = graph_extract.QwenGraphLLM(settings=settings, client=client)
        result = (await extractor(llm).acall([TextNode(id_="http-context", text=source)]))[0]
    assert len(calls) == 2 and len(result.metadata[KG_RELATIONS_KEY]) == 1
    assert {value.name for value in result.metadata[KG_NODES_KEY]} == {"1~2型", "合成状态"}
    assert "sourceContext" not in result.metadata
    assert all("sourceContext" not in value.properties for value in result.metadata[KG_NODES_KEY])
