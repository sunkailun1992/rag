package com.kellen.rag.model;

/**
 * 尚未生成向量的结构化知识切片。
 *
 * @param chunkType  切片类型
 * @param sectionPath 标题或工作表路径
 * @param locator    原文定位信息
 * @param content    保留表头语义的完整分析文本
 */
public record KnowledgeChunkDraft(String chunkType, String sectionPath, String locator, String content) {
}
