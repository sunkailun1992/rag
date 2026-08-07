package com.kellen.rag.client;

import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.exception.ExternalServiceException;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.net.InetSocketAddress;

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
