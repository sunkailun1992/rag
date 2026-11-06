"""完整候选重抽回归：窄slot修复不可用时仍逐次验证，失败绝不筛项发布。"""

import asyncio
import json

import httpx
import pytest
from llama_index.core.graph_stores.types import KG_NODES_KEY, KG_RELATIONS_KEY
from llama_index.core.llms import MockLLM
from llama_index.core.schema import TextNode
from pydantic import Field

from rag_engine import graph_extract
from rag_engine.errors import EngineError


class CandidateLLM(MockLLM):
    responses: list = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)
    modes: list[str] = Field(default_factory=list)

    def __init__(self, responses):
        super().__init__()
        self.responses = responses

    async def apredict(self, prompt, **kwargs):
        self.prompts.append(prompt.format(**kwargs))
        mode = "slot" if "repair_data" in prompt.kwargs else "full"
        self.modes.append(mode)
        value = self.responses[min(len(self.prompts) - 1, len(self.responses) - 1)]
        if isinstance(value, BaseException):
            raise value
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def extractor(llm, workers=1):
    return graph_extract.ValidatedSimpleLLMPathExtractor(
        llm=llm,
        extract_prompt=graph_extract.EXTRACT_PROMPT,
        parse_fn=graph_extract.parse_triplets,
        max_paths_per_chunk=8,
        num_workers=workers,
        raise_on_error=True,
    )


@pytest.mark.parametrize(
    "source,first,corrected",
    [
        (
            "黄色(中度)=≤40单位 重度依赖 / 41～60单位 中度依赖",
            [["≤40单位", "对应", "黄色(中度)=重度依赖"]],
            [["≤40单位", "对应", "重度依赖"]],
        ),
        ("方案甲适用目标乙。", [["改写甲", "适用", "改写乙"]], [["方案甲", "适用", "目标乙"]]),
        (
            "- 标准措施: A-B方案\n- 适用目标: 目标乙",
            [["AB方案", "适用", "目标乙"]],
            [["A-B方案", "适用", "目标乙"]],
        ),
        (
            "工具甲覆盖1~7类，1~2类对应状态乙。",
            [["工具甲1~2类", "对应", "状态乙"]],
            [["1~2类", "对应", "状态乙"]],
        ),
    ],
)
async def test_no_local_quote_plan_gets_complete_validated_candidate(source, first, corrected):
    llm = CandidateLLM([first, corrected])
    node = TextNode(id_="synthetic-retry", text=source)
    result = (await extractor(llm).acall([node]))[0]
    assert llm.modes == ["full", "full"]
    assert len(result.metadata[KG_RELATIONS_KEY]) == len(corrected)
    assert source in llm.prompts[1] and "ENTITY_NOT_IN_SOURCE" in llm.prompts[1]
    assert "validation_feedback_json" in llm.prompts[1]
    assert KG_NODES_KEY not in node.metadata and KG_RELATIONS_KEY not in node.metadata


async def test_invalid_slot_reply_uses_last_request_for_new_complete_candidate():
    source = "A-B：红档对应40～50单位；其他黄档对应20～30单位。"
    llm = CandidateLLM(
        [
            [["AB：红档", "对应", "40～50单位"]],
            {"repairKey": "foreign", "replacements": {}},
            [["A-B：红档", "对应", "40～50单位"]],
        ]
    )
    result = (await extractor(llm).acall([TextNode(id_="slot-then-full", text=source)]))[0]
    assert llm.modes == ["full", "slot", "full"]
    assert len(result.metadata[KG_RELATIONS_KEY]) == 1
    assert source in llm.prompts[2] and "repair_data" not in llm.prompts[2]
    assert "20～30单位" not in [entity.name for entity in result.metadata[KG_NODES_KEY]]


@pytest.mark.parametrize("response", [[["无出处名称", "对应", "目标"]], [], [["实体", "对应", ""]]])
async def test_three_bad_candidates_fail_without_empty_success_or_fourth_request(response):
    llm = CandidateLLM([[["拼接主体", "对应", "目标"]], response, response, [["实体", "对应", "目标"]]])
    node = TextNode(id_="retry-limit", text="实体对应目标。")
    with pytest.raises(EngineError, match="GRAPH_EXTRACTION_"):
        await extractor(llm).acall([node])
    assert len(llm.prompts) == 3
    assert KG_NODES_KEY not in node.metadata and KG_RELATIONS_KEY not in node.metadata


async def test_failed_slot_never_automatically_maps_other_color_from_same_source():
    source = "A-B：红档=40～50单位，A-B：黄档=20～30单位。"
    llm = CandidateLLM([[["AB：红档", "对应", "20～30单位"]]] * 3)
    with pytest.raises(EngineError, match="GRAPH_EXTRACTION_UNGROUNDED"):
        await extractor(llm).acall([TextNode(id_="wrong-pair", text=source)])
    assert llm.modes == ["full", "full", "full"]


async def test_cancellation_during_last_candidate_propagates_without_retry():
    llm = CandidateLLM([[["拼接实体", "对应", "目标"]]] * 2 + [asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await extractor(llm).acall([TextNode(id_="last-cancel", text="实体对应目标。")])
    assert len(llm.prompts) == 3


async def test_qwen_full_retry_restores_normal_schema_after_rejected_slot_reply(settings):
    source = "A-B方案用于目标乙。"
    requests = []

    def response(request):
        body = json.loads(request.content)
        requests.append(body)
        name = body["response_format"]["json_schema"]["name"]
        if len(requests) == 1:
            assert name == "graph_triplets"
            value = {"triples": [{"subjectQuote": "AB方案", "predicate": "用于", "objectQuote": "目标乙"}]}
        elif len(requests) == 2:
            assert name == "graph_quote_slot_repair"
            value = {"repairKey": "foreign", "replacements": {"s0": "A-B方案"}}
        else:
            assert len(requests) == 3 and name == "graph_triplets"
            value = {"triples": [{"subjectQuote": "A-B方案", "predicate": "用于", "objectQuote": "目标乙"}]}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(value, ensure_ascii=False)}}
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        llm = graph_extract.QwenGraphLLM(settings=settings, client=client)
        result = await extractor(llm).acall([TextNode(id_="schema-reset", text=source)])
        assert llm.response_format_override is None
    assert len(requests) == 3 and len(result[0].metadata[KG_RELATIONS_KEY]) == 1


async def test_all_candidates_remain_inside_fixed_worker_budget():
    class ConcurrentLLM(MockLLM):
        active: int = 0
        peak: int = 0
        calls: dict = Field(default_factory=dict)

        async def apredict(self, prompt, **kwargs):
            text = kwargs["text"]
            self.calls[text] = self.calls.get(text, 0) + 1
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.001)
                return json.dumps([["拼接名称" if self.calls[text] < 3 else "实体", "对应", "目标"]])
            finally:
                self.active -= 1

    llm = ConcurrentLLM()
    nodes = [TextNode(id_=str(i), text=f"{i} 实体对应目标。") for i in range(9)]
    result = await extractor(llm, workers=4).acall(nodes)
    assert len(result) == 9 and llm.peak == 4 and llm.active == 0
    assert all(count == 3 for count in llm.calls.values())
