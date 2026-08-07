package com.kellen.rag.model;

import java.util.List;

/**
 * 单个已激活向量文档的知识图谱投影。
 */
public record KnowledgeGraphProjection(
        String tenantId,
        String knowledgeBase,
        String documentId,
        String documentCode,
        int documentVersion,
        List<KnowledgeGraphNode> nodes,
        List<KnowledgeGraphEdge> edges
) {
}
