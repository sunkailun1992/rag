package com.kellen.rag.store;

import com.kellen.rag.mapper.RagGraphEdgeMapper;
import com.kellen.rag.mapper.RagGraphNodeMapper;
import com.kellen.rag.entity.RagGraphNode;
import com.kellen.rag.entity.RagGraphEdge;
import com.kellen.rag.entity.enums.RagGraphNodeType;
import com.kellen.rag.entity.enums.RagGraphRelationType;
import com.kellen.rag.model.KnowledgeGraphQuery;
import com.kellen.rag.model.KnowledgeGraphViewMode;
import com.kellen.utils.context.TenantContextHolder;
import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.conditions.AbstractWrapper;
import com.baomidou.mybatisplus.core.conditions.Wrapper;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class MySqlKnowledgeGraphStoreTest {

    @BeforeEach
    void initializeMapperMetadata() {
        var assistant = new MapperBuilderAssistant(new MybatisConfiguration(), "graph-test");
        TableInfoHelper.initTableInfo(assistant, RagGraphNode.class);
        TableInfoHelper.initTableInfo(assistant, RagGraphEdge.class);
        TenantContextHolder.setTenantId("tenant-a");
    }

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

    @Test
    void shouldIncludeUnconnectedPdfAndImageDocumentsAlongsideExcelConcepts() {
        var excel = node("excel", RagGraphNodeType.DOCUMENT);
        var pdf = node("pdf", RagGraphNodeType.DOCUMENT);
        var image = node("image", RagGraphNodeType.DOCUMENT);
        var concept = node("concept", RagGraphNodeType.CONCEPT);
        var result = fixture(List.of(excel, pdf, image, concept), List.of(excel, pdf, image),
                List.of(edge("e1", "excel", "concept"))).query(query(null, null, 20));

        assertThat(result.nodes()).extracting("id").containsExactlyInAnyOrder("excel", "pdf", "image", "concept");
        assertThat(result.edges()).extracting("id").containsExactly("e1");
        assertThat(result.truncated()).isFalse();
    }

    @Test
    void shouldKeepMatchedDocumentsWhenNeighborsWouldConsumeTheLimit() {
        List<RagGraphNode> documents = new ArrayList<>();
        List<RagGraphNode> nodes = new ArrayList<>();
        List<RagGraphEdge> edges = new ArrayList<>();
        for (int index = 0; index < 20; index++) {
            var document = node("matched-" + index, RagGraphNodeType.DOCUMENT);
            documents.add(document);
            nodes.add(document);
            nodes.add(node("concept-" + index, RagGraphNodeType.CONCEPT));
            edges.add(edge("edge-" + index, document.getId(), "concept-" + index));
        }
        var result = fixture(nodes, documents, edges).query(query("matched", null, 20));

        assertThat(result.nodes()).extracting("id").containsExactlyInAnyOrderElementsOf(
                documents.stream().map(RagGraphNode::getId).toList());
        assertThat(result.edges()).isEmpty();
        assertThat(result.truncated()).isTrue();
    }

    @Test
    void shouldPreserveNeighborsForNodeTypeSearch() {
        var document = node("excel", RagGraphNodeType.DOCUMENT);
        var concept = node("concept", RagGraphNodeType.CONCEPT);
        var result = fixture(List.of(document, concept), List.of(concept),
                List.of(edge("e1", "excel", "concept"))).query(query(null, RagGraphNodeType.CONCEPT, 20));

        assertThat(result.nodes()).extracting("id").containsExactlyInAnyOrder("concept", "excel");
        assertThat(result.edges()).hasSize(1);
    }

    @Test
    void shouldBalanceDocumentsAndRelationsAndReportOverflow() {
        var documents = new ArrayList<RagGraphNode>();
        for (int index = 0; index < 21; index++) documents.add(node("doc-" + index, RagGraphNodeType.DOCUMENT));
        var concept = node("concept", RagGraphNodeType.CONCEPT);
        var all = new ArrayList<>(documents);
        all.add(concept);
        var result = fixture(all, documents, List.of(edge("e1", "doc-0", "concept")))
                .query(query(null, null, 20));

        assertThat(result.nodes()).hasSize(20);
        assertThat(result.nodes()).anyMatch(node -> node.id().equals("concept"));
        assertThat(result.nodes()).anyMatch(node -> node.id().equals("doc-1"));
        assertThat(result.edges()).hasSize(1);
        assertThat(result.truncated()).isTrue();
    }

    @Test
    void shouldReportEdgeTruncationOnlyWhenAnAdditionalEdgeExists() {
        var documents = new ArrayList<RagGraphNode>();
        var nodes = new ArrayList<RagGraphNode>();
        var edges = new ArrayList<RagGraphEdge>();
        for (int index = 0; index < 10; index++) {
            var document = node("doc-" + index, RagGraphNodeType.DOCUMENT);
            documents.add(document);
            nodes.add(document);
            nodes.add(node("concept-" + index, RagGraphNodeType.CONCEPT));
        }
        for (int index = 0; index < 80; index++) {
            edges.add(edge("edge-" + index, "doc-" + index / 10, "concept-" + index % 10));
        }
        var complete = fixture(nodes, documents, edges).query(query(null, null, 20));
        assertThat(complete.edges()).hasSize(80);
        assertThat(complete.truncated()).isFalse();
        edges.add(edge("extra", "doc-8", "concept-0"));
        var truncated = fixture(nodes, documents, edges).query(query(null, null, 20));
        assertThat(truncated.edges()).hasSize(80);
        assertThat(truncated.truncated()).isTrue();
    }

    @Test
    @SuppressWarnings({"unchecked", "rawtypes"})
    void shouldApplyKnowledgeBaseSearchAndTypeToStandaloneCandidates() {
        var nodeMapper = mock(RagGraphNodeMapper.class);
        var edgeMapper = mock(RagGraphEdgeMapper.class);
        when(nodeMapper.selectList(any(Wrapper.class))).thenReturn(List.of());
        when(edgeMapper.selectList(any(Wrapper.class))).thenReturn(List.of());
        var store = new MySqlKnowledgeGraphStore(nodeMapper, edgeMapper);

        var result = store.query(new KnowledgeGraphQuery("tenant-a", "public-kb", "guide",
                RagGraphNodeType.DOCUMENT, KnowledgeGraphViewMode.OVERVIEW, 20));

        var capture = ArgumentCaptor.forClass(Wrapper.class);
        verify(nodeMapper, atLeastOnce()).selectList(capture.capture());
        for (var captured : capture.getAllValues()) {
            var wrapper = (AbstractWrapper<?, ?, ?>) captured;
            assertThat(wrapper.getSqlSegment()).contains("knowledge_base", "node_type", "LIKE", "LIMIT");
            assertThat(wrapper.getParamNameValuePairs().values()).contains("public-kb", "%guide%", RagGraphNodeType.DOCUMENT);
        }
        assertThat(result.nodes()).isEmpty();
        assertThat(result.edges()).isEmpty();
        assertThat(result.truncated()).isFalse();
    }

    @SuppressWarnings("unchecked")
    private MySqlKnowledgeGraphStore fixture(List<RagGraphNode> nodes, List<RagGraphNode> matches,
                                            List<RagGraphEdge> edges) {
        var nodeMapper = mock(RagGraphNodeMapper.class);
        var edgeMapper = mock(RagGraphEdgeMapper.class);
        Map<String, RagGraphNode> byId = nodes.stream().collect(Collectors.toMap(RagGraphNode::getId, node -> node));
        when(nodeMapper.selectList(any(Wrapper.class))).thenReturn(matches);
        when(nodeMapper.selectByIds(anyCollection())).thenAnswer(invocation -> {
            Collection<String> ids = invocation.getArgument(0);
            return ids.stream().map(byId::get).toList();
        });
        when(edgeMapper.selectList(any(Wrapper.class))).thenReturn(edges);
        return new MySqlKnowledgeGraphStore(nodeMapper, edgeMapper);
    }

    private KnowledgeGraphQuery query(String search, RagGraphNodeType type, int limit) {
        return new KnowledgeGraphQuery("tenant-a", "public-kb", search, type, KnowledgeGraphViewMode.OVERVIEW, limit);
    }

    private RagGraphNode node(String id, RagGraphNodeType type) {
        var node = new RagGraphNode();
        node.setId(id);
        node.setCode(id);
        node.setName(id);
        node.setKnowledgeBase("public-kb");
        node.setNodeType(type);
        node.setSorting(type == RagGraphNodeType.DOCUMENT ? 10 : 10000);
        node.setPropertiesJson("{}");
        return node;
    }

    private RagGraphEdge edge(String id, String source, String target) {
        var edge = new RagGraphEdge();
        edge.setId(id);
        edge.setCode(id);
        edge.setSourceNodeId(source);
        edge.setTargetNodeId(target);
        edge.setRelationType(RagGraphRelationType.MENTIONS);
        edge.setDocumentVersion(1);
        edge.setRelationLabel("关联");
        edge.setPropertiesJson("{}");
        return edge;
    }
}
