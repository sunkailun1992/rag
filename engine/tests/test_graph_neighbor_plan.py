"""固定相关子查询的查找起点；真实算子/延迟由Neo4j PROFILE验收。"""

import re

from rag_engine.graph_retrieval import NEIGHBOR_QUERY


def test_canonical_lookup_is_a_scoped_index_seek_before_candidate_reverse_expansion():
    pattern = (
        r"CALL \(canonical\) \{\s*"
        r"MATCH \(t:__Entity__ \{tenant_id:\$tenant,knowledge_base:\$kb,canonical_name:canonical\}\)\s*"
        r"USING INDEX SEEK t:__Entity__\(tenant_id,knowledge_base,canonical_name\)\s*"
        r"RETURN t\s*\}\s*WITH DISTINCT t\s*"
        r"MATCH \(t\)<-\[:MENTIONS\]-\(candidate:Chunk \{tenant_id:\$tenant,knowledge_base:\$kb\}\)"
    )
    anchored = re.search(pattern, NEIGHBOR_QUERY)
    assert anchored is not None
    assert NEIGHBOR_QUERY.index("WITH DISTINCT s.canonical_name AS canonical") < anchored.start()
    assert NEIGHBOR_QUERY.count("CALL (canonical)") == 1
    assert (
        "candidate"
        not in NEIGHBOR_QUERY[anchored.start() : NEIGHBOR_QUERY.index("RETURN t", anchored.start())]
    )


def test_anchored_plan_retains_seed_and_target_publication_and_source_identity_guards():
    required = [
        "MATCH (d:RagGraphDocument {key:a.documentKey,tenant:$tenant,knowledgeBase:$kb})",
        "WHERE d.activeGeneration IS NOT NULL",
        "generation:d.activeGeneration",
        "point_id:a.pointId,source_key:a.sourceKey,document_id:a.documentId",
        "s.tenant_id=$tenant AND s.knowledge_base=$kb AND s.generation=d.activeGeneration",
        "s.point_id=a.pointId AND s.source_key=a.sourceKey AND s.document_id=a.documentId",
        "WITH DISTINCT s.canonical_name AS canonical ORDER BY canonical LIMIT 40",
        "MATCH (target:RagGraphDocument {tenant:$tenant,knowledgeBase:$kb,",
        "documentId:candidate.document_id,activeGeneration:candidate.generation})",
        "t.generation=target.activeGeneration AND candidate.generation=target.activeGeneration",
        "t.point_id=candidate.point_id AND t.source_key=candidate.source_key",
        "t.document_id=candidate.document_id AND NOT candidate.point_id IN $excluded",
        "RETURN DISTINCT candidate.point_id AS pointId,candidate.source_key AS sourceKey,",
        "ORDER BY pointId LIMIT $limit",
    ]
    assert all(guard in NEIGHBOR_QUERY for guard in required)
