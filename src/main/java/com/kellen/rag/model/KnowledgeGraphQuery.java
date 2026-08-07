package com.kellen.rag.model;

import com.kellen.rag.entity.enums.RagGraphNodeType;

/**
 * 图谱存储查询条件。
 */
public record KnowledgeGraphQuery(
        String tenantId,
        String knowledgeBase,
        String searchText,
        RagGraphNodeType nodeType,
        KnowledgeGraphViewMode viewMode,
        int limit
) {
}
