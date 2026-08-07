package com.kellen.rag.service;

import com.kellen.rag.entity.enums.RagGraphNodeType;
import com.kellen.rag.entity.vo.RagGraphSyncVO;
import com.kellen.rag.entity.vo.RagKnowledgeGraphVO;
import com.kellen.rag.model.KnowledgeGraphViewMode;

/**
 * RAG 知识图谱同步与查询服务。
 */
public interface RagKnowledgeGraphService {

    RagGraphSyncVO sync(String knowledgeBase);

    RagGraphSyncVO syncDocument(String documentId);

    void deactivateDocument(String documentId);

    RagKnowledgeGraphVO query(String knowledgeBase, String searchText, RagGraphNodeType nodeType,
                              KnowledgeGraphViewMode viewMode, Integer limit);
}
