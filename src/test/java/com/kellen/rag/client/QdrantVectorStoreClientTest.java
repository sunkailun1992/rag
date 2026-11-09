package com.kellen.rag.client;

import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.exception.ExternalServiceException;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.JsonNode;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;

class QdrantVectorStoreClientTest {

    private HttpServer server;

    @AfterEach
    void stopServer() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    void shouldTreatMissingCollectionAsEmptyOnlyWhenQdrantApiIsHealthy() throws IOException {
        startServer(200);

        assertFalse(client().collectionExists());
    }

    @Test
    void shouldReportConfiguredEndpointMismatchInsteadOfTreatingItAsEmptyCollection() throws IOException {
        startServer(404);

        ExternalServiceException exception = assertThrows(
                ExternalServiceException.class, () -> client().collectionExists());

        assertEquals("Qdrant API", exception.getServiceName());
        assertEquals(404, exception.getStatusCode());
        assertEquals("Qdrant API 请求失败，HTTP 404", exception.getPublicMessage());
    }

    @Test
    void shouldProvisionReadinessSparseModelKeywordIndexWithoutChangingPoints() throws IOException {
        ObjectMapper mapper = new ObjectMapper();
        List<JsonNode> indexes = new CopyOnWriteArrayList<>();
        List<String> indexQueries = new CopyOnWriteArrayList<>();
        List<String> unexpected = new CopyOnWriteArrayList<>();
        AtomicInteger collectionReads = new AtomicInteger();
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/collections/test-collection", exchange -> {
            if ("PUT".equals(exchange.getRequestMethod())
                    && "/collections/test-collection/index".equals(exchange.getRequestURI().getPath())) {
                indexes.add(mapper.readTree(exchange.getRequestBody()));
                indexQueries.add(exchange.getRequestURI().getQuery());
            } else if ("GET".equals(exchange.getRequestMethod())
                    && "/collections/test-collection".equals(exchange.getRequestURI().getPath())) {
                collectionReads.incrementAndGet();
            } else {
                unexpected.add(exchange.getRequestMethod() + " " + exchange.getRequestURI().getPath());
            }
            byte[] response = "{\"result\":true}".getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, response.length);
            exchange.getResponseBody().write(response);
            exchange.close();
        });
        server.start();

        client().ensureCollection();

        assertEquals(Set.of("tenant_id", "knowledge_base", "document_id", "document_code", "document_status",
                        "file_sha256", "content_sha256", "sparse_model"),
                indexes.stream().map(index -> index.path("field_name").asText()).collect(Collectors.toSet()));
        assertEquals(8, indexes.size());
        assertEquals(1, collectionReads.get());
        assertEquals(List.of(), unexpected);
        for (int i = 0; i < indexes.size(); i++) {
            assertEquals("keyword", indexes.get(i).path("field_schema").asText());
            assertEquals(2, indexes.get(i).size());
            assertEquals("wait=true", indexQueries.get(i));
        }
    }

    private void startServer(int collectionsStatus) throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/collections/test-collection", exchange -> respond(exchange, 404));
        server.createContext("/collections", exchange -> respond(exchange, collectionsStatus));
        server.start();
    }

    private QdrantVectorStoreClient client() {
        RagVectorProperties properties = new RagVectorProperties();
        properties.setQdrantUrl("http://127.0.0.1:" + server.getAddress().getPort());
        properties.setCollectionName("test-collection");
        return new QdrantVectorStoreClient(new ObjectMapper(), properties);
    }

    private void respond(HttpExchange exchange, int status) throws IOException {
        exchange.sendResponseHeaders(status, -1);
        exchange.close();
    }
}
