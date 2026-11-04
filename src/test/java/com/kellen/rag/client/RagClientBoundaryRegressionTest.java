package com.kellen.rag.client;

import com.kellen.rag.config.RagEngineProperties;
import com.kellen.rag.config.RagVectorProperties;
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
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.catchThrowable;

/**
 * 独立风险回归：真实 Spring 客户端、JDK 请求构建与本机 HTTP 替身。
 * 仅现场生成的测试凭据及合成数据；不导入开发配置、不连接 Nacos 或真实向量库。
 */
@SpringBootTest(classes = RagClientBoundaryRegressionTest.TestConfig.class,
        webEnvironment = SpringBootTest.WebEnvironment.NONE, properties = {
        "spring.config.name=rag-client-boundary-isolated",
        "spring.cloud.nacos.config.enabled=false",
        "spring.cloud.nacos.config.import-check.enabled=false",
        "spring.cloud.nacos.discovery.enabled=false"
})
class RagClientBoundaryRegressionTest {
    @Autowired private RagEngineClient engineClient;
    @Autowired private QdrantVectorStoreClient qdrantClient;
    @Autowired private RagEngineProperties engine;
    @Autowired private RagVectorProperties vector;
    @Autowired private ObjectMapper mapper;

    private HttpServer upstream;
    private final AtomicInteger requests = new AtomicInteger();
    private final AtomicInteger mutations = new AtomicInteger();
    private volatile int responseStatus;
    private volatile Map<String, Object> responseBody;
    private volatile JsonNode requestBody;
    private String tenant;

    @Configuration(proxyBeanMethods = false)
    @Import({RagEngineClient.class, QdrantVectorStoreClient.class,
            RagEngineProperties.class, RagVectorProperties.class})
    static class TestConfig {
        @Bean ObjectMapper objectMapper() { return new ObjectMapper(); }
    }

    @BeforeEach
    void startIsolatedUpstream() throws Exception {
        tenant = UUID.randomUUID().toString();
        TenantContextHolder.setTenantId(tenant);
        requests.set(0);
        mutations.set(0);
        responseStatus = 200;
        responseBody = Map.of();
        requestBody = null;
        upstream = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        upstream.createContext("/", exchange -> {
            requests.incrementAndGet();
            if (!"GET".equals(exchange.getRequestMethod())) mutations.incrementAndGet();
            byte[] incoming = exchange.getRequestBody().readAllBytes();
            if (incoming.length > 0) requestBody = mapper.readTree(incoming);
            byte[] response = mapper.writeValueAsBytes(responseBody);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(responseStatus, response.length);
            exchange.getResponseBody().write(response);
            exchange.close();
        });
        upstream.start();
        String root = "http://127.0.0.1:" + upstream.getAddress().getPort();
        engine.setBaseUrl(root);
        engine.setApiKey(UUID.randomUUID().toString() + UUID.randomUUID());
        engine.setParseEnabled(true);
        engine.setRetrievalEnabled(true);
        vector.setQdrantUrl(root);
        vector.setCollectionName("boundary-fixture");
        vector.setHybridWriteEnabled(true);
    }

    @AfterEach
    void stopIsolatedUpstream() {
        TenantContextHolder.clear();
        upstream.stop(0);
    }

    @Test
    void shouldHideMalformedUriCredentialsAndDiscardTheOriginalCause() {
        String sentinel = "synthetic-uri-secret";
        engine.setBaseUrl("http://fixture-user:" + sentinel + "@bad host/");

        assertSanitizedConfigurationFailure(parseFailure(), sentinel, "fixture-user", "bad host");
        assertThat(requests.get()).isZero();
    }

    @Test
    void shouldHideIllegalAuthorizationHeaderContentAndDiscardTheOriginalCause() {
        String sentinel = "synthetic-service-secret-" + UUID.randomUUID();
        engine.setApiKey(sentinel + "\r\nsynthetic-second-line");

        assertSanitizedConfigurationFailure(parseFailure(), sentinel, "Bearer", "synthetic-second-line");
        assertThat(requests.get()).isZero();
    }

    @Test
    void shouldPreserveOnlyTheFixedDocumentRejectionMessage() {
        String sentinel = "synthetic-upstream-document-content";
        responseBody = Map.of("error", sentinel);
        for (int status : List.of(400, 413, 422)) {
            responseStatus = status;
            Throwable failure = parseFailure();
            assertThat(failure).isInstanceOf(IllegalArgumentException.class)
                    .hasMessage("PDF/图片无法解析或超过限制，请检查文件格式、页数及大小")
                    .hasNoCause();
            assertThat(failure.toString()).doesNotContain(sentinel);
        }
        assertThat(requests.get()).isEqualTo(3);
    }

    @Test
    void shouldRejectHybridSchemaWithTheWrongModifierBeforeAnyIndexMutation() {
        responseBody = Map.of("result", Map.of("config", Map.of("params", Map.of(
                "vectors", Map.of("size", 1024, "distance", "Cosine"),
                "sparse_vectors", Map.of(LexicalSparseVector.MODEL, Map.of("modifier", "none"))))));

        assertThatThrownBy(qdrantClient::ensureCollection)
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("混合索引尚未准备");
        assertThat(requests.get()).isEqualTo(2);
        assertThat(mutations.get()).isZero();
    }

    @Test
    void shouldRejectAnOversizedScrollPageInsteadOfReturningMoreThanRequested() {
        responseBody = scrollResponse(List.of(point(), point()), null);

        assertThatThrownBy(() -> qdrantClient.scrollPage(scope(), 1, null))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("超过分页范围");
        assertThat(requests.get()).isEqualTo(1);
    }

    @Test
    void shouldRejectARepeatedScrollCursorInsteadOfLooping() {
        String cursor = UUID.randomUUID().toString();
        responseBody = scrollResponse(List.of(point()), cursor);

        assertThatThrownBy(() -> qdrantClient.scrollPage(scope(), 20, cursor))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("游标未前进");
        assertThat(requests.get()).isEqualTo(1);
    }

    @Test
    void shouldKeepTenantScopeAndOmitVectorsOnAValidAdvancingPage() {
        String cursor = UUID.randomUUID().toString();
        String next = UUID.randomUUID().toString();
        responseBody = scrollResponse(List.of(point()), next);

        var page = qdrantClient.scrollPage(scope(), 20, cursor);

        assertThat(page.points()).hasSize(1);
        assertThat(page.nextOffset()).isEqualTo(next);
        assertThat(requestBody.path("offset").asText()).isEqualTo(cursor);
        assertThat(requestBody.path("with_vector").asBoolean()).isFalse();
        assertThat(requestBody.path("limit").asInt()).isEqualTo(20);
        assertThat(requestBody.path("filter").path("must").toString())
                .contains("tenant_id", tenant, "document_id", "synthetic-document");
    }

    private Throwable parseFailure() {
        return catchThrowable(() -> engineClient.parse("synthetic.pdf", "Synthetic", "pdf",
                "synthetic-only".getBytes(StandardCharsets.UTF_8)));
    }

    private void assertSanitizedConfigurationFailure(Throwable failure, String... hidden) {
        assertThat(failure).isInstanceOf(IllegalArgumentException.class)
                .hasMessage("RAG 引擎配置或请求不合法").hasNoCause();
        assertThat(failure.toString()).doesNotContain(hidden);
    }

    private Map<String, Object> scope() {
        return Map.of("tenant_id", tenant, "document_id", "synthetic-document");
    }

    private Map<String, Object> point() {
        return Map.of("id", UUID.randomUUID().toString(), "payload", scope());
    }

    private Map<String, Object> scrollResponse(List<Map<String, Object>> points, String next) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("points", points);
        result.put("next_page_offset", next);
        return Map.of("result", result);
    }
}
