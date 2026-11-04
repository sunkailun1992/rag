package com.kellen.rag.entity.vo;

import java.util.List;
import java.util.Map;

/** 可见文档的切片预览，不包含向量、租户主键或原始存储 payload。 */
public record RagChunkPageVO(String documentId, List<Chunk> items, String nextCursor) {
    public record Chunk(String chunkId, int chunkIndex, String chunkType, String sectionPath,
                        String sourceName, String sourceLocator, String content,
                        Map<String, Object> sourceMetadata, List<String> duplicateLocators) { }
}
