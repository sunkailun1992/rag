package com.kellen.rag.model;

import java.util.Map;

/**
 * 尚未生成向量的结构化知识切片。
 *
 * @param chunkType  切片类型
 * @param sectionPath 标题或工作表路径
 * @param locator    原文定位信息
 * @param content    保留表头语义的完整分析文本
 * @param metadata   PDF/OCR 来源页码、区块和坐标；不能包含租户或文档状态
 */
public record KnowledgeChunkDraft(String chunkType, String sectionPath, String locator, String content,
                                  Map<String, Object> metadata) {
    public KnowledgeChunkDraft {
        metadata = metadata == null ? Map.of() : Map.copyOf(metadata);
    }

    /** 兼容现有 Office 切片调用，不人为补造不存在的页码。 */
    public KnowledgeChunkDraft(String chunkType, String sectionPath, String locator, String content) {
        this(chunkType, sectionPath, locator, content, Map.of());
    }
}
