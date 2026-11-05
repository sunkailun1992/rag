package com.kellen.rag.client;

import com.kellen.rag.config.RagEngineProperties;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.exception.ExternalServiceException;
import com.kellen.rag.model.ParsedKnowledgeDocument;
import com.kellen.rag.service.impl.DefaultRagRetrievalService;
import com.kellen.rpc.rag.RagRetrievalRpcDTO;
import com.kellen.rpc.rag.RagRetrievalRpcRequest;
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
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.verifyNoInteractions;

/**
 * 独立契约验证：真实 Spring Bean 经本机 HTTP 检查服务认证、可信租户、响应校验与总时限。
 * 不读取开发配置、不连接 Nacos/Qdrant，不使用真实业务租户或模型凭据。
 */
@SpringBootTest(classes = RagEngineContractIntegrationTest.TestConfig.class,
        webEnvironment = SpringBootTest.WebEnvironment.NONE, properties = {
        "spring.config.name=rag-engine-contract-isolated",
        "spring.cloud.nacos.config.enabled=false",
        "spring.cloud.nacos.config.import-check.enabled=false",
        "spring.cloud.nacos.discovery.enabled=false"
})
class RagEngineContractIntegrationTest {
    @Autowired private RagEngineClient client;
    @Autowired private RagEngineProperties engine;
    @Autowired private RagVectorProperties vector;
    @Autowired private ObjectMapper mapper;
    @Autowired private DefaultRagRetrievalService retrievalService;
    @MockitoBean private EmbeddingClient embedding;
    @MockitoBean private VectorStoreClient vectors;
    @MockitoBean private RerankClient reranker;
    private HttpServer server;
    private ExecutorService executor;
    private String tenant;
    private String key;
    private volatile String response;
    private volatile int status;
    private volatile long delayMillis;
    private volatile Map<String, List<String>> requestHeaders;
    private volatile String requestBody;
    private final AtomicInteger requests = new AtomicInteger();

    @Configuration(proxyBeanMethods = false)
    @Import({RagEngineClient.class, RagEngineProperties.class, RagVectorProperties.class,
            DefaultRagRetrievalService.class})
    static class TestConfig {
        @Bean ObjectMapper objectMapper() { return new ObjectMapper(); }
    }

    @BeforeEach
    void startUpstream() throws Exception {
        tenant = UUID.randomUUID().toString();
        key = UUID.randomUUID().toString() + UUID.randomUUID();
        TenantContextHolder.setTenantId(tenant);
        requests.set(0);
        status = 200;
        delayMillis = 0;
        response = mapper.writeValueAsString(retrievalResponse());
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        executor = Executors.newCachedThreadPool();
        server.setExecutor(executor);
        server.createContext("/", exchange -> {
            requests.incrementAndGet();
            requestHeaders = new LinkedHashMap<>(exchange.getRequestHeaders());
            requestBody = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            try {
                if (delayMillis > 0) Thread.sleep(delayMillis);
                byte[] bytes = response.getBytes(StandardCharsets.UTF_8);
                if (status == 302) exchange.getResponseHeaders().add("Location", "/redirect-target");
                exchange.getResponseHeaders().add("Content-Type", "application/json");
                exchange.sendResponseHeaders(status, bytes.length);
                exchange.getResponseBody().write(bytes);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            } finally {
                exchange.close();
            }
        });
        server.start();
        engine.setBaseUrl("http://127.0.0.1:" + server.getAddress().getPort());
        engine.setApiKey(key);
        engine.setParseEnabled(true);
        engine.setRetrievalEnabled(true);
        engine.setRetrievalTimeoutMillis(3500);
        engine.setParseTimeoutMillis(1000);
        engine.setMaxResponseBytes(1024 * 1024);
        engine.setMaxChunks(5000);
        vector.setMaxChunkCharacters(6000);
        vector.setEnabled(true);
        vector.setDefaultKnowledgeBase("contract-test");
    }

    @AfterEach
    void shutdownUpstream() {
        TenantContextHolder.clear();
        server.stop(0);
        executor.shutdownNow();
    }

    @Test
    void shouldSendConfiguredServiceKeyAndOnlyAuthenticatedTenant() {
        assertThat(retrieve()).hasSize(1);
        assertThat(header("Authorization")).isEqualTo("Bearer " + key);
        assertThat(header("X-Tenant-Id")).isEqualTo(tenant);
        assertThat(requestBody).contains("\"knowledgeBase\":\"contract-test\"");
        assertThat(requestBody).doesNotContain(key).doesNotContain("tenantId");
    }

    @Test
    void shouldReturnUnavailableInsteadOfContextWhenEngineHitsAreDegraded() throws Exception {
        for (String mode : List.of("dense", "hybrid")) {
            response = mapper.writeValueAsString(Map.of("mode", mode, "degraded", true,
                    "hits", List.of(hit(validPayload()))));

            RagRetrievalRpcDTO result = retrieveThroughService();

            assertThat(result.isEnabled()).isTrue();
            assertThat(result.isMatched()).isFalse();
            assertThat(result.getMessage()).isEqualTo("RAG retrieval is temporarily unavailable");
            assertThat(result.getContext()).isEmpty();
            assertThat(result.getChunks()).isEmpty();
            assertThat(result.getSourceCount()).isZero();
        }
        assertThat(requests.get()).isEqualTo(2);
        verifyNoInteractions(embedding, vectors, reranker);
    }

    @Test
    void shouldKeepNonDegradedDenseAndHybridHitsCompatibleWithRpc() throws Exception {
        for (String mode : List.of("dense", "hybrid")) {
            response = mapper.writeValueAsString(Map.of("mode", mode, "degraded", false,
                    "hits", List.of(hit(validPayload()))));

            RagRetrievalRpcDTO result = retrieveThroughService();

            assertThat(result.isMatched()).isTrue();
            assertThat(result.getMessage()).isEqualTo("ok");
            assertThat(result.getContext()).contains("Synthetic fixture paragraph");
            assertThat(result.getChunks()).hasSize(1);
        }
        verifyNoInteractions(embedding, vectors, reranker);
    }

    @Test
    void shouldUseContractDefaultWhenDegradedFlagIsAbsent() throws Exception {
        response = mapper.writeValueAsString(Map.of("mode", "dense", "hits", List.of(hit(validPayload()))));

        RagRetrievalRpcDTO result = retrieveThroughService();

        assertThat(result.isMatched()).isTrue();
        assertThat(result.getMessage()).isEqualTo("ok");
        assertThat(result.getContext()).contains("Synthetic fixture paragraph");
        verifyNoInteractions(embedding, vectors, reranker);
    }

    @Test
    void shouldRejectMalformedDegradedFlagInsteadOfCoercingIt() throws Exception {
        for (Object flag : new Object[]{null, "false", "true", 0, Map.of()}) {
            Map<String, Object> body = new LinkedHashMap<>(retrievalResponse());
            body.put("degraded", flag);
            response = mapper.writeValueAsString(body);

            assertThatThrownBy(this::retrieve).isInstanceOf(ExternalServiceException.class)
                    .hasMessageNotContaining("Synthetic fixture paragraph").hasMessageNotContaining(key);
        }
    }

    @Test
    void shouldRejectMissingTenantBeforeAnyNetworkRequest() {
        TenantContextHolder.clear();
        assertThatThrownBy(this::retrieve).isInstanceOf(IllegalArgumentException.class);
        assertThat(requests.get()).isZero();
    }

    @Test
    void shouldRejectMissingServiceKeyBeforeAnyNetworkRequest() {
        engine.setApiKey("");
        assertThatThrownBy(this::retrieve).isInstanceOf(ExternalServiceException.class);
        assertThat(requests.get()).isZero();
    }

    @Test
    void shouldRejectAnyOutOfScopeOrInactiveHitAsWholeResponse() throws Exception {
        for (Map.Entry<String, Object> mutation : Map.<String, Object>of(
                "tenant_id", UUID.randomUUID().toString(),
                "knowledge_base", "different-knowledge",
                "document_status", "SUPERSEDED").entrySet()) {
            Map<String, Object> payload = validPayload();
            payload.put(mutation.getKey(), mutation.getValue());
            response = mapper.writeValueAsString(Map.of("mode", "dense", "degraded", false,
                    "hits", List.of(hit(validPayload()), hit(payload))));
            assertThatThrownBy(this::retrieve).isInstanceOf(ExternalServiceException.class);
        }
    }

    @Test
    void shouldRejectDifferentEmbeddingModelOrDimension() throws Exception {
        for (Map.Entry<String, Object> mutation : Map.<String, Object>of(
                "embedding_model", "different-model", "embedding_dimension", 7).entrySet()) {
            Map<String, Object> payload = validPayload();
            payload.put(mutation.getKey(), mutation.getValue());
            response = mapper.writeValueAsString(Map.of("mode", "dense", "degraded", false,
                    "hits", List.of(hit(payload))));
            assertThatThrownBy(this::retrieve).isInstanceOf(ExternalServiceException.class);
        }
    }

    @Test
    void shouldRejectStringEmbeddingDimensionInsteadOfCoercingIt() throws Exception {
        Map<String, Object> payload = validPayload();
        payload.put("embedding_dimension", String.valueOf(vector.getEmbeddingDimension()));
        response = mapper.writeValueAsString(Map.of("mode", "dense", "hits", List.of(hit(payload))));
        assertThatThrownBy(this::retrieve).isInstanceOf(ExternalServiceException.class);
    }

    @Test
    void shouldRejectOverflowedPageNumberInsteadOfWrappingIt() throws Exception {
        response = mapper.writeValueAsString(parseResponse(null)).replace("\"page\":1", "\"page\":4294967297");
        assertThatThrownBy(() -> client.parse("fixture.pdf", "Synthetic", "pdf", new byte[]{1}))
                .isInstanceOf(ExternalServiceException.class);
    }

    @Test
    void shouldRejectDuplicateHitIdsAndUnsupportedMode() throws Exception {
        Map<String, Object> hit = hit(validPayload());
        response = mapper.writeValueAsString(Map.of("mode", "dense", "hits", List.of(hit, hit)));
        assertThatThrownBy(this::retrieve).isInstanceOf(ExternalServiceException.class);
        response = mapper.writeValueAsString(Map.of("mode", "unknown", "hits", List.of()));
        assertThatThrownBy(this::retrieve).isInstanceOf(ExternalServiceException.class);
    }

    @Test
    void shouldRejectNonNumericScore() throws Exception {
        Map<String, Object> hit = hit(validPayload());
        hit.put("score", "NaN");
        response = mapper.writeValueAsString(Map.of("mode", "dense", "hits", List.of(hit)));
        assertThatThrownBy(this::retrieve).isInstanceOf(ExternalServiceException.class);
    }

    @Test
    void shouldCancelOversizedResponse() {
        engine.setMaxResponseBytes(1024);
        response = " ".repeat(2048) + "{}";
        assertThatThrownBy(this::retrieve).isInstanceOf(ExternalServiceException.class);
    }

    @Test
    void shouldEnforceTotalRequestTimeout() {
        engine.setRetrievalTimeoutMillis(100);
        delayMillis = 3000;
        long started = System.nanoTime();
        assertThatThrownBy(this::retrieve).isInstanceOf(ExternalServiceException.class);
        assertThat((System.nanoTime() - started) / 1_000_000).isLessThan(2000);
    }

    @Test
    void shouldNotFollowRedirectOrExposeUpstreamBody() {
        status = 302;
        response = "sensitive upstream error body";
        assertThatThrownBy(this::retrieve).isInstanceOf(ExternalServiceException.class)
                .hasMessageNotContaining("sensitive").hasMessageNotContaining(key);
        assertThat(requests.get()).isEqualTo(1);
    }

    @Test
    void shouldAcceptTextPdfWithoutBoundingBoxAndKeepSourceMetadata() throws Exception {
        response = mapper.writeValueAsString(parseResponse(null));
        ParsedKnowledgeDocument parsed = client.parse("fixture.pdf", "Synthetic", "pdf", new byte[]{1});
        assertThat(parsed.chunks()).hasSize(1);
        assertThat(parsed.chunks().get(0).metadata()).containsEntry("page", 1)
                .containsEntry("source_name", "fixture.pdf");
    }

    @Test
    void shouldCountUnicodeCodePointsAtPythonCharacterBoundary() throws Exception {
        String supplementaryText = "😀".repeat(6000);
        response = mapper.writeValueAsString(parseResponse(null))
                .replace("Synthetic fixture paragraph", supplementaryText);
        ParsedKnowledgeDocument parsed = client.parse("fixture.pdf", "Synthetic", "pdf", new byte[]{1});
        assertThat(parsed.chunks().get(0).content()).isEqualTo(supplementaryText);
        response = mapper.writeValueAsString(parseResponse(null))
                .replace("Synthetic fixture paragraph", supplementaryText + "😀");
        assertThatThrownBy(() -> client.parse("fixture.pdf", "Synthetic", "pdf", new byte[]{1}))
                .isInstanceOf(ExternalServiceException.class);
    }

    @Test
    void shouldRejectInvalidBoundingBoxOrOversizedChunk() throws Exception {
        response = mapper.writeValueAsString(parseResponse(List.of(10, 0, 1, 20)));
        assertThatThrownBy(() -> client.parse("fixture.pdf", "Synthetic", "pdf", new byte[]{1}))
                .isInstanceOf(ExternalServiceException.class);
        vector.setMaxChunkCharacters(5);
        response = mapper.writeValueAsString(parseResponse(null));
        assertThatThrownBy(() -> client.parse("fixture.pdf", "Synthetic", "pdf", new byte[]{1}))
                .isInstanceOf(ExternalServiceException.class);
    }

    @Test
    void shouldRejectMalformedJsonOrMissingPayloadFields() throws Exception {
        response = "not json";
        assertThatThrownBy(this::retrieve).isInstanceOf(RuntimeException.class);
        Map<String, Object> payload = validPayload();
        payload.remove("source_name");
        response = mapper.writeValueAsString(Map.of("mode", "dense", "hits", List.of(hit(payload))));
        assertThatThrownBy(this::retrieve).isInstanceOf(ExternalServiceException.class);
    }

    private List<?> retrieve() { return client.retrieve("synthetic query", "contract-test", 6, 30, .2, 12000); }

    private RagRetrievalRpcDTO retrieveThroughService() {
        RagRetrievalRpcRequest request = new RagRetrievalRpcRequest();
        request.setQuery("synthetic query");
        return retrievalService.retrieve(request);
    }

    private String header(String name) {
        return requestHeaders.entrySet().stream().filter(entry -> entry.getKey().equalsIgnoreCase(name))
                .findFirst().orElseThrow().getValue().get(0);
    }

    private Map<String, Object> retrievalResponse() {
        return Map.of("mode", "dense", "degraded", false, "hits", List.of(hit(validPayload())));
    }

    private Map<String, Object> validPayload() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("tenant_id", tenant);
        payload.put("knowledge_base", "contract-test");
        payload.put("document_status", "ACTIVE");
        payload.put("embedding_model", vector.getEmbeddingModel());
        payload.put("embedding_dimension", vector.getEmbeddingDimension());
        payload.put("document_id", UUID.randomUUID().toString());
        payload.put("chunk_id", UUID.randomUUID().toString());
        payload.put("document_title", "Synthetic");
        payload.put("source_name", "fixture.docx");
        payload.put("content_sha256", "synthetic-content-fingerprint");
        payload.put("content", "Synthetic fixture paragraph");
        return payload;
    }

    private Map<String, Object> hit(Map<String, Object> payload) {
        return new LinkedHashMap<>(Map.of("id", UUID.randomUUID().toString(), "score", .9, "payload", payload));
    }

    private Map<String, Object> parseResponse(List<Integer> boundingBox) {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("page", 1);
        metadata.put("block_type", "pdf-text");
        metadata.put("bbox", boundingBox);
        metadata.put("source_name", "fixture.pdf");
        return Map.of("title", "Synthetic", "sourceName", "fixture.pdf", "sourceType", "pdf", "chunks", List.of(
                Map.of("chunkType", "pdf-text", "sectionPath", "第1页", "locator", "第1页 / 区块1",
                        "content", "Synthetic fixture paragraph", "metadata", metadata)));
    }
}
