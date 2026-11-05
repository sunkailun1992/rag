package com.kellen.rag.model;

/** Java 从当前租户活动 Qdrant 切片取得的不可变来源身份，不接受浏览器提供。 */
public record ActiveGraphSource(
        String pointId,
        String documentId,
        String documentCode,
        int documentVersion,
        long documentRevision,
        String chunkId,
        String contentSha256
) {
}
