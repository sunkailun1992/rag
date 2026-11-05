package com.kellen.rag.graph;

import com.kellen.rag.entity.enums.RagGraphNodeType;
import com.kellen.rag.entity.enums.RagGraphRelationType;
import com.kellen.rag.model.KnowledgeGraphProjection;
import com.kellen.rag.model.StoredVectorPoint;
import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.groups.Tuple.tuple;

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

    @Test
    void shouldDescribePdfPagesAndPreserveOnlyApprovedSourceProperties() {
        Map<String, Object> data = payload("tenant-a", "pdf-1", "第2页 / 区块3 / 切片1", "普通原文");
        data.put("source_name", "guide.pdf");
        data.put("source_type", "pdf");
        data.put("section_path", "第2页");
        data.put("chunk_type", "pdf-text");
        data.put("source_metadata", Map.of("page", 2, "block_type", "pdf-text",
                "bbox", List.of(1, 2, 30, 40), "secret", "must-not-return",
                "ocr_capabilities", Map.of("textRecognition", true, "tableStructure", false,
                        "layoutAnalysis", false, "secret", "must-not-return")));
        data.put("api_key", "must-not-return");

        var projection = projector.project("tenant-a", List.of(new StoredVectorPoint("pdf-1", data, 0)));

        assertThat(projection.nodes()).extracting("type", "label")
                .contains(tuple(RagGraphNodeType.SECTION, "页面"), tuple(RagGraphNodeType.RECORD, "文字区块"));
        var document = projection.nodes().stream().filter(node -> node.type() == RagGraphNodeType.DOCUMENT)
                .findFirst().orElseThrow();
        assertThat(document.properties()).containsEntry("sourceName", "guide.pdf").containsEntry("sourceType", "pdf");
        var section = projection.nodes().stream().filter(node -> node.type() == RagGraphNodeType.SECTION)
                .findFirst().orElseThrow();
        assertThat(section.properties()).containsEntry("sourceMetadata", Map.of("page", 2));
        var record = projection.nodes().stream().filter(node -> node.type() == RagGraphNodeType.RECORD)
                .findFirst().orElseThrow();
        assertThat(record.properties()).containsOnlyKeys("chunkId", "chunkType", "sectionPath",
                "sourceName", "sourceType", "sourceMetadata");
        assertThat(record.properties().get("sourceMetadata")).isEqualTo(Map.of("page", 2,
                "block_type", "pdf-text", "bbox", List.of(1, 2, 30, 40),
                "ocr_capabilities", Map.of("textRecognition", true, "tableStructure", false, "layoutAnalysis", false)));
        assertThat(projection.nodes()).noneMatch(node -> node.type() == RagGraphNodeType.CONCEPT);
    }

    @Test
    void shouldDescribeImagesAndKeepOfficeLabelsCompatible() {
        for (String type : List.of("png", "jpg", "jpeg")) {
            var data = payload("tenant-a", "image-1", "第1页 / 区块1", "识别文字");
            data.put("source_type", type);
            data.put("chunk_type", "ocr-text");
            var projection = projector.project("tenant-a", List.of(new StoredVectorPoint("image", data, 0)));
            assertThat(projection.nodes()).extracting("type", "label").contains(
                    tuple(RagGraphNodeType.SECTION, "图片"), tuple(RagGraphNodeType.RECORD, "识别区块"));
            assertThat(projection.edges()).anyMatch(edge -> edge.label().equals("包含图片"));
        }
        for (String chunkType : List.of("xlsx-row", "docx-paragraph", "docx-table-row")) {
            var data = payload("tenant-a", "office", "原始位置", "- 名称: 示例");
            data.put("chunk_type", chunkType);
            var projection = projector.project("tenant-a", List.of(new StoredVectorPoint("office", data, 0)));
            String sectionLabel = chunkType.startsWith("docx-") ? "章节" : "工作表";
            String recordLabel = chunkType.equals("docx-paragraph") ? "知识段落"
                    : chunkType.equals("docx-table-row") ? "表格记录" : "知识记录";
            assertThat(projection.nodes()).extracting("type", "label").contains(
                    tuple(RagGraphNodeType.SECTION, sectionLabel), tuple(RagGraphNodeType.RECORD, recordLabel));
        }
    }

    @Test
    void shouldDiscardMalformedMetadataWithoutChangingStableIdentifiers() {
        var data = payload("tenant-a", "chunk-1", "第1页", "原文");
        var original = projector.project("tenant-a", List.of(new StoredVectorPoint("point", data, 0)));
        data.put("source_type", "pdf");
        data.put("source_metadata", Map.of("page", "private-value", "bbox", List.of(0, 0, Double.NaN, 10),
                "block_type", Map.of("secret", "must-not-return"), "ocr_capabilities",
                Map.of("textRecognition", "must-not-return", "secret", true)));
        var updated = projector.project("tenant-a", List.of(new StoredVectorPoint("point", data, 0)));
        assertThat(updated.nodes()).extracting("id").containsExactlyElementsOf(
                original.nodes().stream().map(node -> node.id()).toList());
        assertThat(updated.edges()).extracting("id").containsExactlyElementsOf(
                original.edges().stream().map(edge -> edge.id()).toList());
        assertThat(updated.nodes()).noneMatch(node -> node.properties().containsKey("sourceMetadata"));
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
