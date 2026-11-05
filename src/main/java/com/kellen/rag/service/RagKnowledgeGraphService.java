package com.kellen.rag.service;

import com.kellen.rag.entity.enums.RagGraphNodeType;
import com.kellen.rag.entity.vo.RagGraphSyncVO;
import com.kellen.rag.entity.vo.GraphBuildVO;
import com.kellen.rag.entity.vo.RagKnowledgeGraphVO;
import com.kellen.rag.model.KnowledgeGraphViewMode;
import com.kellen.rag.model.GraphRemovalFence;

/**
 * RAG 知识图谱同步与查询服务。
 */
public interface RagKnowledgeGraphService {

    /** 只有异步派生后端将任务失败与已提交向量结果分开报告。 */
    default boolean isAsynchronous() {
        return false;
    }

    RagGraphSyncVO sync(String knowledgeBase);

    RagGraphSyncVO syncDocument(String documentId);

    void deactivateDocument(String documentId);

    /** 建立删除屏障并返回仅本次失败补偿可用的票据；同步后端保持原调用。 */
    default GraphRemovalFence deactivateDocumentWithFence(String documentId) {
        deactivateDocument(documentId);
        return null;
    }

    /** 只有向量删除失败的调用链可以提交屏障票据，普通同步不接受此能力。 */
    default RagGraphSyncVO restoreDocument(String documentId, GraphRemovalFence fence) {
        return syncDocument(documentId);
    }

    /** 仅按当前可信租户和知识库读取异步构建状态。 */
    GraphBuildVO build(String buildId, String knowledgeBase);

    RagKnowledgeGraphVO query(String knowledgeBase, String searchText, RagGraphNodeType nodeType,
                              KnowledgeGraphViewMode viewMode, Integer limit);
}
