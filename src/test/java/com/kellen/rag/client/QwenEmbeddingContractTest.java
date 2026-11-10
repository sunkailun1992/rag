package com.kellen.rag.client;

import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.exception.ExternalServiceException;
import com.kellen.rag.model.KnowledgeChunkDraft;
import com.kellen.rag.model.ParsedKnowledgeDocument;
import com.kellen.rag.parser.KnowledgeDocumentParser;
import com.kellen.rag.service.RagKnowledgeGraphService;
import com.kellen.rag.service.impl.DefaultRagDocumentService;
import com.kellen.utils.context.TenantContextHolder;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Function;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/** 实际 Spring 摄取/客户端与回环 HTTP，验证不完整向量整批拒绝且不写入。 */
@SpringBootTest(classes = QwenEmbeddingContractTest.Config.class,
        webEnvironment = SpringBootTest.WebEnvironment.NONE, properties = {
        "spring.config.name=embedding-contract-isolated",
        "spring.cloud.nacos.config.enabled=false",
        "spring.cloud.nacos.config.import-check.enabled=false",
        "spring.cloud.nacos.discovery.enabled=false"})
class QwenEmbeddingContractTest {
    @Autowired private QwenEmbeddingClient embedding;
    @Autowired private DefaultRagDocumentService documents;
    @Autowired private RagVectorProperties properties;
    @Autowired private ObjectMapper mapper;
    @MockitoBean private VectorStoreClient vectors;
    @MockitoBean private KnowledgeDocumentParser parser;
    @MockitoBean private RagKnowledgeGraphService graphs;
    private HttpServer server;
    private final AtomicInteger requests = new AtomicInteger();
    private volatile Function<JsonNode, String> reply;
    private volatile JsonNode lastRequest;
    private volatile int status;

    @Configuration(proxyBeanMethods = false)
    @Import({RagVectorProperties.class, QwenEmbeddingClient.class, DefaultRagDocumentService.class})
    static class Config {
        @Bean ObjectMapper objectMapper() { return new ObjectMapper(); }
    }

    @BeforeEach
    void start() throws Exception {
        requests.set(0);
        status = 200;
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/v1/embeddings", exchange -> {
            requests.incrementAndGet();
            lastRequest = mapper.readTree(exchange.getRequestBody().readAllBytes());
            byte[] body = reply.apply(lastRequest).getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        });
        server.start();
        properties.setQwenBaseUrl("http://127.0.0.1:" + server.getAddress().getPort() + "/v1");
        properties.setQwenApiKey(UUID.randomUUID().toString());
        properties.setEmbeddingDimension(3);
        properties.setEmbeddingBatchSize(10);
        TenantContextHolder.setTenantId(UUID.randomUUID().toString());
        when(vectors.scroll(anyMap(), anyInt(), eq(false))).thenReturn(List.of());
    }

    @AfterEach
    void stop() {
        TenantContextHolder.clear();
        server.stop(0);
    }

    static Stream<String> invalidResponses() {
        return Stream.of(
                "{\"data\":[{\"embedding\":[1,2,3]},{\"index\":1,\"embedding\":[4,5,6]}]}",
                "{\"data\":[{\"index\":0,\"embedding\":[1,2,3]},{\"index\":0,\"embedding\":[4,5,6]}]}",
                "{\"data\":[{\"index\":-1,\"embedding\":[1,2,3]},{\"index\":1,\"embedding\":[4,5,6]}]}",
                "{\"data\":[{\"index\":0,\"embedding\":[1,2,3]},{\"index\":2,\"embedding\":[4,5,6]}]}",
                "{\"data\":[{\"index\":0.0,\"embedding\":[1,2,3]},{\"index\":1,\"embedding\":[4,5,6]}]}",
                "{\"data\":[{\"index\":\"0\",\"embedding\":[1,2,3]},{\"index\":1,\"embedding\":[4,5,6]}]}",
                "{\"data\":[{\"index\":false,\"embedding\":[1,2,3]},{\"index\":1,\"embedding\":[4,5,6]}]}",
                "{\"data\":[{\"index\":null,\"embedding\":[1,2,3]},{\"index\":1,\"embedding\":[4,5,6]}]}",
                "{\"data\":[{\"index\":0,\"embedding\":[1,2,3]}]}",
                "{\"data\":[{\"index\":0,\"embedding\":[1,2]},{\"index\":1,\"embedding\":[4,5,6]}]}",
                "{\"data\":[{\"index\":0,\"embedding\":[\"not-a-number\",2,3]},{\"index\":1,\"embedding\":[4,5,6]}]}",
                "{\"data\":[{\"index\":0,\"embedding\":[false,2,3]},{\"index\":1,\"embedding\":[4,5,6]}]}",
                "{\"data\":[{\"index\":0,\"embedding\":[\"NaN\",2,3]},{\"index\":1,\"embedding\":[4,5,6]}]}",
                "{\"data\":[{\"index\":0,\"embedding\":[\"Infinity\",2,3]},{\"index\":1,\"embedding\":[4,5,6]}]}",
                "{\"data\":[{\"index\":0,\"embedding\":[1e308,2,3]},{\"index\":1,\"embedding\":[4,5,6]}]}",
                "{\"data\":[{\"index\":0,\"embedding\":[null,2,3]},{\"index\":1,\"embedding\":[4,5,6]}]}"
        );
    }

    @ParameterizedTest
    @MethodSource("invalidResponses")
    void rejectsTheWholeResponse(String body) {
        reply = ignored -> body;
        assertThatThrownBy(() -> embedding.embedDocuments(List.of("Synthetic alpha", "Synthetic beta")))
                .isInstanceOf(RuntimeException.class)
                .satisfies(failure -> assertThat(failure.getCause()).isNull());
        assertThat(requests.get()).isEqualTo(1);
    }

    @Test
    void restoresExplicitUnsortedIndicesWithoutChangingTheModelRequest() {
        reply = ignored -> "{\"data\":[{\"index\":1,\"embedding\":[4,5,6]},{\"index\":0,\"embedding\":[1,2,3]}]}";
        List<float[]> result = embedding.embedDocuments(List.of("Synthetic alpha", "Synthetic beta"));
        assertThat(result.get(0)).containsExactly(1, 2, 3);
        assertThat(result.get(1)).containsExactly(4, 5, 6);
        assertThat(lastRequest.path("model").asText()).isEqualTo("text-embedding-v4");
        assertThat(lastRequest.path("dimensions").asInt()).isEqualTo(3);
        assertThat(requests.get()).isEqualTo(1);
    }

    @Test
    void aMalformedFinalBatchCannotWriteTheEarlierValidBatch() {
        reply = request -> {
            int count = request.path("input").size();
            if (count == 1) return "{\"data\":[{\"embedding\":[1,2,3]}]}";
            List<Map<String, Object>> rows = new ArrayList<>();
            for (int index = 0; index < count; index++) rows.add(Map.of("index", index, "embedding", List.of(1, 2, 3)));
            return mapper.writeValueAsString(Map.of("data", rows));
        };
        List<KnowledgeChunkDraft> chunks = new ArrayList<>();
        for (int index = 0; index < 11; index++) chunks.add(new KnowledgeChunkDraft(
                "PARAGRAPH", "Synthetic", "paragraph-" + index, "Synthetic evidence " + (char) ('a' + index)));
        when(parser.parse(eq("fixture.docx"), eq("Fixture"), any(byte[].class)))
                .thenReturn(new ParsedKnowledgeDocument("Fixture", "fixture.docx", "docx", chunks));
        var file = new MockMultipartFile("file", "fixture.docx", "application/octet-stream", new byte[]{1});
        assertThatThrownBy(() -> documents.ingest(file, "synthetic", "fixture", "Fixture"))
                .isInstanceOf(IllegalStateException.class);
        assertThat(requests.get()).isEqualTo(2);
        verify(vectors, never()).upsert(anyList());
        verify(vectors, never()).setPayload(anyMap(), anyMap());
        verifyNoInteractions(graphs);
    }

    @Test
    void upstreamFailureIsNotRetriedAndDoesNotRetainResponseDetails() {
        status = 503;
        reply = ignored -> "{\"secret\":\"synthetic-response-must-not-escape\"}";
        assertThatThrownBy(() -> embedding.embedDocuments(List.of("Synthetic alpha")))
                .isInstanceOf(ExternalServiceException.class).hasNoCause()
                .hasMessageNotContaining("synthetic-response-must-not-escape");
        assertThat(requests.get()).isEqualTo(1);
    }

    @Test
    void malformedJsonDoesNotRetainTheRawParserCause() {
        reply = ignored -> "synthetic-response-must-not-escape";
        assertThatThrownBy(() -> embedding.embedDocuments(List.of("Synthetic alpha")))
                .isInstanceOf(ExternalServiceException.class).hasNoCause()
                .hasMessageNotContaining("synthetic-response-must-not-escape");
        assertThat(requests.get()).isEqualTo(1);
    }
}
