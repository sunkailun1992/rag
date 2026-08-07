package com.kellen.rag.service.impl;

import com.kellen.rag.client.EmbeddingClient;
import com.kellen.rag.client.RerankClient;
import com.kellen.rag.client.VectorStoreClient;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.model.RerankResult;
import com.kellen.rag.model.StoredVectorPoint;
import com.kellen.rag.model.VectorPoint;
import com.kellen.rag.model.VectorSearchHit;
import com.kellen.rpc.rag.RagRetrievalRpcDTO;
import com.kellen.rpc.rag.RagRetrievalRpcRequest;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

class DefaultRagRetrievalServiceTest {

    @Test
    void shouldRejectMissingTenantWithoutCallingExternalServices() {
        DefaultRagRetrievalService service = new DefaultRagRetrievalService(
                new RagVectorProperties(), new NoopEmbeddingClient(), new NoopVectorStoreClient(), new NoopRerankClient());
        RagRetrievalRpcRequest request = new RagRetrievalRpcRequest();
        request.setQuery("业务分析需要参考什么知识");

        RagRetrievalRpcDTO result = service.retrieve(request);

        assertFalse(result.isMatched());
        assertEquals("tenant context is missing", result.getMessage());
        assertEquals("", result.getContext());
        assertEquals(0, result.getSourceCount());
        assertEquals(0, result.getChunks().size());
    }

    @Test
    void shouldRejectBlankQuery() {
        DefaultRagRetrievalService service = new DefaultRagRetrievalService(
                new RagVectorProperties(), new NoopEmbeddingClient(), new NoopVectorStoreClient(), new NoopRerankClient());
        RagRetrievalRpcDTO result = service.retrieve(new RagRetrievalRpcRequest());

        assertFalse(result.isMatched());
        assertEquals("query is blank", result.getMessage());
        assertEquals("", result.getContext());
        assertEquals(0, result.getSourceCount());
        assertEquals(0, result.getChunks().size());
    }

    private static class NoopEmbeddingClient implements EmbeddingClient {
        @Override
        public List<float[]> embedDocuments(List<String> texts) {
            throw new AssertionError("not expected");
        }

        @Override
        public float[] embedQuery(String query) {
            throw new AssertionError("not expected");
        }
    }

    private static class NoopRerankClient implements RerankClient {
        @Override
        public List<RerankResult> rerank(String query, List<String> documents, int topN) {
            throw new AssertionError("not expected");
        }
    }

    private static class NoopVectorStoreClient implements VectorStoreClient {
        @Override public boolean collectionExists() { throw new AssertionError("not expected"); }
        @Override public void ensureCollection() { throw new AssertionError("not expected"); }
        @Override public void upsert(List<VectorPoint> points) { throw new AssertionError("not expected"); }
        @Override public List<StoredVectorPoint> scroll(Map<String, Object> exactFilter, int limit, boolean withVector) { throw new AssertionError("not expected"); }
        @Override public List<VectorSearchHit> search(float[] vector, Map<String, Object> exactFilter, int limit, double scoreThreshold) { throw new AssertionError("not expected"); }
        @Override public void setPayload(Map<String, Object> exactFilter, Map<String, Object> payload) { throw new AssertionError("not expected"); }
        @Override public long count(Map<String, Object> exactFilter) { throw new AssertionError("not expected"); }
    }
}
