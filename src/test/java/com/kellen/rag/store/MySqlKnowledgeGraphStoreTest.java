package com.kellen.rag.store;

import com.kellen.rag.mapper.RagGraphEdgeMapper;
import com.kellen.rag.mapper.RagGraphNodeMapper;
import com.kellen.rag.model.KnowledgeGraphQuery;
import com.kellen.rag.model.KnowledgeGraphViewMode;
import com.kellen.utils.context.TenantContextHolder;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;

class MySqlKnowledgeGraphStoreTest {

    @AfterEach
    void clearTenant() {
        TenantContextHolder.clear();
    }

    @Test
    void shouldRejectQueryWhenRequestedTenantDiffersFromContext() {
        RagGraphNodeMapper nodeMapper = mock(RagGraphNodeMapper.class);
        RagGraphEdgeMapper edgeMapper = mock(RagGraphEdgeMapper.class);
        MySqlKnowledgeGraphStore store = new MySqlKnowledgeGraphStore(nodeMapper, edgeMapper);
        TenantContextHolder.setTenantId("tenant-a");

        assertThrows(IllegalStateException.class, () -> store.query(new KnowledgeGraphQuery(
                "tenant-b", "general-knowledge", null, null, KnowledgeGraphViewMode.OVERVIEW, 100)));

        verifyNoInteractions(nodeMapper, edgeMapper);
    }
}
