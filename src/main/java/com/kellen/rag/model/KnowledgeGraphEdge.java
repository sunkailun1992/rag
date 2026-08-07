package com.kellen.rag.model;

import com.kellen.rag.entity.enums.RagGraphRelationType;

import java.util.Map;

/**
 * 与具体图数据库无关的知识图谱有向关系。
 */
public record KnowledgeGraphEdge(
        String id,
        String code,
        String sourceNodeId,
        String targetNodeId,
        RagGraphRelationType type,
        String label,
        String documentId,
        String documentCode,
        int documentVersion,
        String sourceLocator,
        Map<String, Object> properties,
        int sorting
) {
}
