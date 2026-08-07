package com.kellen.rag.store;

import com.kellen.rag.model.KnowledgeGraphProjection;
import com.kellen.rag.model.KnowledgeGraphQuery;
import com.kellen.rag.model.KnowledgeGraphSnapshot;

import java.util.Set;

/**
 * 知识图谱存储端口。
 *
 * <p>业务服务只依赖稳定的节点、关系和查询模型；未来接入 Neo4j 时新增适配器即可，
 * 不需要修改文档投影、Controller 或前端契约。</p>
 */
public interface KnowledgeGraphStore {

    void save(KnowledgeGraphProjection projection);

    void deactivateDocument(String tenantId, String documentId);

    void retainActiveDocuments(String tenantId, String knowledgeBase, Set<String> activeDocumentIds);

    KnowledgeGraphSnapshot query(KnowledgeGraphQuery query);
}
