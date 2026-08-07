package com.kellen.rag.model;

import java.util.Map;

/**
 * 待写入向量库的点。
 *
 * @param id      确定性 UUID
 * @param vector  稠密向量
 * @param payload 可追溯且显式包含租户的元数据
 */
public record VectorPoint(String id, float[] vector, Map<String, Object> payload) {
}
