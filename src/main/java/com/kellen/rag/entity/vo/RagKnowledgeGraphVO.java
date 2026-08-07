package com.kellen.rag.entity.vo;

import java.util.List;
import java.util.Map;

/**
 * 知识图谱查询结果。
 */
public record RagKnowledgeGraphVO(
        String knowledgeBase,
        String viewMode,
        int nodeCount,
        int edgeCount,
        boolean truncated,
        Map<String, Long> nodeTypeCounts,
        Map<String, Long> relationTypeCounts,
        List<RagGraphNodeVO> nodes,
        List<RagGraphEdgeVO> edges
) {
}
