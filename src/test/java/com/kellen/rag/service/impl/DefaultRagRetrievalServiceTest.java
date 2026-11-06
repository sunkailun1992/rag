package com.kellen.rag.service.impl;

import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rpc.rag.RagRetrievalRpcDTO;
import com.kellen.rpc.rag.RagRetrievalRpcRequest;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

class DefaultRagRetrievalServiceTest {

    @Test
    void shouldRejectMissingTenantWithoutCallingExternalServices() {
        DefaultRagRetrievalService service = new DefaultRagRetrievalService(
                new RagVectorProperties(),
                org.mockito.Mockito.mock(com.kellen.rag.client.RagEngineClient.class));
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
                new RagVectorProperties(),
                org.mockito.Mockito.mock(com.kellen.rag.client.RagEngineClient.class));
        RagRetrievalRpcDTO result = service.retrieve(new RagRetrievalRpcRequest());

        assertFalse(result.isMatched());
        assertEquals("query is blank", result.getMessage());
        assertEquals("", result.getContext());
        assertEquals(0, result.getSourceCount());
        assertEquals(0, result.getChunks().size());
    }

}
