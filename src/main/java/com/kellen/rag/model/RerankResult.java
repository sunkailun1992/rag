package com.kellen.rag.model;

/**
 * 重排结果。
 *
 * @param index 输入候选下标
 * @param score 重排相关度
 */
public record RerankResult(int index, double score) {
}
