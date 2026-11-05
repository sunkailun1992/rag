package com.kellen.rag.entity.vo;

/**
 * 从活动向量切片同步知识图谱的结果。
 */
public record RagGraphSyncVO(
        String knowledgeBase,
        int documentCount,
        int nodeCount,
        int edgeCount,
        String buildId,
        String status
) {
    public RagGraphSyncVO(String knowledgeBase, int documentCount, int nodeCount, int edgeCount) {
        this(knowledgeBase, documentCount, nodeCount, edgeCount, null, null);
    }

    public static RagGraphSyncVO fromBuild(GraphBuildVO build) {
        return new RagGraphSyncVO(build.knowledgeBase(), build.documentCount(), build.nodeCount(),
                build.edgeCount(), build.buildId(), build.status());
    }
}
