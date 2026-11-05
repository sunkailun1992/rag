"""仅用合成量表式三元组验证完整抽取、一次纠正和有界资源；无业务材料。"""

import asyncio
import json
import logging

import pytest
from llama_index.core import PropertyGraphIndex
from llama_index.core.graph_stores import SimplePropertyGraphStore
from llama_index.core.graph_stores.types import KG_NODES_KEY, KG_RELATIONS_KEY, EntityNode, Relation
from llama_index.core.indices.property_graph import SimpleLLMPathExtractor
from llama_index.core.llms import MockLLM
from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode
from pydantic import Field, ValidationError

from rag_engine import graph_extract, graph_jobs
from rag_engine.errors import EngineError


class ScriptedLLM(MockLLM):
    responses: list[str] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)

    def __init__(self, responses):
        super().__init__()
        self.responses = responses

    async def apredict(self, prompt, **kwargs):
        self.prompts.append(prompt.format(**kwargs))
        return self.responses[min(len(self.prompts) - 1, len(self.responses) - 1)]


def extractor(llm, workers=1):
    cls = getattr(graph_extract, "ValidatedSimpleLLMPathExtractor", SimpleLLMPathExtractor)
    return cls(
        llm=llm,
        extract_prompt=graph_extract.EXTRACT_PROMPT,
        parse_fn=graph_extract.parse_triplets,
        max_paths_per_chunk=12,
        num_workers=workers,
        raise_on_error=True,
    )


def source_node():
    text = "；".join(f"合成条目{index}使用合成选项{index}" for index in range(12))
    return TextNode(
        id_="synthetic-source",
        text=text,
        metadata={"source_marker": "retained"},
        relationships={NodeRelationship.SOURCE: RelatedNodeInfo(node_id="synthetic-document")},
    )


def valid_triples():
    return [[f"合成条目{index}", "使用", f"合成选项{index}"] for index in range(12)]


@pytest.mark.asyncio
async def test_empty_objects_require_one_complete_corrected_response_without_dropping_valid_triples():
    invalid = valid_triples()
    for item in invalid[-4:]:
        item[2] = ""
    llm = ScriptedLLM(responses=[json.dumps(invalid), json.dumps(valid_triples())])
    node = source_node()
    result = await extractor(llm).acall([node])
    assert len(llm.prompts) == 2
    assert len(result[0].metadata[KG_RELATIONS_KEY]) == 12
    assert result[0].metadata["source_marker"] == "retained"
    assert result[0].ref_doc_id == "synthetic-document"
    assert KG_RELATIONS_KEY not in node.metadata  # 尝试用副本，不把失败中间态污染原始节点。
    assert "FIELD_EMPTY" in llm.prompts[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_output,reason",
    [
        ('[["合成条目0","使用",42]]', "FIELD_TYPE"),
        (json.dumps([["合成条目0", "使用", "长" * 161]]), "FIELD_LENGTH"),
        (json.dumps([["合成条目0", "使用", "合成\n选项0"]]), "FIELD_CONTROL"),
        ('{"triples":"MODEL_OUTPUT_ONLY_MARKER"}', "TRIPLE_LIST_TYPE"),
    ],
)
async def test_correction_validates_field_contract_and_only_includes_safe_reason(bad_output, reason):
    llm = ScriptedLLM(responses=[bad_output, '[["合成条目0","使用","合成选项0"]]'])
    result = await extractor(llm).acall([source_node()])
    assert len(result[0].metadata[KG_RELATIONS_KEY]) == 1 and len(llm.prompts) == 2
    assert reason in llm.prompts[1]
    assert "MODEL_OUTPUT_ONLY_MARKER" not in llm.prompts[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [EngineError("UPSTREAM_REJECTED"), TimeoutError(), asyncio.CancelledError()]
)
async def test_non_contract_failure_and_cancellation_are_never_retried(failure):
    class FailingLLM(MockLLM):
        calls: int = 0

        async def apredict(self, _prompt, **_kwargs):
            self.calls += 1
            raise failure

    llm = FailingLLM()
    with pytest.raises(type(failure)):
        await extractor(llm).acall([source_node()])
    assert llm.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", ["invalid_again", "empty_array", "ungrounded_again"])
async def test_invalid_repair_never_reports_success_or_attempts_a_third_request(repair):
    invalid = valid_triples()
    invalid[-1][2] = ""
    response = json.dumps(invalid)
    if repair == "empty_array":
        response = "[]"
    elif repair == "ungrounded_again":
        response = '[["不存在的合成主体","使用","合成选项0"]]'
    llm = ScriptedLLM(responses=[json.dumps(invalid), response, json.dumps(valid_triples())])
    node = source_node()
    with pytest.raises((EngineError, ValueError), match="GRAPH_EXTRACTION_"):
        await extractor(llm).acall([node])
    assert len(llm.prompts) == 2
    assert KG_NODES_KEY not in node.metadata and KG_RELATIONS_KEY not in node.metadata


@pytest.mark.asyncio
async def test_invalid_correction_exposes_only_whitelisted_reason():
    llm = ScriptedLLM(responses=['[["合成条目0","使用",""]]', "[]"])
    with pytest.raises(EngineError) as caught:
        await extractor(llm).acall([source_node()])
    assert caught.value.code == "GRAPH_EXTRACTION_INVALID"
    assert caught.value.reason == "REPAIR_EMPTY_RESULT"
    assert str(caught.value) == "GRAPH_EXTRACTION_INVALID"


@pytest.mark.asyncio
async def test_punctuation_and_units_are_not_rewritten_during_grounding_correction():
    llm = ScriptedLLM(
        responses=[
            '[["甲乙","合成用量","3 kg"]]',
            '[["甲/乙","合成用量","3kg"]]',
        ]
    )
    node = TextNode(id_="punctuation", text="合成字段: 甲/乙；合成数量: 3kg")
    result = await extractor(llm).acall([node])
    assert len(llm.prompts) == 2
    assert {entity.name for entity in result[0].metadata[KG_NODES_KEY]} == {"甲/乙", "3kg"}


@pytest.mark.asyncio
async def test_real_qwen_adapter_and_official_extractor_correct_entire_response(settings):
    import httpx

    calls = []
    invalid = valid_triples()
    for item in invalid[-4:]:
        item[2] = ""

    def response(request):
        body = json.loads(request.content)
        assert body["max_tokens"] == 2048 and body["enable_thinking"] is False
        calls.append(body)
        output = invalid if len(calls) == 1 else valid_triples()
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        llm = graph_extract.QwenGraphLLM(settings=settings, client=client)
        result = await extractor(llm).acall([source_node()])
    assert len(calls) == 2 and len(result[0].metadata[KG_RELATIONS_KEY]) == 12
    assert "FIELD_EMPTY" in calls[1]["messages"][0]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["FIELD_EMPTY", "MODEL_OUTPUT_ONLY_MARKER", ["FIELD_EMPTY"], None])
async def test_parent_worker_protocol_only_logs_whitelisted_reason(settings, monkeypatch, caplog, reason):
    class Process:
        returncode = 0

        async def communicate(self, _private_input):
            return json.dumps(
                {
                    "error": "GRAPH_EXTRACTION_INVALID",
                    "reason": reason,
                    "modelOutput": "MODEL_OUTPUT_ONLY_MARKER",
                }
            ).encode(), b""

    async def spawn(*_args, **_kwargs):
        return Process()

    monkeypatch.setattr(graph_jobs.asyncio, "create_subprocess_exec", spawn)
    with caplog.at_level(logging.WARNING, logger="rag_engine.graph"):
        with pytest.raises(EngineError, match="GRAPH_EXTRACTION_INVALID"):
            await graph_jobs.run_worker({}, settings, 1)
    assert "MODEL_OUTPUT_ONLY_MARKER" not in caplog.text
    assert ("reason=FIELD_EMPTY" in caplog.text) is (reason == "FIELD_EMPTY")


@pytest.mark.asyncio
async def test_first_valid_empty_extraction_is_accepted_without_correction():
    llm = ScriptedLLM(responses=["[]"])
    result = await extractor(llm).acall([source_node()])
    assert len(llm.prompts) == 1
    assert result[0].metadata[KG_NODES_KEY] == result[0].metadata[KG_RELATIONS_KEY] == []


def test_grounding_correction_uses_fresh_official_llama_node_without_old_bad_paths():
    llm = ScriptedLLM(
        responses=[
            '[["不存在的合成主体","使用","合成选项0"]]',
            '[["合成条目0","使用","合成选项0"]]',
        ]
    )
    node = source_node()
    store = SimplePropertyGraphStore()
    PropertyGraphIndex(
        nodes=[node], property_graph_store=store, embed_kg_nodes=False, kg_extractors=[extractor(llm)]
    )
    entities = [value for value in store.get() if isinstance(value, EntityNode)]
    triplets = store.get_triplets(ids=[value.id for value in entities])
    assert len(llm.prompts) == 2
    assert {value.name for value in entities} == {"合成条目0", "合成选项0"}
    assert len(triplets) == 1
    assert all("不存在" not in value.name for value in entities)


@pytest.mark.asyncio
@pytest.mark.parametrize("workers", [1, 4])
async def test_extractor_bounded_concurrency_includes_correction_attempts(workers):
    class ConcurrentLLM(MockLLM):
        active: int = 0
        peak: int = 0
        counts: dict[str, int] = Field(default_factory=dict)

        async def apredict(self, _prompt, **kwargs):
            text = kwargs["text"]
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.counts[text] = self.counts.get(text, 0) + 1
            try:
                await asyncio.sleep(0.01)
                return (
                    '[["合成条目0","使用",""]]'
                    if self.counts[text] == 1
                    else ('[["合成条目0","使用","合成选项0"]]')
                )
            finally:
                self.active -= 1

    llm = ConcurrentLLM()
    nodes = [TextNode(id_=str(index), text=f"记录{index}: 合成条目0使用合成选项0") for index in range(8)]
    result = await extractor(llm, workers).acall(nodes)
    assert llm.peak == workers
    assert len(result) == 8 and all(value == 2 for value in llm.counts.values())


def test_extraction_workers_configuration_is_opt_in_and_bounded(settings):
    assert settings.graph_extraction_workers == 1
    for value in [0, 5]:
        with pytest.raises(ValidationError):
            type(settings)(api_key="synthetic-" * 4, graph_extraction_workers=value)
    assert type(settings)(api_key="synthetic-" * 4, graph_extraction_workers=4).graph_extraction_workers == 4


@pytest.mark.filterwarnings(
    "ignore:The `dict` method is deprecated:DeprecationWarning:llama_index.graph_stores.neo4j"
)
def test_controlled_neo4j_store_bounds_official_unwind_batches_without_reordering():
    store = object.__new__(graph_extract.ControlledNeo4jPropertyGraphStore)
    batches = []

    def record(_query, param_map=None):
        batches.append(param_map["data"])
        return []

    store.structured_query = record
    entities = [EntityNode(name=f"synthetic-{index}") for index in range(257)]
    store.upsert_nodes(entities)
    assert max(len(batch) for batch in batches) <= 128
    assert [row["id"] for batch in batches for row in batch] == [node.id for node in entities]
    batches.clear()
    relations = [
        Relation(label="REL_SYNTHETIC", source_id=f"s-{index}", target_id=f"t-{index}")
        for index in range(257)
    ]
    store.upsert_relations(relations)
    assert max(len(batch) for batch in batches) <= 128
    assert [row["source_id"] for batch in batches for row in batch] == [rel.source_id for rel in relations]
