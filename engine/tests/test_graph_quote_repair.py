"""受限slot修复的合成安全回归；无业务正文或模型值。"""

import json

import pytest

from rag_engine.graph_schema import QuoteRepairError, build_quote_repair_plan


def test_wrong_color_mapping_cannot_pair_quote_with_frozen_other_threshold():
    source = "A-B：红档=40～50单位，A-B：黄档=20～30单位。"
    with pytest.raises(QuoteRepairError):
        build_quote_repair_plan("synthetic", source, [["AB：红档", "对应范围", "20～30单位"]])


def test_repair_key_binds_complete_frozen_triplet_not_only_source():
    source = "A-B：红档=40～50单位。"
    a = build_quote_repair_plan("synthetic", source, [["AB：红档", "对应范围", "40～50单位"]])
    b = build_quote_repair_plan("synthetic", source, [["AB：红档", "表示范围", "40～50单位"]])
    assert a.repair_key != b.repair_key
    reply = json.dumps({"repairKey": a.repair_key, "replacements": {a.slots[0].id: a.slots[0].choices[1]}})
    with pytest.raises(QuoteRepairError):
        b.decode(reply)


@pytest.mark.parametrize(
    "source",
    [
        "映射（A-B：红档=40～50单位，A-B：黄档=20～30单位）。",
        "映射（A-B：红档=40～50单位\nA-B：黄档=20～30单位。",
        "映射“A-B：红档=40～50单位\nA-B：黄档=20～30单位。",
    ],
)
def test_brackets_or_unclosed_quote_never_join_distinct_mapping_pairs(source):
    with pytest.raises(QuoteRepairError):
        build_quote_repair_plan("synthetic", source, [["AB：红档", "对应范围", "20～30单位"]])


def test_numeric_thousands_comma_stays_in_one_supported_pair():
    source = "A-B：红档=1,000单位。"
    plan = build_quote_repair_plan("synthetic", source, [["AB：红档", "对应范围", "1,000单位"]])
    reply = json.dumps({"repairKey": plan.repair_key, "replacements": {plan.slots[0].id: "A-B：红档"}})
    assert json.loads(plan.decode(reply)) == [["A-B：红档", "对应范围", "1,000单位"]]


@pytest.mark.parametrize(
    "output",
    ["\ud800", '{"repairKey":NaN,"replacements":{}}', '{"repairKey":"x","repairKey":"y","replacements":{}}'],
)
def test_invalid_unicode_and_duplicate_or_non_json_constants_are_safe(output):
    plan = build_quote_repair_plan("synthetic", "A-B对应目标。", [["AB", "对应", "目标"]])
    with pytest.raises(QuoteRepairError) as caught:
        plan.decode(output)
    assert str(caught.value) in {"RESULT_STRUCTURE", "OUTPUT_TYPE_OR_SIZE"}


@pytest.mark.parametrize("left,right", [("（", "）"), ("“", "”")])
@pytest.mark.parametrize("separator", ["；", ";", "。", "!", "？"])
def test_other_nested_mapping_separators_do_not_merge_pairs(left, right, separator):
    source = f"映射{left}A-B：红档=40～50单位{separator}A-B：黄档=20～30单位{right}。"
    with pytest.raises(QuoteRepairError):
        build_quote_repair_plan("synthetic", source, [["AB：红档", "对应范围", "20～30单位"]])


@pytest.mark.parametrize("separator", ["、", ".", " "])
def test_multiple_assignments_are_not_a_single_pair_unit(separator):
    source = f"映射（A-B：红档=40～50单位{separator}A-B：黄档=20～30单位）。"
    with pytest.raises(QuoteRepairError):
        build_quote_repair_plan("synthetic", source, [["AB：红档", "对应范围", "20～30单位"]])


def test_other_assignment_outside_minimal_pair_does_not_hide_a_valid_witness():
    source = "其他定义=9 A-B：红档=40～50单位 另一公式(x)=3。"
    plan = build_quote_repair_plan("synthetic", source, [["AB：红档", "对应范围", "40～50单位"]])
    assert plan.slots[0].choices == ("", "A-B：红档")


def test_previous_other_mapping_value_cannot_bind_later_subject():
    source = "A-B：黄档=20～30单位 A-B：红档=40～50单位。"
    with pytest.raises(QuoteRepairError):
        build_quote_repair_plan("synthetic", source, [["AB：红档", "对应范围", "20～30单位"]])
