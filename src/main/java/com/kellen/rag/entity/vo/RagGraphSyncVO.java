package com.kellen.rag.entity.vo;

/**
 * 从活动向量切片同步知识图谱的结果。
 */
public record RagGraphSyncVO(
        String knowledgeBase,
        int documentCount,
        int nodeCount,
        int edgeCount
) {
}
