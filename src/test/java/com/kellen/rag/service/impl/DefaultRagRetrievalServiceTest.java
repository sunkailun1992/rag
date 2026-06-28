package com.kellen.rag.service.impl;

import com.kellen.rpc.rag.RagRetrievalRpcDTO;
import com.kellen.rpc.rag.RagRetrievalRpcRequest;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

class DefaultRagRetrievalServiceTest {

    @Test
    void shouldReturnEmptyContextUntilVectorRetrievalIsImplemented() {
        DefaultRagRetrievalService service = new DefaultRagRetrievalService();
        RagRetrievalRpcRequest request = new RagRetrievalRpcRequest();
        request.setQuery("患者报告分析需要参考什么知识");

        RagRetrievalRpcDTO result = service.retrieve(request);

        assertFalse(result.isEnabled());
        assertFalse(result.isMatched());
        assertEquals("", result.getContext());
        assertEquals(0, result.getSourceCount());
        assertEquals(0, result.getChunks().size());
    }
}
