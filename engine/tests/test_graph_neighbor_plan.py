"""固定相关子查询的查找起点与每个名称的候选上限；真实算子/延迟由Neo4j PROFILE验收。"""

import re

from rag_engine.graph_retrieval import NEIGHBOR_QUERY

SUBQUERY_START = "CALL (active, canonical) {"


def _subquery_bounds():
    start = NEIGHBOR_QUERY.index(SUBQUERY_START)
    return start, NEIGHBOR_QUERY.index("\n}\n", start)


def test_canonical_lookup_is_a_scoped_index_seek_before_candidate_reverse_expansion():
    pattern = (
        r"CALL \(active, canonical\) \{\s*"
        r"MATCH \(t:__Entity__ \{tenant_id:\$tenant,knowledge_base:\$kb,canonical_name:canonical\}\)\s*"
        r"USING INDEX SEEK t:__Entity__\(tenant_id,knowledge_base,canonical_name\)\s*"
        r"WHERE \[t\.document_id, t\.generation\] IN active AND NOT t\.point_id IN \$excluded\s*"
        r"MATCH \(t\)<-\[:MENTIONS\]-\(candidate:Chunk \{tenant_id:\$tenant,knowledge_base:\$kb\}\)"
    )
    anchored = re.search(pattern, NEIGHBOR_QUERY)
    assert anchored is not None
    assert NEIGHBOR_QUERY.index("WITH DISTINCT active, s.canonical_name AS canonical") < anchored.start()
    assert NEIGHBOR_QUERY.count("CALL (active, canonical)") == 1


def test_each_name_keeps_at_most_limit_candidates_in_point_order_before_global_limit():
    start, end = _subquery_bounds()
    block = NEIGHBOR_QUERY[start:end]
    # 全局前 $limit 个（按 pointId）必在各名称自己的前 $limit 个之中，截断不改变结果。
    assert block.index("WITH DISTINCT candidate ORDER BY candidate.point_id LIMIT $limit") > block.index(
        "MATCH (t)<-[:MENTIONS]-(candidate:Chunk"
    )
    assert block.rstrip().endswith("RETURN candidate")
    tail = NEIGHBOR_QUERY[end:]
    assert tail.lstrip("\n}").lstrip().startswith("WITH DISTINCT candidate")
    assert tail.rstrip().endswith("ORDER BY pointId LIMIT $limit")


def test_active_documents_are_resolved_once_instead_of_a_label_scan_per_candidate():
    prefix = NEIGHBOR_QUERY[: NEIGHBOR_QUERY.index("UNWIND $seeds AS a")]
    assert "MATCH (doc:RagGraphDocument {tenant:$tenant,knowledgeBase:$kb})" in prefix
    assert "WHERE doc.activeGeneration IS NOT NULL" in prefix
    assert "WITH collect([doc.documentId, doc.activeGeneration]) AS active" in prefix
    assert "MATCH (target:RagGraphDocument" not in NEIGHBOR_QUERY


def test_anchored_plan_retains_seed_and_target_publication_and_source_identity_guards():
    required = [
        "MATCH (d:RagGraphDocument {key:a.documentKey,tenant:$tenant,knowledgeBase:$kb})",
        "WHERE d.activeGeneration IS NOT NULL",
        "generation:d.activeGeneration",
        "point_id:a.pointId,source_key:a.sourceKey,document_id:a.documentId",
        "s.tenant_id=$tenant AND s.knowledge_base=$kb AND s.generation=d.activeGeneration",
        "s.point_id=a.pointId AND s.source_key=a.sourceKey AND s.document_id=a.documentId",
        "WITH DISTINCT active, s.canonical_name AS canonical ORDER BY canonical LIMIT 40",
        "[t.document_id, t.generation] IN active AND NOT t.point_id IN $excluded",
        "candidate.generation=t.generation AND t.point_id=candidate.point_id",
        "t.source_key=candidate.source_key AND t.document_id=candidate.document_id",
        "RETURN DISTINCT candidate.point_id AS pointId,candidate.source_key AS sourceKey,",
        "ORDER BY pointId LIMIT $limit",
    ]
    assert all(guard in NEIGHBOR_QUERY for guard in required)
