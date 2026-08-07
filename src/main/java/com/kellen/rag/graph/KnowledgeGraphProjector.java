package com.kellen.rag.graph;

import com.kellen.rag.entity.enums.RagGraphNodeType;
import com.kellen.rag.entity.enums.RagGraphRelationType;
import com.kellen.rag.model.KnowledgeGraphEdge;
import com.kellen.rag.model.KnowledgeGraphNode;
import com.kellen.rag.model.KnowledgeGraphProjection;
import com.kellen.rag.model.StoredVectorPoint;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.Normalizer;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 将已写入 Qdrant 的结构化知识切片确定性投影为节点和关系。
 *
 * <p>该投影器不调用大模型猜测实体或关系，所有概念都来自切片保留的完整表头和值，
 * 从而保证重复同步结果一致，也避免把未经审核的生成关系写入知识库。</p>
 */
@Component
public class KnowledgeGraphProjector {

    private static final Pattern FIELD_LINE = Pattern.compile("^- ([^:\\n]{1,100}):\\s*(.+)$");
    private static final Set<String> NOISE_FIELDS = Set.of("序号", "行号", "记录编号", "编号");
    private static final int MAX_CONCEPT_LENGTH = 96;

    /**
     * 投影一个文档的全部去重切片。
     *
     * @param tenantId 当前租户
     * @param points 同一文档的 Qdrant 切片
     * @return 与存储实现无关的图谱投影
     */
    public KnowledgeGraphProjection project(String tenantId, List<StoredVectorPoint> points) {
        if (!StringUtils.hasText(tenantId) || points == null || points.isEmpty()) {
            throw new IllegalArgumentException("知识图谱投影缺少租户或文档切片");
        }
        Map<String, Object> first = points.get(0).payload();
        String knowledgeBase = required(first, "knowledge_base");
        String documentId = required(first, "document_id");
        String documentCode = required(first, "document_code");
        String documentTitle = required(first, "document_title");
        int documentVersion = number(first, "document_version");

        LinkedHashMap<String, KnowledgeGraphNode> nodes = new LinkedHashMap<>();
        LinkedHashMap<String, KnowledgeGraphEdge> edges = new LinkedHashMap<>();
        KnowledgeGraphNode document = node(tenantId, knowledgeBase, "document|" + documentId,
                RagGraphNodeType.DOCUMENT, documentTitle, "文档", documentId, documentCode,
                documentVersion, null, Map.of("documentCode", documentCode,
                        "documentVersion", documentVersion), 10);
        nodes.put(document.id(), document);

        int pointOrder = 0;
        for (StoredVectorPoint point : points) {
            Map<String, Object> payload = point.payload();
            requireSameDocument(tenantId, knowledgeBase, documentId, payload);
            String sectionPath = required(payload, "section_path");
            String chunkId = required(payload, "chunk_id");
            String locator = limited(required(payload, "source_locator"), 255);
            String chunkType = required(payload, "chunk_type");
            LinkedHashMap<String, String> fields = parseFields(required(payload, "content"));

            KnowledgeGraphNode section = node(tenantId, knowledgeBase,
                    "section|" + documentId + "|" + normalizedKey(sectionPath),
                    RagGraphNodeType.SECTION, sectionPath, "工作表", documentId, documentCode,
                    documentVersion, sectionPath, Map.of("sectionPath", sectionPath), 20);
            nodes.putIfAbsent(section.id(), section);
            addEdge(edges, edge(tenantId, knowledgeBase, document, section,
                    RagGraphRelationType.CONTAINS, "包含工作表", documentId, documentCode,
                    documentVersion, sectionPath, Map.of(), 10));

            KnowledgeGraphNode record = node(tenantId, knowledgeBase,
                    "record|" + documentId + "|" + chunkId,
                    RagGraphNodeType.RECORD, recordName(fields, locator), "知识记录", documentId,
                    documentCode, documentVersion, locator,
                    Map.of("chunkId", chunkId, "chunkType", chunkType, "sectionPath", sectionPath),
                    100 + pointOrder++);
            nodes.put(record.id(), record);
            addEdge(edges, edge(tenantId, knowledgeBase, section, record,
                    RagGraphRelationType.CONTAINS, "包含记录", documentId, documentCode,
                    documentVersion, locator, Map.of(), record.sorting()));

            for (Map.Entry<String, String> field : fields.entrySet()) {
                if (!isConcept(field.getKey(), field.getValue())) {
                    continue;
                }
                String conceptKey = "concept|" + normalizedKey(field.getKey()) + "|"
                        + normalizedKey(field.getValue());
                KnowledgeGraphNode concept = node(tenantId, knowledgeBase, conceptKey,
                        RagGraphNodeType.CONCEPT, field.getValue(), field.getKey(), null, null,
                        null, null, Map.of("field", field.getKey()), 10000);
                nodes.putIfAbsent(concept.id(), concept);
                addEdge(edges, edge(tenantId, knowledgeBase, record, concept,
                        RagGraphRelationType.HAS_ATTRIBUTE, field.getKey(), documentId, documentCode,
                        documentVersion, locator, Map.of("field", field.getKey()), record.sorting()));
                addEdge(edges, edge(tenantId, knowledgeBase, document, concept,
                        RagGraphRelationType.MENTIONS, field.getKey(), documentId, documentCode,
                        documentVersion, locator, Map.of("field", field.getKey()), record.sorting()));
            }
        }
        return new KnowledgeGraphProjection(tenantId, knowledgeBase, documentId, documentCode,
                documentVersion, List.copyOf(nodes.values()), List.copyOf(edges.values()));
    }

    private KnowledgeGraphNode node(String tenantId, String knowledgeBase, String stableKey,
                                    RagGraphNodeType type, String name, String label,
                                    String documentId, String documentCode, Integer documentVersion,
                                    String locator, Map<String, Object> properties, int sorting) {
        String id = stableId("node", tenantId + "|" + knowledgeBase + "|" + stableKey);
        return new KnowledgeGraphNode(id, id, type, limited(name, 255), limited(label, 255),
                documentId, documentCode, documentVersion, limited(locator, 255),
                Map.copyOf(properties), sorting);
    }

    private KnowledgeGraphEdge edge(String tenantId, String knowledgeBase,
                                    KnowledgeGraphNode source, KnowledgeGraphNode target,
                                    RagGraphRelationType type, String label, String documentId,
                                    String documentCode, int documentVersion, String locator,
                                    Map<String, Object> properties, int sorting) {
        String key = documentId + "|" + source.id() + "|" + target.id() + "|" + type
                + "|" + normalizedKey(label);
        String id = stableId("edge", tenantId + "|" + knowledgeBase + "|" + key);
        return new KnowledgeGraphEdge(id, id, source.id(), target.id(), type, limited(label, 255),
                documentId, documentCode, documentVersion, limited(locator, 255),
                Map.copyOf(properties), sorting);
    }

    private void addEdge(Map<String, KnowledgeGraphEdge> edges, KnowledgeGraphEdge edge) {
        edges.putIfAbsent(edge.id(), edge);
    }

    private LinkedHashMap<String, String> parseFields(String content) {
        LinkedHashMap<String, String> fields = new LinkedHashMap<>();
        for (String line : content.split("\\n")) {
            Matcher matcher = FIELD_LINE.matcher(line.trim());
            if (matcher.matches()) {
                fields.putIfAbsent(matcher.group(1).trim(), matcher.group(2).trim());
            }
        }
        return fields;
    }

    private boolean isConcept(String field, String value) {
        if (!StringUtils.hasText(field) || !StringUtils.hasText(value)
                || NOISE_FIELDS.contains(field.trim()) || value.length() > MAX_CONCEPT_LENGTH
                || value.contains("\n")) {
            return false;
        }
        return !value.matches("[\\d.%-]+");
    }

    private String recordName(Map<String, String> fields, String locator) {
        List<String> preferred = List.of("名称", "标题", "问题", "症状", "量表", "维度", "关键词");
        for (String keyword : preferred) {
            String value = fields.entrySet().stream()
                    .filter(entry -> entry.getKey().contains(keyword))
                    .map(Map.Entry::getValue)
                    .filter(item -> item.length() <= MAX_CONCEPT_LENGTH)
                    .findFirst().orElse(null);
            if (StringUtils.hasText(value)) {
                return value;
            }
        }
        return fields.values().stream()
                .filter(value -> StringUtils.hasText(value) && value.length() <= MAX_CONCEPT_LENGTH)
                .findFirst().orElse(locator);
    }

    private void requireSameDocument(String tenantId, String knowledgeBase, String documentId,
                                     Map<String, Object> payload) {
        if (!tenantId.equals(required(payload, "tenant_id"))
                || !knowledgeBase.equals(required(payload, "knowledge_base"))
                || !documentId.equals(required(payload, "document_id"))) {
            throw new IllegalArgumentException("知识图谱投影包含跨租户或跨文档切片");
        }
    }

    private String stableId(String prefix, String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder(32);
            for (int index = 0; index < 16; index++) {
                hex.append(String.format("%02x", digest[index]));
            }
            return prefix + "_" + hex;
        } catch (Exception exception) {
            throw new IllegalStateException("知识图谱稳定编码生成失败", exception);
        }
    }

    private String normalizedKey(String value) {
        return Normalizer.normalize(value == null ? "" : value, Normalizer.Form.NFKC)
                .toLowerCase(Locale.ROOT)
                .replaceAll("\\s+", " ")
                .trim();
    }

    private String required(Map<String, Object> payload, String key) {
        Object value = payload.get(key);
        if (value == null || !StringUtils.hasText(String.valueOf(value))) {
            throw new IllegalArgumentException("知识切片缺少图谱字段: " + key);
        }
        return String.valueOf(value);
    }

    private int number(Map<String, Object> payload, String key) {
        Object value = payload.get(key);
        if (value instanceof Number number) {
            return number.intValue();
        }
        return Integer.parseInt(required(payload, key));
    }

    private String limited(String value, int maxLength) {
        if (value == null || value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength);
    }
}
