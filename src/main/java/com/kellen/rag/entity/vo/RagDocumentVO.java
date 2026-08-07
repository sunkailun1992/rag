package com.kellen.rag.entity.vo;

import io.swagger.v3.oas.annotations.media.Schema;

/**
 * RAG 文档摘要。
 */
@Schema(description = "RAG 文档摘要")
public record RagDocumentVO(
        String documentId,
        String knowledgeBase,
        String documentCode,
        String title,
        String sourceName,
        String sourceType,
        int documentVersion,
        String status,
        int chunkCount,
        int duplicateChunkCount,
        String fileFingerprint,
        String ingestedAt
) {
}
