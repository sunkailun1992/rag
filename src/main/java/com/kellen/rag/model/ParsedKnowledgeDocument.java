package com.kellen.rag.model;

import java.util.List;

/**
 * 文档解析结果。
 *
 * @param title       文档标题
 * @param sourceName  安全化后的源文件名
 * @param sourceType  xlsx、docx 或 txt
 * @param chunks      结构化切片
 */
public record ParsedKnowledgeDocument(String title, String sourceName, String sourceType,
                                      List<KnowledgeChunkDraft> chunks) {
}
