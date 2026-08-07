package com.kellen.rag.graph;

import com.kellen.rag.entity.enums.RagGraphNodeType;
import com.kellen.rag.entity.enums.RagGraphRelationType;
import com.kellen.rag.model.KnowledgeGraphProjection;
import com.kellen.rag.model.StoredVectorPoint;
import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class KnowledgeGraphProjectorTest {

    private final KnowledgeGraphProjector projector = new KnowledgeGraphProjector();

    @Test
    void shouldPreserveHeaderSemanticsAndReuseSharedConceptNodes() {
        KnowledgeGraphProjection projection = projector.project("tenant-a", List.of(
                point("chunk-1", "第2行", "- 风险等级: 高\n- 建议: 建议A"),
                point("chunk-2", "第3行", "- 风险等级: 高\n- 建议: 建议B")
        ));

        assertThat(projection.nodes()).hasSize(7);
        assertThat(projection.nodes())
                .filteredOn(node -> node.type() == RagGraphNodeType.CONCEPT)
                .extracting("label", "name")
                .containsExactlyInAnyOrder(
                        org.assertj.core.groups.Tuple.tuple("风险等级", "高"),
                        org.assertj.core.groups.Tuple.tuple("建议", "建议A"),
                        org.assertj.core.groups.Tuple.tuple("建议", "建议B")
                );
        assertThat(projection.edges())
                .filteredOn(edge -> edge.type() == RagGraphRelationType.MENTIONS
                        && "风险等级".equals(edge.label()))
                .hasSize(1);
        assertThat(projection.edges()).hasSize(10);
    }

    @Test
    void shouldIncludeTenantInStableIdentifiers() {
        List<StoredVectorPoint> points = List.of(
                point("chunk-1", "第2行", "- 风险等级: 高")
        );

        KnowledgeGraphProjection first = projector.project("tenant-a", points);
        List<StoredVectorPoint> otherTenantPoints = List.of(new StoredVectorPoint("point-chunk-1",
                payload("tenant-b", "chunk-1", "第2行", "- 风险等级: 高"), 0));
        KnowledgeGraphProjection second = projector.project("tenant-b", otherTenantPoints);

        assertThat(first.nodes().get(0).id()).isNotEqualTo(second.nodes().get(0).id());
    }

    private StoredVectorPoint point(String chunkId, String locator, String fields) {
        return new StoredVectorPoint("point-" + chunkId,
                payload("tenant-a", chunkId, locator, fields), 0);
    }

    private Map<String, Object> payload(String tenantId, String chunkId, String locator, String fields) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("tenant_id", tenantId);
        payload.put("knowledge_base", "general-knowledge");
        payload.put("document_id", "document-1");
        payload.put("document_code", "structured-knowledge");
        payload.put("document_title", "结构化知识库");
        payload.put("document_version", 1);
        payload.put("section_path", "对齐结果");
        payload.put("chunk_id", chunkId);
        payload.put("source_locator", locator);
        payload.put("chunk_type", "xlsx-row");
        payload.put("content", "文档: 结构化知识库\n位置: 对齐结果 / " + locator
                + "\n表头语义与记录:\n" + fields);
        return payload;
    }
}
