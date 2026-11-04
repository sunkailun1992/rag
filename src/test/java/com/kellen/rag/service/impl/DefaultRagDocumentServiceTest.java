package com.kellen.rag.service.impl;

import com.kellen.rag.client.EmbeddingClient;
import com.kellen.rag.client.VectorStoreClient;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.parser.KnowledgeDocumentParser;
import com.kellen.rag.service.RagKnowledgeGraphService;
import com.kellen.utils.context.TenantContextHolder;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class DefaultRagDocumentServiceTest {

    @AfterEach
    void clearTenantContext() {
        TenantContextHolder.clear();
    }

    @Test
    void shouldReturnEmptyDocumentListWhenCollectionHasNotBeenCreated() {
        VectorStoreClient vectorStoreClient = mock(VectorStoreClient.class);
        when(vectorStoreClient.collectionExists()).thenReturn(false);
        DefaultRagDocumentService service = new DefaultRagDocumentService(
                new RagVectorProperties(),
                mock(KnowledgeDocumentParser.class),
                mock(EmbeddingClient.class),
                vectorStoreClient,
                mock(RagKnowledgeGraphService.class)
        );
        TenantContextHolder.setTenantId("tenant-test");

        assertTrue(service.list(null).isEmpty());
        verify(vectorStoreClient).collectionExists();
        verify(vectorStoreClient, never()).scroll(org.mockito.ArgumentMatchers.anyMap(),
                org.mockito.ArgumentMatchers.anyInt(), org.mockito.ArgumentMatchers.anyBoolean());
    }

    @Test
    void shouldHideGraphBeforeDeactivatingVectorDocument() {
        VectorStoreClient vectorStoreClient = mock(VectorStoreClient.class);
        RagKnowledgeGraphService graphService = mock(RagKnowledgeGraphService.class);
        when(vectorStoreClient.count(anyMap())).thenReturn(1L);
        TenantContextHolder.setTenantId("tenant-test");
        DefaultRagDocumentService service = service(vectorStoreClient, graphService);

        assertTrue(service.deactivate("document-1"));

        var ordered = inOrder(graphService, vectorStoreClient);
        ordered.verify(graphService).deactivateDocument("document-1");
        ordered.verify(vectorStoreClient).setPayload(anyMap(),
                org.mockito.ArgumentMatchers.eq(java.util.Map.of("document_status", "DELETED")));
    }

    @Test
    void shouldTryToRestoreGraphWhenVectorDeactivationFails() {
        VectorStoreClient vectorStoreClient = mock(VectorStoreClient.class);
        RagKnowledgeGraphService graphService = mock(RagKnowledgeGraphService.class);
        when(vectorStoreClient.count(anyMap())).thenReturn(1L);
        org.mockito.Mockito.doThrow(new IllegalStateException("vector unavailable"))
                .when(vectorStoreClient).setPayload(anyMap(), anyMap());
        TenantContextHolder.setTenantId("tenant-test");

        assertThrows(IllegalStateException.class,
                () -> service(vectorStoreClient, graphService).deactivate("document-1"));

        verify(graphService).syncDocument("document-1");
    }

    @Test
    void shouldScopeEveryChunkPageAndOmitRawGovernanceMetadata() {
        VectorStoreClient vectors = mock(VectorStoreClient.class);
        when(vectors.collectionExists()).thenReturn(true);
        var payload = new java.util.LinkedHashMap<String,Object>();
        payload.put("tenant_id", "tenant-test"); payload.put("document_id", "doc-1");
        payload.put("chunk_id", "chunk-1"); payload.put("chunk_index", 0); payload.put("content", "安全文本");
        payload.put("source_metadata", java.util.Map.of("page", 1, "secret", "must-not-return"));
        when(vectors.scrollPage(anyMap(), org.mockito.ArgumentMatchers.eq(20), org.mockito.ArgumentMatchers.isNull()))
                .thenReturn(new com.kellen.rag.model.VectorPointPage(java.util.List.of(
                        new com.kellen.rag.model.StoredVectorPoint("id", payload, 0)), null));
        TenantContextHolder.setTenantId("tenant-test");
        var result = service(vectors, mock(RagKnowledgeGraphService.class)).chunks("doc-1",20,null);
        org.junit.jupiter.api.Assertions.assertEquals(java.util.Map.of("page",1),result.items().get(0).sourceMetadata());
        verify(vectors).scrollPage(java.util.Map.of("tenant_id","tenant-test","document_id","doc-1"),20,null);
        assertThrows(IllegalArgumentException.class, () -> service(vectors, mock(RagKnowledgeGraphService.class)).chunks("doc-1",101,null));
        assertThrows(IllegalArgumentException.class, () -> service(vectors, mock(RagKnowledgeGraphService.class)).chunks("doc-1",20,"invalid"));
        payload.put("tenant_id","tenant-other");
        assertThrows(IllegalStateException.class, () -> service(vectors, mock(RagKnowledgeGraphService.class)).chunks("doc-1",20,null));
    }

    private DefaultRagDocumentService service(VectorStoreClient vectorStoreClient,
                                               RagKnowledgeGraphService graphService) {
        return new DefaultRagDocumentService(
                new RagVectorProperties(),
                mock(KnowledgeDocumentParser.class),
                mock(EmbeddingClient.class),
                vectorStoreClient,
                graphService
        );
    }
}
