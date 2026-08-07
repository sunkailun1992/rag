package com.kellen.rag.model;

import java.util.Map;

/**
 * 向量检索候选。
 *
 * @param id      向量点 ID
 * @param score   相似度
 * @param payload 原始元数据
 */
public record VectorSearchHit(String id, double score, Map<String, Object> payload) {
}
