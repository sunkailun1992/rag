package com.kellen.rag.entity.vo;

import io.swagger.v3.oas.annotations.media.Schema;

/**
 * 文档摄取结果。
 */
@Schema(description = "RAG 文档摄取结果")
public record RagDocumentIngestionVO(
        RagDocumentVO document,
        boolean idempotent,
        RagDocumentValidationVO validation
) {
}
