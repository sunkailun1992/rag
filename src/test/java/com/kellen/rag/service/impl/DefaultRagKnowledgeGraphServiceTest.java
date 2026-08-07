package com.kellen.rag.service.impl;

import com.kellen.rag.client.VectorStoreClient;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.graph.KnowledgeGraphProjector;
import com.kellen.rag.model.KnowledgeGraphProjection;
import com.kellen.rag.model.KnowledgeGraphQuery;
import com.kellen.rag.model.KnowledgeGraphSnapshot;
import com.kellen.rag.model.KnowledgeGraphViewMode;
import com.kellen.rag.model.StoredVectorPoint;
import com.kellen.rag.store.KnowledgeGraphStore;
import com.kellen.utils.context.TenantContextHolder;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@SuppressWarnings("unchecked")
class DefaultRagKnowledgeGraphServiceTest {

    @AfterEach
    void clearTenant() {
        TenantContextHolder.clear();
    }

    @Test
    void shouldSyncOnlyCurrentTenantActiveVectors() {
        VectorStoreClient vectorStore = mock(VectorStoreClient.class);
        KnowledgeGraphStore graphStore = mock(KnowledgeGraphStore.class);
        when(vectorStore.collectionExists()).thenReturn(true);
        when(vectorStore.count(anyMap())).thenReturn(1L);
        when(vectorStore.scroll(anyMap(), org.mockito.ArgumentMatchers.anyInt(),
                org.mockito.ArgumentMatchers.eq(false))).thenReturn(List.of(point("tenant-a")));
        TenantContextHolder.setTenantId("tenant-a");

        var service = service(vectorStore, graphStore);
        var result = service.sync("general-knowledge");

        assertThat(result.documentCount()).isEqualTo(1);
        ArgumentCaptor<Map<String, Object>> filter = ArgumentCaptor.forClass(Map.class);
        verify(vectorStore).count(filter.capture());
        assertThat(filter.getValue()).containsEntry("tenant_id", "tenant-a")
                .containsEntry("document_status", "ACTIVE");
        ArgumentCaptor<KnowledgeGraphProjection> projection =
                ArgumentCaptor.forClass(KnowledgeGraphProjection.class);
        verify(graphStore).save(projection.capture());
        assertThat(projection.getValue().tenantId()).isEqualTo("tenant-a");
        verify(graphStore).retainActiveDocuments("tenant-a", "general-knowledge", Set.of("document-1"));
    }

    @Test
    void shouldRejectGraphQueryWithoutTenantContext() {
        var service = service(mock(VectorStoreClient.class), mock(KnowledgeGraphStore.class));

        assertThrows(IllegalStateException.class,
                () -> service.query("general-knowledge", null, null, KnowledgeGraphViewMode.OVERVIEW, 100));
    }

    @Test
    void shouldPassBoundedTenantQueryToStoragePort() {
        KnowledgeGraphStore graphStore = mock(KnowledgeGraphStore.class);
        when(graphStore.query(org.mockito.ArgumentMatchers.any()))
                .thenReturn(new KnowledgeGraphSnapshot(List.of(), List.of(), false));
        TenantContextHolder.setTenantId("tenant-a");

        service(mock(VectorStoreClient.class), graphStore)
                .query("general-knowledge", "风险", null, KnowledgeGraphViewMode.OVERVIEW, 9999);

        ArgumentCaptor<KnowledgeGraphQuery> query = ArgumentCaptor.forClass(KnowledgeGraphQuery.class);
        verify(graphStore).query(query.capture());
        assertThat(query.getValue().tenantId()).isEqualTo("tenant-a");
        assertThat(query.getValue().limit()).isEqualTo(500);
    }

    private DefaultRagKnowledgeGraphService service(VectorStoreClient vectorStore,
                                                     KnowledgeGraphStore graphStore) {
        RagVectorProperties properties = new RagVectorProperties();
        properties.setDefaultKnowledgeBase("general-knowledge");
        return new DefaultRagKnowledgeGraphService(properties, vectorStore,
                new KnowledgeGraphProjector(), graphStore);
    }

    private StoredVectorPoint point(String tenantId) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("tenant_id", tenantId);
        payload.put("knowledge_base", "general-knowledge");
        payload.put("document_id", "document-1");
        payload.put("document_code", "structured-knowledge");
        payload.put("document_title", "结构化知识库");
        payload.put("document_version", 1);
        payload.put("section_path", "对齐结果");
        payload.put("chunk_id", "chunk-1");
        payload.put("source_locator", "第2行");
        payload.put("chunk_type", "xlsx-row");
        payload.put("content", "文档: 结构化知识库\n位置: 对齐结果 / 第2行"
                + "\n表头语义与记录:\n- 风险等级: 高\n- 建议: 建议A");
        return new StoredVectorPoint("point-1", payload, 0);
    }
}
