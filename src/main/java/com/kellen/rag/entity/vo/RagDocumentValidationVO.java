package com.kellen.rag.entity.vo;

import io.swagger.v3.oas.annotations.media.Schema;

/**
 * 向量读回校验结果。
 *
 * @param valid                  是否全部通过
 * @param expectedChunkCount     声明切片数
 * @param storedPointCount       实际点数
 * @param validPointCount        内容指纹和维度均正确的点数
 * @param invalidContentHashCount 内容指纹异常数
 * @param invalidDimensionCount  向量维度异常数
 */
@Schema(description = "RAG 文档向量读回校验")
public record RagDocumentValidationVO(
        boolean valid,
        int expectedChunkCount,
        int storedPointCount,
        int validPointCount,
        int invalidContentHashCount,
        int invalidDimensionCount
) {
}
