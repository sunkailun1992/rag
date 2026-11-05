package com.kellen.rag.client;

import com.kellen.rag.config.RagEngineProperties;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.exception.ExternalServiceException;
import com.kellen.rag.graph.KnowledgeGraphProjector;
import com.kellen.rag.model.ActiveGraphSource;
import com.kellen.rag.model.KnowledgeGraphViewMode;
import com.kellen.rag.model.StoredVectorPoint;
import com.kellen.rag.service.impl.DefaultRagKnowledgeGraphService;
import com.kellen.rag.store.KnowledgeGraphStore;
import com.kellen.utils.context.TenantContextHolder;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import tools.jackson.databind.ObjectMapper;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.anySet;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/** 真实 Spring 服务与内部 HTTP 协议的隔离反例，不读取运行配置或真实知识库。 */
@SpringBootTest(classes = GraphEngineContractIntegrationTest.TestConfig.class,
        webEnvironment = SpringBootTest.WebEnvironment.NONE, properties = {
        "spring.config.name=graph-engine-isolated",
        "spring.cloud.nacos.config.enabled=false",
        "spring.cloud.nacos.config.import-check.enabled=false",
        "spring.cloud.nacos.discovery.enabled=false"
})
class GraphEngineContractIntegrationTest {
    @Configuration(proxyBeanMethods = false)
    @Import({RagEngineClient.class, RagEngineProperties.class, RagVectorProperties.class,
            DefaultRagKnowledgeGraphService.class, KnowledgeGraphProjector.class})
    static class TestConfig {
        @Bean ObjectMapper objectMapper() { return new ObjectMapper(); }
    }

    @Autowired private RagEngineClient client;
    @Autowired private RagEngineProperties properties;
    @Autowired private ObjectMapper mapper;
    @Autowired private DefaultRagKnowledgeGraphService service;
    @MockitoBean private VectorStoreClient vectors;
    @MockitoBean private KnowledgeGraphStore legacy;
    private HttpServer server;
    private ExecutorService executor;
    private String tenant;
    private String key;
    private volatile String response;
    private volatile int status;
    private volatile long delayMillis;
    private volatile String requestBody;
    private volatile String requestMethod;
    private volatile String requestUri;
    private volatile String authorization;
    private volatile String tenantHeader;
    private final AtomicInteger requests = new AtomicInteger();
    private final ActiveGraphSource source = new ActiveGraphSource("point-1", "doc-1", "code-1", 2,
            6_000_000_001L, "chunk-1", "a".repeat(64));

    @BeforeEach
    void startInternalUpstream() throws Exception {
        tenant = UUID.randomUUID().toString();
        key = UUID.randomUUID().toString() + UUID.randomUUID();
        TenantContextHolder.setTenantId(tenant);
        status = 200;
        delayMillis = 0;
        requests.set(0);
        response = mapper.writeValueAsString(graph());
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        executor = Executors.newCachedThreadPool();
        server.setExecutor(executor);
        server.createContext("/", exchange -> {
            requests.incrementAndGet();
            requestMethod = exchange.getRequestMethod();
            requestUri = exchange.getRequestURI().toString();
            authorization = exchange.getRequestHeaders().getFirst("Authorization");
            tenantHeader = exchange.getRequestHeaders().getFirst("X-Tenant-Id");
            requestBody = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            try {
                if (delayMillis > 0) Thread.sleep(delayMillis);
                byte[] bytes = response.getBytes(StandardCharsets.UTF_8);
                if (status == 302) exchange.getResponseHeaders().add("Location", "/redirect");
                exchange.sendResponseHeaders(status, bytes.length);
                exchange.getResponseBody().write(bytes);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            } finally {
                exchange.close();
            }
        });
        server.start();
        properties.setBaseUrl("http://127.0.0.1:" + server.getAddress().getPort());
        properties.setApiKey(key);
        properties.setGraphEnabled(true);
        properties.setGraphTimeoutMillis(2000);
        properties.setGraphStatusTimeoutMillis(1000);
        properties.setMaxResponseBytes(1024 * 1024);
        when(vectors.collectionExists()).thenReturn(true);
        when(vectors.count(anyMap())).thenReturn(1L);
        when(vectors.scroll(anyMap(), anyInt(), anyBoolean())).thenReturn(List.of(point()));
        when(vectors.scrollPayload(anyMap(), anyInt(), anySet())).thenReturn(List.of(point()));
    }

    @AfterEach
    void stopUpstream() {
        TenantContextHolder.clear();
        server.stop(0);
        executor.shutdownNow();
    }

    @Test
    void shouldForwardOnlyTrustedTenantAndKeepMultiSourceEnvelope() throws Exception {
        var result = service.query("kb-1", null, null, KnowledgeGraphViewMode.OVERVIEW, 20);
        assertThat(result.backend()).isEqualTo("neo4j");
        assertThat(result.nodes()).hasSize(2);
        assertThat(result.edges()).hasSize(1);
        assertThat(authorization).isEqualTo("Bearer " + key);
        assertThat(tenantHeader).isEqualTo(tenant);
        assertThat(requestUri).isEqualTo("/v1/graphs/queries");
        var request = mapper.readTree(requestBody);
        assertThat(request.has("tenantId")).isFalse();
        assertThat(request.path("activeSources").get(0).path("documentRevision").asLong()).isEqualTo(source.documentRevision());
        verifyNoInteractions(legacy);
    }

    @Test
    void shouldRejectEachForgedSourceIdentityAndNumericCoercion() throws Exception {
        Map<String, Object> mutations = Map.of("pointId", "other-point", "documentId", "other-document",
                "documentCode", "other-code", "documentVersion", 3, "documentRevision", 6_000_000_002L,
                "chunkId", "other-chunk", "contentSha256", "b".repeat(64));
        for (Map.Entry<String, Object> mutation : mutations.entrySet()) {
            Map<String, Object> forged = sourceMap();
            forged.put(mutation.getKey(), mutation.getValue());
            response = mapper.writeValueAsString(graphWithSources(List.of(forged), 1, false));
            assertThatThrownBy(this::queryClient).as(mutation.getKey()).isInstanceOf(ExternalServiceException.class);
        }
        for (String field : List.of("documentVersion", "documentRevision")) {
            Map<String, Object> forged = sourceMap();
            forged.put(field, field.equals("documentVersion") ? "2" : "6000000001");
            response = mapper.writeValueAsString(graphWithSources(List.of(forged), 1, false));
            assertThatThrownBy(this::queryClient).isInstanceOf(ExternalServiceException.class);
        }
    }

    @Test
    void shouldRejectCrossScopeInactiveAndMissingEvidence() throws Exception {
        for (String field : List.of("tenantId", "knowledgeBase")) {
            Map<String, Object> forged = sourceMap();
            forged.put(field, "outside-scope");
            response = mapper.writeValueAsString(graphWithSources(List.of(forged), 1, false));
            assertThatThrownBy(this::queryClient).isInstanceOf(ExternalServiceException.class);
        }
        assertThatThrownBy(() -> client.queryGraph("kb-1", List.of(), null, null, KnowledgeGraphViewMode.OVERVIEW, 20))
                .isInstanceOf(ExternalServiceException.class);
        response = mapper.writeValueAsString(graphWithSources(List.of(), 0, false));
        assertThatThrownBy(this::queryClient).isInstanceOf(ExternalServiceException.class);
    }

    @Test
    void shouldRejectDanglingEdgesDuplicateNodesAndWrongCounts() throws Exception {
        Map<String, Object> graph = graph();
        graph.put("edges", List.of(Map.of("id", "edge-1", "source", "absent", "target", "entity-1",
                "type", "RELATED_TO", "label", "关系", "properties", evidence())));
        response = mapper.writeValueAsString(graph);
        assertThatThrownBy(this::queryClient).isInstanceOf(ExternalServiceException.class);
        graph = graph();
        graph.put("nodes", List.of(node("same"), node("same")));
        response = mapper.writeValueAsString(graph);
        assertThatThrownBy(this::queryClient).isInstanceOf(ExternalServiceException.class);
        graph = graph();
        graph.put("nodeCount", 3);
        response = mapper.writeValueAsString(graph);
        assertThatThrownBy(this::queryClient).isInstanceOf(ExternalServiceException.class);
        graph = graph();
        graph.put("viewMode", "DETAIL");
        response = mapper.writeValueAsString(graph);
        assertThatThrownBy(this::queryClient).isInstanceOf(ExternalServiceException.class);
    }

    @Test
    void shouldBoundSourcesAndStripUnapprovedProperties() throws Exception {
        response = mapper.writeValueAsString(graphWithSources(java.util.Collections.nCopies(9, sourceMap()), 9, false));
        assertThatThrownBy(this::queryClient).isInstanceOf(ExternalServiceException.class);
        response = mapper.writeValueAsString(graphWithSources(List.of(sourceMap()), 2, false));
        assertThatThrownBy(this::queryClient).isInstanceOf(ExternalServiceException.class);
        var source = sourceMap();
        source.put("secret", "must-not-leave-service");
        source.put("sourceMetadata", Map.of("page", 1, "secret", "must-not-leave-service"));
        response = mapper.writeValueAsString(graphWithSources(List.of(source), 1, false));
        assertThat(mapper.writeValueAsString(queryClient())).doesNotContain("must-not-leave-service");
    }

    @Test
    void shouldSubmitAsyncBuildAndReadScopedStatusWithGet() throws Exception {
        response = mapper.writeValueAsString(build());
        var accepted = service.sync("kb-1");
        assertThat(accepted.status()).isEqualTo("QUEUED");
        assertThat(accepted.buildId()).isEqualTo("build-1");
        assertThat(mapper.readTree(requestBody).path("reconcile").asBoolean()).isTrue();
        var read = service.build("build-1", "kb-1");
        assertThat(read.processedDocumentCount()).isZero();
        assertThat(requestMethod).isEqualTo("GET");
        assertThat(requestUri).isEqualTo("/v1/graphs/builds/build-1?knowledgeBase=kb-1");
        assertThat(requestBody).isEmpty();
        verifyNoInteractions(legacy);
        var other = build();
        other.put("knowledgeBase", "other");
        response = mapper.writeValueAsString(other);
        assertThatThrownBy(() -> service.build("build-1", "kb-1")).isInstanceOf(ExternalServiceException.class);
    }

    @Test
    void shouldRejectIncompleteOversizedAndCrossTenantSnapshotsBeforeGraphRequest() {
        when(vectors.count(anyMap())).thenReturn(10001L);
        assertThatThrownBy(() -> service.sync("kb-1")).isInstanceOf(IllegalStateException.class);
        when(vectors.count(anyMap())).thenReturn(2L);
        assertThatThrownBy(() -> service.sync("kb-1")).isInstanceOf(IllegalStateException.class);
        when(vectors.count(anyMap())).thenReturn(1L);
        var bad = point();
        bad.payload().put("tenant_id", "outside");
        when(vectors.scroll(anyMap(), anyInt(), anyBoolean())).thenReturn(List.of(bad));
        when(vectors.scrollPayload(anyMap(), anyInt(), anySet())).thenReturn(List.of(bad));
        assertThatThrownBy(() -> service.sync("kb-1")).isInstanceOf(IllegalStateException.class);
        assertThat(requests).hasValue(0);
    }

    @Test
    void shouldRejectStaleGraphWhenSourcesChangeDuringQuery() {
        when(vectors.count(anyMap())).thenReturn(1L, 1L, 0L, 0L);
        when(vectors.scroll(anyMap(), anyInt(), anyBoolean())).thenReturn(List.of(point()), List.of());
        when(vectors.scrollPayload(anyMap(), anyInt(), anySet())).thenReturn(List.of(point()), List.of());
        assertThatThrownBy(() -> service.query("kb-1", null, null, KnowledgeGraphViewMode.OVERVIEW, 20))
                .isInstanceOf(ExternalServiceException.class);
    }

    @Test
    void shouldRejectDuplicateProjectionPointsAndIncompleteIdentityBeforeNetwork() {
        when(vectors.count(anyMap())).thenReturn(2L);
        when(vectors.scrollPayload(anyMap(), anyInt(), anySet())).thenReturn(List.of(point(), point()));
        assertThatThrownBy(() -> service.sync("kb-1")).isInstanceOf(IllegalStateException.class);
        when(vectors.count(anyMap())).thenReturn(1L);
        var incomplete = point();
        incomplete.payload().remove("content_sha256");
        when(vectors.scrollPayload(anyMap(), anyInt(), anySet())).thenReturn(List.of(incomplete));
        assertThatThrownBy(() -> service.sync("kb-1")).isInstanceOf(IllegalStateException.class);
        incomplete.payload().put("content_sha256", "a".repeat(64));
        incomplete.payload().put("document_version", "2");
        assertThatThrownBy(() -> service.sync("kb-1")).isInstanceOf(IllegalStateException.class);
        assertThat(requests).hasValue(0);
    }

    @Test
    void shouldRejectProjectedHashAndRevisionChangesAfterGraphQuery() {
        for (Map.Entry<String, Object> change : Map.<String, Object>of(
                "content_sha256", "b".repeat(64), "document_revision", 6_000_000_002L).entrySet()) {
            var after = point();
            after.payload().put(change.getKey(), change.getValue());
            when(vectors.scrollPayload(anyMap(), anyInt(), anySet())).thenReturn(List.of(point()), List.of(after));
            assertThatThrownBy(() -> service.query("kb-1", null, null, KnowledgeGraphViewMode.OVERVIEW, 20))
                    .isInstanceOf(ExternalServiceException.class);
        }
    }

    @Test
    void shouldEstablishBarrierForDocumentWithNoActiveSources() throws Exception {
        when(vectors.count(anyMap())).thenReturn(0L);
        when(vectors.scroll(anyMap(), anyInt(), anyBoolean())).thenReturn(List.of());
        when(vectors.scrollPayload(anyMap(), anyInt(), anySet())).thenReturn(List.of());
        response = "{\"deactivated\":true,\"barrierEpoch\":1,\"documentRevision\":0}";
        assertThat(service.syncDocument("doc-1").documentCount()).isZero();
        assertThat(requestUri).isEqualTo("/v1/graphs/deactivations");
        assertThat(mapper.readTree(requestBody).path("documentId").asText()).isEqualTo("doc-1");
        response = "{\"deactivated\":false}";
        assertThatThrownBy(() -> service.deactivateDocument("doc-1")).isInstanceOf(ExternalServiceException.class);
    }

    @Test
    void shouldRequireRevisionFenceBeforeAcknowledgingDeletion() {
        response = "{\"deactivated\":true}";
        assertThatThrownBy(() -> service.deactivateDocument("doc-1"))
                .isInstanceOf(ExternalServiceException.class);
    }

    @Test
    void shouldForwardRemovalFenceOnlyForFailedVectorDeletionRestore() throws Exception {
        response = "{\"deactivated\":true,\"barrierEpoch\":12,\"documentRevision\":6000000001}";
        var fence = service.deactivateDocumentWithFence("doc-1");
        assertThat(fence.barrierEpoch()).isEqualTo(12);
        assertThat(mapper.readTree(requestBody).path("documentRevision").asLong()).isEqualTo(source.documentRevision());
        response = mapper.writeValueAsString(build());
        service.restoreDocument("doc-1", fence);
        var restore = mapper.readTree(requestBody).path("restoreFence");
        assertThat(restore.path("barrierEpoch").asLong()).isEqualTo(12);
        assertThat(restore.path("documentRevision").asLong()).isEqualTo(source.documentRevision());
        service.syncDocument("doc-1");
        assertThat(mapper.readTree(requestBody).has("restoreFence")).isFalse();
        var stale = new com.kellen.rag.model.GraphRemovalFence("doc-1", 11, 6000000000L);
        int previousRequests = requests.get();
        assertThatThrownBy(() -> service.restoreDocument("doc-1", stale)).isInstanceOf(IllegalStateException.class);
        assertThat(requests).hasValue(previousRequests);
        assertThatThrownBy(() -> service.restoreDocument("doc-1", null)).isInstanceOf(IllegalStateException.class);
        response = "{\"deactivated\":true,\"barrierEpoch\":13,\"documentRevision\":6000000001}";
        service.deactivateDocumentWithFence("doc-1");
        status = 409;
        response = "{\"error\":{\"code\":\"GRAPH_RESTORE_SUPERSEDED\"}}";
        previousRequests = requests.get();
        assertThatThrownBy(() -> service.restoreDocument("doc-1", fence)).isInstanceOf(ExternalServiceException.class);
        assertThat(requests).hasValue(previousRequests + 1);
        assertThat(mapper.readTree(requestBody).path("restoreFence").path("barrierEpoch").asLong()).isEqualTo(12);
    }

    @Test
    void shouldKeepBooleanEvidenceFlagsAndRejectStringFlags() throws Exception {
        var provenance = sourceMap();
        provenance.put("evidenceTruncated", true);
        provenance.put("evidenceNormalized", false);
        response = mapper.writeValueAsString(graphWithSources(List.of(provenance), 1, false));
        assertThat(mapper.writeValueAsString(queryClient())).contains("\"evidenceTruncated\":true")
                .contains("\"evidenceNormalized\":false");
        provenance.put("evidenceTruncated", "true");
        response = mapper.writeValueAsString(graphWithSources(List.of(provenance), 1, false));
        assertThatThrownBy(this::queryClient).isInstanceOf(ExternalServiceException.class);
    }

    @Test
    void shouldFailClosedOnInternalErrorsRedirectOversizeAndTimeout() {
        status = 429;
        assertThatThrownBy(this::queryClient).isInstanceOf(ExternalServiceException.class).hasMessageNotContaining(key);
        status = 302;
        assertThatThrownBy(this::queryClient).isInstanceOf(ExternalServiceException.class);
        assertThat(requests).hasValue(2);
        status = 200;
        properties.setMaxResponseBytes(1024);
        response = "x".repeat(2048);
        assertThatThrownBy(this::queryClient).isInstanceOf(ExternalServiceException.class);
        properties.setMaxResponseBytes(1024 * 1024);
        properties.setGraphTimeoutMillis(100);
        delayMillis = 300;
        assertThatThrownBy(this::queryClient).isInstanceOf(ExternalServiceException.class);
    }

    @Test
    void shouldRejectOversizedRequestAndUnsafeBuildPathBeforeNetwork() {
        List<ActiveGraphSource> oversized = java.util.Collections.nCopies(40000, source);
        assertThatThrownBy(() -> client.buildGraph("kb-1", oversized, true)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> client.graphBuild("../escape", "kb-1")).isInstanceOf(IllegalArgumentException.class);
        assertThat(requests).hasValue(0);
    }

    private com.kellen.rag.entity.vo.RagKnowledgeGraphVO queryClient() {
        return client.queryGraph("kb-1", List.of(source), null, null, KnowledgeGraphViewMode.OVERVIEW, 20);
    }

    private StoredVectorPoint point() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("tenant_id", tenant);
        payload.put("knowledge_base", "kb-1");
        payload.put("document_status", "ACTIVE");
        payload.put("document_id", source.documentId());
        payload.put("document_code", source.documentCode());
        payload.put("document_version", source.documentVersion());
        payload.put("document_revision", source.documentRevision());
        payload.put("chunk_id", source.chunkId());
        payload.put("content_sha256", source.contentSha256());
        return new StoredVectorPoint(source.pointId(), payload, 0);
    }

    private Map<String, Object> sourceMap() {
        return new LinkedHashMap<>(Map.of("pointId", source.pointId(), "documentId", source.documentId(),
                "documentCode", source.documentCode(), "documentVersion", source.documentVersion(),
                "documentRevision", source.documentRevision(), "chunkId", source.chunkId(), "contentSha256", source.contentSha256()));
    }

    private Map<String, Object> evidence() {
        return Map.of("sources", List.of(sourceMap()), "sourceCount", 1, "sourceTruncated", false);
    }

    private Map<String, Object> node(String id) {
        return new LinkedHashMap<>(Map.of("id", id, "type", "ENTITY", "name", id, "label", "实体", "properties", evidence()));
    }

    private Map<String, Object> graph() {
        Map<String, Object> graph = new LinkedHashMap<>();
        graph.put("knowledgeBase", "kb-1");
        graph.put("viewMode", "OVERVIEW");
        graph.put("backend", "neo4j");
        graph.put("nodeCount", 2);
        graph.put("edgeCount", 1);
        graph.put("truncated", false);
        graph.put("nodeTypeCounts", Map.of("ENTITY", 2));
        graph.put("relationTypeCounts", Map.of("RELATED_TO", 1));
        graph.put("nodes", List.of(node("entity-1"), node("entity-2")));
        graph.put("edges", List.of(Map.of("id", "edge-1", "source", "entity-1", "target", "entity-2",
                "type", "RELATED_TO", "label", "支持", "properties", evidence())));
        return graph;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> graphWithSources(List<Map<String, Object>> sources, int count, boolean truncated) {
        Map<String, Object> graph = graph();
        List<Map<String, Object>> nodes = new ArrayList<>((List<Map<String, Object>>) graph.get("nodes"));
        nodes.get(0).put("properties", Map.of("sources", sources, "sourceCount", count, "sourceTruncated", truncated));
        return graph;
    }

    private Map<String, Object> build() {
        return new LinkedHashMap<>(Map.of("buildId", "build-1", "knowledgeBase", "kb-1", "status", "QUEUED",
                "documentCount", 1, "processedDocumentCount", 0, "nodeCount", 0, "edgeCount", 0));
    }
}
