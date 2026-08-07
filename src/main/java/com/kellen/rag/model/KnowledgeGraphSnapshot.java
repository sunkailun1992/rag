package com.kellen.rag.model;

import java.util.List;

/**
 * 图谱查询结果快照。
 */
public record KnowledgeGraphSnapshot(
        List<KnowledgeGraphNode> nodes,
        List<KnowledgeGraphEdge> edges,
        boolean truncated
) {
}
