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

    RagGraphSyncVO sync(String knowledgeBase);

    /** 同一次提交重试复用不透明键；省略时保留既有的显式重建语义。 */
    RagGraphSyncVO sync(String knowledgeBase, String submissionKey);

    /** 只读查回当前租户、知识库内的原提交；不存在时返回空。 */
    GraphBuildVO submission(String submissionKey, String knowledgeBase);

    RagGraphSyncVO syncDocument(String documentId);

    void deactivateDocument(String documentId);

    /** 建立删除屏障并返回仅本次失败补偿可用的票据。 */
    GraphRemovalFence deactivateDocumentWithFence(String documentId);

    /** 只有向量删除失败的调用链可以提交屏障票据，普通同步不接受此能力。 */
    RagGraphSyncVO restoreDocument(String documentId, GraphRemovalFence fence);

    /** 仅按当前可信租户和知识库读取异步构建状态。 */
    GraphBuildVO build(String buildId, String knowledgeBase);

    RagKnowledgeGraphVO query(String knowledgeBase, String searchText, RagGraphNodeType nodeType,
                              KnowledgeGraphViewMode viewMode, Integer limit);
}
