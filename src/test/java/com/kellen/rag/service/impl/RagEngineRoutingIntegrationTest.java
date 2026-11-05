package com.kellen.rag.service.impl;

import com.kellen.rag.client.EmbeddingClient;
import com.kellen.rag.client.RagEngineClient;
import com.kellen.rag.client.RerankClient;
import com.kellen.rag.client.VectorStoreClient;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.exception.ExternalServiceException;
import com.kellen.rag.model.KnowledgeChunkDraft;
import com.kellen.rag.model.ParsedKnowledgeDocument;
import com.kellen.rag.model.RerankResult;
import com.kellen.rag.model.StoredVectorPoint;
import com.kellen.rag.model.VectorPoint;
import com.kellen.rag.model.VectorSearchHit;
import com.kellen.rag.parser.KnowledgeDocumentParser;
import com.kellen.rag.service.RagKnowledgeGraphService;
import com.kellen.rpc.rag.RagRetrievalRpcDTO;
import com.kellen.rpc.rag.RagRetrievalRpcRequest;
import com.kellen.utils.context.TenantContextHolder;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyDouble;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * 真实服务 Bean 的迁移路由与摄取边界。外部端口使用可控替身，
 * 验证请求参数、上下文组装、失败路径和持久化副作用，而非仅验证替身返回值。
 */
@SpringBootTest(classes = RagEngineRoutingIntegrationTest.TestConfig.class,
        webEnvironment = SpringBootTest.WebEnvironment.NONE, properties = {
        "spring.config.name=rag-engine-routing-isolated",
        "spring.cloud.nacos.config.enabled=false",
        "spring.cloud.nacos.config.import-check.enabled=false",
        "spring.cloud.nacos.discovery.enabled=false"
})
class RagEngineRoutingIntegrationTest {
    @Autowired private DefaultRagRetrievalService retrievalService;
    @Autowired private DefaultRagDocumentService documentService;
    @Autowired private RagVectorProperties properties;
    @MockitoBean private RagEngineClient engine;
    @MockitoBean private EmbeddingClient embedding;
    @MockitoBean private VectorStoreClient vectors;
    @MockitoBean private RerankClient reranker;
    @MockitoBean private KnowledgeDocumentParser parser;
    @MockitoBean private RagKnowledgeGraphService graphs;
    private String tenant;

    @Configuration(proxyBeanMethods = false)
    @Import({DefaultRagRetrievalService.class, DefaultRagDocumentService.class, RagVectorProperties.class})
    static class TestConfig {
    }

    @BeforeEach
    void prepareScope() {
        tenant = UUID.randomUUID().toString();
        TenantContextHolder.setTenantId(tenant);
        properties.setEnabled(true);
        properties.setDefaultKnowledgeBase("routing-test");
        properties.setTopK(6);
        properties.setCandidateCount(30);
        properties.setSimilarityThreshold(.2D);
        properties.setMaxContextCharacters(12000);
    }

    @AfterEach
    void clearScope() {
        TenantContextHolder.clear();
    }

    @Test
    void shouldBuildExistingRpcContextFromEngineHitsWithoutLegacyCalls() {
        when(engine.isRetrievalEnabled()).thenReturn(true);
        VectorSearchHit hit = hit("Engine evidence", "source.pdf", "第2页");
        when(engine.retrieve(anyString(), anyString(), anyInt(), anyInt(), anyDouble(), anyInt()))
                .thenReturn(List.of(hit));
        RagRetrievalRpcRequest request = request();
        request.setQuery("  synthetic query  ");

        RagRetrievalRpcDTO result = retrievalService.retrieve(request);

        assertThat(result.isMatched()).isTrue();
        assertThat(result.getMessage()).isEqualTo("ok");
        assertThat(result.getContext()).isEqualTo("[来源: source.pdf / 第2页]\nEngine evidence");
        assertThat(result.getSourceCount()).isEqualTo(1);
        assertThat(result.getChunks()).hasSize(1);
        assertThat(result.getChunks().get(0).getDocumentId()).isEqualTo(hit.payload().get("document_id"));
        verify(engine).retrieve("synthetic query", "routing-test", 6, 30, .2D, 12000);
        verifyNoInteractions(embedding, vectors, reranker);
    }

    @Test
    void shouldUseLegacyRouteWhenEngineSwitchIsOff() {
        when(engine.isRetrievalEnabled()).thenReturn(false);
        float[] queryVector = new float[]{1, 0};
        when(embedding.embedQuery("synthetic query")).thenReturn(queryVector);
        when(vectors.search(eq(queryVector), anyMap(), eq(30), eq(.2D)))
                .thenReturn(List.of(hit("Legacy evidence", "source.docx", "段落1")));
        when(reranker.rerank("synthetic query", List.of("Legacy evidence"), 6))
                .thenReturn(List.of(new RerankResult(0, .8D)));

        RagRetrievalRpcDTO result = retrievalService.retrieve(request());

        assertThat(result.getContext()).contains("Legacy evidence");
        verify(vectors).search(queryVector, Map.of("tenant_id", tenant, "knowledge_base", "routing-test",
                "document_status", "ACTIVE"), 30, .2D);
        verify(engine, never()).retrieve(anyString(), anyString(), anyInt(), anyInt(), anyDouble(), anyInt());
    }

    @Test
    void shouldNotCallAnyExternalPortWhenRagIsDisabled() {
        properties.setEnabled(false);

        RagRetrievalRpcDTO result = retrievalService.retrieve(request());

        assertThat(result.isEnabled()).isFalse();
        assertThat(result.isMatched()).isFalse();
        assertThat(result.getContext()).isEmpty();
        assertThat(result.getMessage()).isEqualTo("RAG retrieval is disabled");
        verifyNoInteractions(engine, embedding, vectors, reranker);
    }

    @Test
    void shouldNotAppendLegacyWorkAfterEngineFailure() {
        when(engine.isRetrievalEnabled()).thenReturn(true);
        when(engine.retrieve(anyString(), anyString(), anyInt(), anyInt(), anyDouble(), anyInt()))
                .thenThrow(ExternalServiceException.httpFailure("RAG engine", 504));

        RagRetrievalRpcDTO result = retrievalService.retrieve(request());

        assertThat(result.isMatched()).isFalse();
        assertThat(result.getContext()).isEmpty();
        assertThat(result.getChunks()).isEmpty();
        assertThat(result.getMessage()).isEqualTo("RAG retrieval is temporarily unavailable");
        verifyNoInteractions(embedding, vectors, reranker);
    }

    @Test
    void shouldReturnEngineNoMatchWithoutRetryingLegacy() {
        when(engine.isRetrievalEnabled()).thenReturn(true);
        when(engine.retrieve(anyString(), anyString(), anyInt(), anyInt(), anyDouble(), anyInt()))
                .thenReturn(List.of());

        RagRetrievalRpcDTO result = retrievalService.retrieve(request());

        assertThat(result.getMessage()).isEqualTo("no knowledge matched");
        assertThat(result.isMatched()).isFalse();
        verifyNoInteractions(embedding, vectors, reranker);
    }

    @Test
    void shouldRejectMissingTenantBeforeExternalWork() {
        TenantContextHolder.clear();

        RagRetrievalRpcDTO result = retrievalService.retrieve(request());

        assertThat(result.getMessage()).isEqualTo("tenant context is missing");
        assertThat(result.getContext()).isEmpty();
        verifyNoInteractions(engine, embedding, vectors, reranker);
    }

    @Test
    void shouldPreserveContextBudgetAndWholeChunkAssemblyContract() {
        when(engine.isRetrievalEnabled()).thenReturn(true);
        String first = "A".repeat(600);
        String second = "B".repeat(600);
        when(engine.retrieve(anyString(), anyString(), anyInt(), anyInt(), anyDouble(), anyInt()))
                .thenReturn(List.of(hit(first, "first.pdf", "第1页"), hit(second, "second.pdf", "第1页")));
        RagRetrievalRpcRequest request = request();
        request.setMaxContextCharacters(1000);

        RagRetrievalRpcDTO result = retrievalService.retrieve(request);

        assertThat(result.getContext()).contains(first).doesNotContain(second);
        assertThat(result.getContext().length()).isLessThanOrEqualTo(1000);
        // RPC chunks 仍返回完整命中列表；context 继续按原 Java 契约整块截取。
        assertThat(result.getChunks()).hasSize(2);
        verify(engine).retrieve("synthetic query", "routing-test", 6, 30, .2D, 1000);
    }

    @Test
    void shouldClampRequestBudgetsBeforeEngineCallAndUseConfiguredDefault() {
        when(engine.isRetrievalEnabled()).thenReturn(true);
        when(engine.retrieve(anyString(), anyString(), anyInt(), anyInt(), anyDouble(), anyInt()))
                .thenReturn(List.of());
        properties.setCandidateCount(500);
        RagRetrievalRpcRequest request = request();
        request.setTopK(500);
        request.setSimilarityThreshold(-1D);
        request.setMaxContextCharacters(1);

        retrievalService.retrieve(request);

        verify(engine).retrieve("synthetic query", "routing-test", 20, 100, 0D, 1000);
        request.setMaxContextCharacters(50000);
        retrievalService.retrieve(request);
        verify(engine).retrieve("synthetic query", "routing-test", 20, 100, 0D, 30000);
        request.setMaxContextCharacters(null);
        properties.setMaxContextCharacters(9000);
        retrievalService.retrieve(request);
        verify(engine).retrieve("synthetic query", "routing-test", 20, 100, 0D, 9000);
    }

    @Test
    void shouldKeepParserMetadataOutsideAuthoritativeTenantAndVersionFields() {
        String injectedTenant = UUID.randomUUID().toString();
        Map<String, Object> metadata = Map.of("tenant_id", injectedTenant, "knowledge_base", "other",
                "document_status", "ACTIVE", "document_version", 999, "page", 1);
        when(parser.parse(anyString(), anyString(), any(byte[].class))).thenReturn(
                new ParsedKnowledgeDocument("Synthetic", "fixture.pdf", "pdf", List.of(
                        new KnowledgeChunkDraft("pdf-text", "第1页", "第1页", "Synthetic evidence", metadata))));
        when(embedding.embedDocuments(List.of("Synthetic evidence")))
                .thenReturn(List.of(new float[properties.getEmbeddingDimension()]));
        List<StoredVectorPoint> stored = new ArrayList<>();
        List<Map<String, Object>> initialWrites = new ArrayList<>();
        doAnswer(invocation -> {
            List<VectorPoint> points = invocation.getArgument(0);
            for (VectorPoint point : points) {
                initialWrites.add(new LinkedHashMap<>(point.payload()));
                stored.add(new StoredVectorPoint(point.id(), new LinkedHashMap<>(point.payload()),
                        properties.getEmbeddingDimension()));
            }
            return null;
        }).when(vectors).upsert(anyList());
        when(vectors.scroll(anyMap(), anyInt(), anyBoolean())).thenAnswer(invocation -> {
            Map<String, Object> filter = invocation.getArgument(0);
            int limit = invocation.getArgument(1);
            return stored.stream().filter(point -> matches(point, filter)).limit(limit).toList();
        });
        doAnswer(invocation -> {
            Map<String, Object> filter = invocation.getArgument(0);
            Map<String, Object> update = invocation.getArgument(1);
            stored.stream().filter(point -> matches(point, filter)).forEach(point -> point.payload().putAll(update));
            return null;
        }).when(vectors).setPayload(anyMap(), anyMap());
        MockMultipartFile file = new MockMultipartFile("file", "fixture.pdf", "application/pdf",
                "synthetic fixture".getBytes(StandardCharsets.UTF_8));

        var result = documentService.ingest(file, "routing-test", "synthetic-doc", "Synthetic");

        assertThat(result.validation().valid()).isTrue();
        assertThat(initialWrites).hasSize(1);
        assertThat(initialWrites.get(0)).containsEntry("tenant_id", tenant)
                .containsEntry("knowledge_base", "routing-test").containsEntry("document_version", 1)
                .containsEntry("document_status", "STAGING").containsEntry("source_metadata", metadata);
        assertThat(stored.get(0).payload()).containsEntry("document_status", "ACTIVE")
                .containsEntry("tenant_id", tenant);
        verify(graphs).syncDocument(result.document().documentId());
        verifyNoInteractions(engine, reranker);
    }

    private boolean matches(StoredVectorPoint point, Map<String, Object> filter) {
        return filter.entrySet().stream().allMatch(entry -> entry.getValue().equals(point.payload().get(entry.getKey())));
    }

    @Test
    void shouldKeepValidatedActiveVectorsWhenGraphQueueRejectsUpload() {
        when(graphs.isAsynchronous()).thenReturn(true);
        when(parser.parse(anyString(), anyString(), any(byte[].class))).thenReturn(
                new ParsedKnowledgeDocument("Synthetic", "fixture.pdf", "pdf", List.of(
                        new KnowledgeChunkDraft("pdf-text", "page-1", "page-1", "Synthetic evidence"))));
        when(embedding.embedDocuments(anyList())).thenReturn(List.of(new float[properties.getEmbeddingDimension()]));
        List<StoredVectorPoint> stored = new ArrayList<>();
        doAnswer(invocation -> {
            List<VectorPoint> points = invocation.getArgument(0);
            points.forEach(point -> stored.add(new StoredVectorPoint(point.id(),
                    new LinkedHashMap<>(point.payload()), properties.getEmbeddingDimension())));
            return null;
        }).when(vectors).upsert(anyList());
        when(vectors.scroll(anyMap(), anyInt(), anyBoolean())).thenAnswer(invocation -> {
            Map<String, Object> filter = invocation.getArgument(0);
            int limit = invocation.getArgument(1);
            return stored.stream().filter(point -> matches(point, filter)).limit(limit).toList();
        });
        doAnswer(invocation -> {
            Map<String, Object> filter = invocation.getArgument(0);
            Map<String, Object> update = invocation.getArgument(1);
            stored.stream().filter(point -> matches(point, filter)).forEach(point -> point.payload().putAll(update));
            return null;
        }).when(vectors).setPayload(anyMap(), anyMap());
        when(graphs.syncDocument(anyString())).thenThrow(ExternalServiceException.httpFailure("RAG engine", 429));

        var result = documentService.ingest(new MockMultipartFile("file", "fixture.pdf", "application/pdf",
                "synthetic graph rejection".getBytes(StandardCharsets.UTF_8)), "routing-test", "graph-doc", "Synthetic");

        assertThat(result.document().status()).isEqualTo("ACTIVE");
        assertThat(result.graphStatus()).isEqualTo("FAILED");
        assertThat(result.validation().valid()).isTrue();
        assertThat(stored).allSatisfy(point -> assertThat(point.payload()).containsEntry("document_status", "ACTIVE"));
        verify(graphs, never()).deactivateDocument(anyString());
    }

    private RagRetrievalRpcRequest request() {
        RagRetrievalRpcRequest request = new RagRetrievalRpcRequest();
        request.setQuery("synthetic query");
        return request;
    }

    private VectorSearchHit hit(String content, String sourceName, String locator) {
        return new VectorSearchHit(UUID.randomUUID().toString(), .9D,
                Map.of("document_id", UUID.randomUUID().toString(), "chunk_id", UUID.randomUUID().toString(),
                        "document_title", "Synthetic", "source_name", sourceName, "source_locator", locator,
                        "content", content, "content_sha256", UUID.randomUUID().toString()));
    }
}
