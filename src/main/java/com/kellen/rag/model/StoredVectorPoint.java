package com.kellen.rag.model;

import java.util.Map;

/**
 * 从 Qdrant 读回的点。
 *
 * @param id              点 ID
 * @param payload         元数据
 * @param vectorDimension 向量维度，未请求向量时为 0
 */
public record StoredVectorPoint(String id, Map<String, Object> payload, int vectorDimension) {
}
