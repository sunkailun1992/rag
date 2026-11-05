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
        Map<String, Object> documentProperties = sourceProperties(first);
        documentProperties.put("documentCode", documentCode);
        documentProperties.put("documentVersion", documentVersion);
        KnowledgeGraphNode document = node(tenantId, knowledgeBase, "document|" + documentId,
                RagGraphNodeType.DOCUMENT, documentTitle, "文档", documentId, documentCode,
                documentVersion, null, documentProperties, 10);
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
            String sectionLabel = sectionLabel(payload, chunkType);
            boolean pageChunk = sectionLabel.equals("页面") || sectionLabel.equals("图片");
            Map<String, Object> metadata = sourceMetadata(payload);
            Map<String, Object> sectionProperties = sourceProperties(payload);
            sectionProperties.put("sectionPath", sectionPath);
            // 区块坐标不能冒充整页坐标；章节节点只保留页码。
            if (metadata.containsKey("page")) {
                sectionProperties.put("sourceMetadata", Map.of("page", metadata.get("page")));
            }

            KnowledgeGraphNode section = node(tenantId, knowledgeBase,
                    "section|" + documentId + "|" + normalizedKey(sectionPath),
                    RagGraphNodeType.SECTION, sectionPath, sectionLabel,
                    documentId, documentCode,
                    documentVersion, sectionPath, sectionProperties, 20);
            nodes.putIfAbsent(section.id(), section);
            addEdge(edges, edge(tenantId, knowledgeBase, document, section,
                    RagGraphRelationType.CONTAINS, "包含" + sectionLabel,
                    documentId, documentCode,
                    documentVersion, sectionPath, Map.of(), 10,
                    chunkType.startsWith("docx-") ? "包含章节" : "包含工作表"));

            Map<String, Object> recordProperties = sourceProperties(payload);
            recordProperties.put("chunkId", chunkId);
            recordProperties.put("chunkType", chunkType);
            recordProperties.put("sectionPath", sectionPath);
            if (!metadata.isEmpty()) recordProperties.put("sourceMetadata", metadata);
            KnowledgeGraphNode record = node(tenantId, knowledgeBase,
                    "record|" + documentId + "|" + chunkId,
                    RagGraphNodeType.RECORD, recordName(fields, locator), recordLabel(chunkType, pageChunk), documentId,
                    documentCode, documentVersion, locator,
                    recordProperties,
                    100 + pointOrder++);
            nodes.put(record.id(), record);
            addEdge(edges, edge(tenantId, knowledgeBase, section, record,
                    RagGraphRelationType.CONTAINS, pageChunk ? "包含区块" : "包含记录", documentId, documentCode,
                    documentVersion, locator, Map.of(), record.sorting(), "包含记录"));

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
        return edge(tenantId, knowledgeBase, source, target, type, label, documentId, documentCode,
                documentVersion, locator, properties, sorting, label);
    }

    private KnowledgeGraphEdge edge(String tenantId, String knowledgeBase,
                                    KnowledgeGraphNode source, KnowledgeGraphNode target,
                                    RagGraphRelationType type, String label, String documentId,
                                    String documentCode, int documentVersion, String locator,
                                    Map<String, Object> properties, int sorting, String identityLabel) {
        // 显示名升级不能重编码既有 PDF/图片结构边；identityLabel 保持原投影的稳定键。
        String key = documentId + "|" + source.id() + "|" + target.id() + "|" + type
                + "|" + normalizedKey(identityLabel);
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

    private String recordLabel(String chunkType, boolean pageChunk) {
        return switch (chunkType) {
            case "docx-paragraph" -> "知识段落";
            case "docx-table-row" -> "表格记录";
            case "pdf-text" -> "文字区块";
            case "ocr-text" -> "识别区块";
            case "table" -> pageChunk ? "表格区块" : "知识记录";
            default -> pageChunk ? "内容区块" : "知识记录";
        };
    }

    private String sectionLabel(Map<String, Object> payload, String chunkType) {
        if (chunkType.startsWith("docx-")) return "章节";
        String type = payload.get("source_type") instanceof String value ? value.toLowerCase(Locale.ROOT) : "";
        if (Set.of("png", "jpg", "jpeg").contains(type)) return "图片";
        if (type.equals("pdf") || chunkType.equals("pdf-text") || chunkType.equals("ocr-text")) return "页面";
        return "工作表";
    }

    private Map<String, Object> sourceProperties(Map<String, Object> payload) {
        Map<String, Object> properties = new LinkedHashMap<>();
        if (payload.get("source_name") instanceof String name && StringUtils.hasText(name)) {
            properties.put("sourceName", limited(name, 255));
        }
        if (payload.get("source_type") instanceof String type && type.matches("[A-Za-z0-9_-]{1,32}")) {
            properties.put("sourceType", type);
        }
        return properties;
    }

    /** 只发布来源定位字段；嵌套能力说明也按键和类型过滤，不能转发任意解析 payload。 */
    private Map<String, Object> sourceMetadata(Map<String, Object> payload) {
        Map<String, Object> metadata = new LinkedHashMap<>();
        if (!(payload.get("source_metadata") instanceof Map<?, ?> source)) return metadata;
        if (source.get("page") instanceof Number page && Double.isFinite(page.doubleValue())
                && page.doubleValue() > 0 && page.doubleValue() <= Integer.MAX_VALUE
                && page.doubleValue() == page.intValue()) {
            metadata.put("page", page.intValue());
        }
        if (source.get("block_type") instanceof String type && type.matches("[A-Za-z0-9_-]{1,64}")) {
            metadata.put("block_type", type);
        }
        if (source.get("bbox") instanceof List<?> box && box.size() == 4
                && box.stream().allMatch(value -> value instanceof Number number
                && Double.isFinite(number.doubleValue()) && number.doubleValue() >= 0)
                && ((Number) box.get(2)).doubleValue() >= ((Number) box.get(0)).doubleValue()
                && ((Number) box.get(3)).doubleValue() >= ((Number) box.get(1)).doubleValue()) {
            metadata.put("bbox", List.copyOf(box));
        }
        if (source.get("ocr_capabilities") instanceof Map<?, ?> capabilities) {
            Map<String, Object> approved = new LinkedHashMap<>();
            for (String key : List.of("textRecognition", "tableStructure", "layoutAnalysis")) {
                if (capabilities.get(key) instanceof Boolean value) approved.put(key, value);
            }
            if (!approved.isEmpty()) metadata.put("ocr_capabilities", Map.copyOf(approved));
        }
        return Map.copyOf(metadata);
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
