package com.kellen.rag.client;

import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.entity.vo.GraphBuildVO;
import com.kellen.rag.graph.KnowledgeGraphProjector;
import com.kellen.rag.model.ActiveGraphSource;
import com.kellen.rag.model.KnowledgeGraphProjection;
import com.kellen.rag.service.impl.DefaultRagKnowledgeGraphService;
import com.kellen.rag.store.KnowledgeGraphStore;
import com.kellen.utils.context.TenantContextHolder;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/** 经实际本机 HTTP 分页，证明 Neo4j 身份快照不下载正文且旧结构投影仍取得全文。 */
class GraphSnapshotProjectionIntegrationTest {
    private static final Set<String> FIELDS = Set.of("tenant_id", "knowledge_base", "document_status",
            "document_id", "document_code", "document_version", "document_revision", "chunk_id", "content_sha256");
    private final ObjectMapper mapper = new ObjectMapper();
    private final List<JsonNode> scrollRequests = new CopyOnWriteArrayList<>();
    private HttpServer server;
    private int countRequests;
    private String pageMode;

    @AfterEach
    void stopServer() {
        TenantContextHolder.clear();
        if (server != null) server.stop(0);
    }

    @Test
    @SuppressWarnings("unchecked")
    void shouldReadOnlyNineIdentityFieldsAcrossFourPagesAnd866PointsForNeo4j() throws Exception {
        startServer(866);
        RagEngineClient engine = mock(RagEngineClient.class);
        when(engine.isGraphEnabled()).thenReturn(true);
        when(engine.buildGraph(anyString(), anyList(), eq(true))).thenReturn(
                new GraphBuildVO("build-1", "kb-1", "QUEUED", 1, 0, 0, 0, null, null, null));
        KnowledgeGraphStore legacy = mock(KnowledgeGraphStore.class);

        var result = service(engine, legacy).sync("kb-1");

        assertThat(result.status()).isEqualTo("QUEUED");
        assertThat(scrollRequests).hasSize(4);
        for (JsonNode request : scrollRequests) {
            assertThat(request.path("with_payload").isObject()).isTrue();
            var fields = mapper.convertValue(request.path("with_payload").path("include"), List.class);
            assertThat(fields).containsExactlyInAnyOrderElementsOf(FIELDS);
            assertThat(request.path("with_vector").asBoolean()).isFalse();
            assertThat(request.path("filter").path("must")).hasSize(3);
        }
        assertThat(scrollRequests.get(0).has("offset")).isFalse();
        assertThat(scrollRequests.get(1).path("offset").asInt()).isEqualTo(256);
        assertThat(scrollRequests.get(2).path("offset").asInt()).isEqualTo(512);
        assertThat(scrollRequests.get(3).path("offset").asInt()).isEqualTo(768);
        assertThat(countRequests).isEqualTo(2);
        ArgumentCaptor<List<ActiveGraphSource>> captured = ArgumentCaptor.forClass(List.class);
        verify(engine).buildGraph(eq("kb-1"), captured.capture(), eq(true));
        assertThat(captured.getValue()).hasSize(866).extracting(ActiveGraphSource::pointId).doesNotHaveDuplicates();
        assertThat(captured.getValue()).allSatisfy(source -> {
            assertThat(source.documentRevision()).isEqualTo(6_000_000_001L);
            assertThat(source.contentSha256()).isEqualTo("a".repeat(64));
        });
        verifyNoInteractions(legacy);
    }

    @Test
    void shouldKeepFullPayloadAndContentProjectionForLegacyMySqlBackend() throws Exception {
        startServer(2);
        RagEngineClient engine = mock(RagEngineClient.class);
        KnowledgeGraphStore legacy = mock(KnowledgeGraphStore.class);

        var result = service(engine, legacy).sync("kb-1");

        assertThat(result.documentCount()).isEqualTo(1);
        assertThat(scrollRequests).hasSize(1);
        assertThat(scrollRequests.get(0).path("with_payload").isBoolean()).isTrue();
        assertThat(scrollRequests.get(0).path("with_payload").asBoolean()).isTrue();
        ArgumentCaptor<KnowledgeGraphProjection> captured = ArgumentCaptor.forClass(KnowledgeGraphProjection.class);
        verify(legacy).save(captured.capture());
        assertThat(captured.getValue().nodes()).anySatisfy(node -> assertThat(node.name()).isEqualTo("高"));
        assertThat(captured.getValue().edges()).isNotEmpty();
    }

    @Test
    void shouldRejectUnimplementedProjectionInsteadOfFallingBackToFullPayload() {
        VectorStoreClient port = mock(VectorStoreClient.class, org.mockito.Answers.CALLS_REAL_METHODS);
        assertThatThrownBy(() -> port.scrollPayload(Map.of(), 10000, FIELDS))
                .isInstanceOf(UnsupportedOperationException.class);
        verify(port, org.mockito.Mockito.never()).scroll(org.mockito.ArgumentMatchers.anyMap(),
                org.mockito.ArgumentMatchers.anyInt(), org.mockito.ArgumentMatchers.anyBoolean());
    }

    @Test
    void shouldRejectProjectionWhenMorePointsRemainBeyondRequestedScopeLimit() throws Exception {
        startServer(2);
        assertThatThrownBy(() -> client().scrollPayload(Map.of("tenant_id", "tenant-a"), 1, FIELDS))
                .isInstanceOf(com.kellen.rag.exception.ExternalServiceException.class);
    }

    @Test
    void shouldRejectRepeatingAndMissingProjectionCursors() throws Exception {
        startServer(866);
        pageMode = "repeat";
        assertThatThrownBy(() -> client().scrollPayload(Map.of("tenant_id", "tenant-a"), 10000, FIELDS))
                .isInstanceOf(com.kellen.rag.exception.ExternalServiceException.class);
        assertThat(scrollRequests).hasSize(2);
        pageMode = "missing-cursor";
        assertThatThrownBy(() -> client().scrollPayload(Map.of("tenant_id", "tenant-a"), 10000, FIELDS))
                .isInstanceOf(com.kellen.rag.exception.ExternalServiceException.class);
    }

    @Test
    void shouldNotRetainUnexpectedBodyFieldsFromProjectionResponse() throws Exception {
        startServer(1);
        pageMode = "ignore-selector";
        var points = client().scrollPayload(Map.of("tenant_id", "tenant-a"), 10000, FIELDS);
        assertThat(points).hasSize(1);
        assertThat(points.get(0).payload().keySet()).containsExactlyInAnyOrderElementsOf(FIELDS);
        assertThat(points.get(0).payload()).doesNotContainKeys("content", "document_title", "source_name");
        assertThat(points.get(0).vectorDimension()).isZero();
    }

    private QdrantVectorStoreClient client() {
        RagVectorProperties properties = new RagVectorProperties();
        properties.setQdrantUrl("http://127.0.0.1:" + server.getAddress().getPort());
        properties.setCollectionName("fixture");
        return new QdrantVectorStoreClient(mapper, properties);
    }

    private DefaultRagKnowledgeGraphService service(RagEngineClient engine, KnowledgeGraphStore legacy) {
        RagVectorProperties properties = new RagVectorProperties();
        properties.setQdrantUrl("http://127.0.0.1:" + server.getAddress().getPort());
        properties.setCollectionName("fixture");
        TenantContextHolder.setTenantId("tenant-a");
        return new DefaultRagKnowledgeGraphService(properties, new QdrantVectorStoreClient(mapper, properties),
                new KnowledgeGraphProjector(), legacy, engine);
    }

    private void startServer(int total) throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/collections/fixture", exchange -> {
            Object response;
            if (exchange.getRequestURI().getPath().endsWith("/count")) {
                countRequests++;
                response = Map.of("result", Map.of("count", total));
            } else if (exchange.getRequestURI().getPath().endsWith("/scroll")) {
                JsonNode request = mapper.readTree(exchange.getRequestBody().readAllBytes());
                scrollRequests.add(request);
                int offset = request.path("offset").asInt(0);
                int end = Math.min(total, offset + request.path("limit").asInt());
                List<Map<String, Object>> points = new ArrayList<>();
                for (int index = offset; index < end; index++) {
                    Map<String, Object> payload = payload(index);
                    if (request.path("with_payload").isObject() && !"ignore-selector".equals(pageMode)) {
                        var fields = request.path("with_payload").path("include");
                        Map<String, Object> selected = new LinkedHashMap<>();
                        for (JsonNode field : fields) selected.put(field.asText(), payload.get(field.asText()));
                        payload = selected;
                    }
                    points.add(Map.of("id", "point-" + index, "payload", payload));
                }
                Map<String, Object> page = new LinkedHashMap<>();
                page.put("points", points);
                page.put("next_page_offset", end < total ? end : null);
                if ("repeat".equals(pageMode)) page.put("next_page_offset", 0);
                if ("missing-cursor".equals(pageMode)) page.remove("next_page_offset");
                response = Map.of("result", page);
            } else {
                response = Map.of("result", Map.of());
            }
            byte[] bytes = mapper.writeValueAsString(response).getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
    }

    private Map<String, Object> payload(int index) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("tenant_id", "tenant-a"); value.put("knowledge_base", "kb-1");
        value.put("document_status", "ACTIVE"); value.put("document_id", "doc-1");
        value.put("document_code", "code-1"); value.put("document_version", 2);
        value.put("document_revision", 6_000_000_001L); value.put("chunk_id", "chunk-" + index);
        value.put("content_sha256", "a".repeat(64));
        value.put("document_title", "Synthetic fixture"); value.put("source_name", "fixture.xlsx");
        value.put("source_type", "xlsx"); value.put("section_path", "Sheet1");
        value.put("source_locator", "row-" + index); value.put("chunk_type", "xlsx-row");
        value.put("content", "- 风险等级: 高\n" + "Synthetic body that metadata snapshots must not download. ".repeat(20));
        return value;
    }
}
