package com.kellen.rag.entity.vo;

/** 异步派生图任务状态；已接收与完成分别表达，错误只返回受控代码。 */
public record GraphBuildVO(
        String buildId,
        String knowledgeBase,
        String status,
        int documentCount,
        int processedDocumentCount,
        int nodeCount,
        int edgeCount,
        String errorCode,
        String createdAt,
        String updatedAt
) {
}
