package com.kellen.rag.model;

import com.kellen.rag.entity.enums.RagGraphNodeType;

import java.util.Map;

/**
 * 与具体图数据库无关的知识图谱节点。
 */
public record KnowledgeGraphNode(
        String id,
        String code,
        RagGraphNodeType type,
        String name,
        String label,
        String documentId,
        String documentCode,
        Integer documentVersion,
        String sourceLocator,
        Map<String, Object> properties,
        int sorting
) {
}
