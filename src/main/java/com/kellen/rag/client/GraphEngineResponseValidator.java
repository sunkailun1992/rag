package com.kellen.rag.client;

import com.kellen.rag.entity.enums.RagGraphNodeType;
import com.kellen.rag.entity.enums.RagGraphRelationType;
import com.kellen.rag.entity.vo.GraphBuildVO;
import com.kellen.rag.entity.vo.RagGraphEdgeVO;
import com.kellen.rag.entity.vo.RagGraphNodeVO;
import com.kellen.rag.entity.vo.RagKnowledgeGraphVO;
import com.kellen.rag.exception.ExternalServiceException;
import com.kellen.rag.model.ActiveGraphSource;
import com.kellen.rag.model.KnowledgeGraphViewMode;
import tools.jackson.databind.JsonNode;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/** 严格复核内部图服务的身份、作用域、证据和资源边界；上游错误不放宽过滤。 */
final class GraphEngineResponseValidator {
    private static final Set<String> STATUSES = Set.of("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED");

    private GraphEngineResponseValidator() {
    }

    static GraphBuildVO build(JsonNode result, String tenant, String knowledgeBase) {
        require(result.isObject() && knowledgeBase.equals(text(result, "knowledgeBase", 64)));
        scope(result, tenant, knowledgeBase);
        String id = text(result, "buildId", 64);
        require(id.matches("[A-Za-z0-9_-]{1,64}"));
        String status = text(result, "status", 16);
        require(STATUSES.contains(status));
        int documents = integer(result, "documentCount", 0, 10000);
        int processed = integer(result, "processedDocumentCount", 0, documents);
        int nodes = integer(result, "nodeCount", 0, Integer.MAX_VALUE);
        int edges = integer(result, "edgeCount", 0, Integer.MAX_VALUE);
        require(!"SUCCEEDED".equals(status) || processed == documents);
        String error = optionalText(result, "errorCode", 80);
        require(error == null || error.matches("[A-Z][A-Z0-9_]{0,79}"));
        return new GraphBuildVO(id, knowledgeBase, status, documents, processed, nodes, edges,
                error, timestamp(result, "createdAt"), timestamp(result, "updatedAt"));
    }

    static RagKnowledgeGraphVO graph(JsonNode result, String tenant, String knowledgeBase,
                                      KnowledgeGraphViewMode viewMode, int limit,
                                      List<ActiveGraphSource> activeSources) {
        require(result.isObject() && knowledgeBase.equals(text(result, "knowledgeBase", 64))
                && viewMode.name().equals(text(result, "viewMode", 16))
                && "neo4j".equals(text(result, "backend", 16)));
        scope(result, tenant, knowledgeBase);
        JsonNode nodeArray = result.path("nodes");
        JsonNode edgeArray = result.path("edges");
        require(nodeArray.isArray() && nodeArray.size() <= limit && edgeArray.isArray()
                && edgeArray.size() <= Math.min(limit * 4, 2000));
        require(integer(result, "nodeCount", 0, limit) == nodeArray.size()
                && integer(result, "edgeCount", 0, 2000) == edgeArray.size());
        require(result.path("truncated").isBoolean());
        Map<String, ActiveGraphSource> allowed = new HashMap<>();
        for (ActiveGraphSource source : activeSources) {
            require(allowed.put(source.pointId(), source) == null);
        }
        Set<String> ids = new HashSet<>();
        List<RagGraphNodeVO> nodes = new ArrayList<>();
        for (JsonNode node : nodeArray) {
            scope(node, tenant, knowledgeBase);
            String id = text(node, "id", 128);
            require(ids.add(id));
            String type = text(node, "type", 32);
            require(enumContains(RagGraphNodeType.class, type));
            Map<String, Object> properties = properties(node.path("properties"), tenant, knowledgeBase, allowed);
            String documentId = optionalText(node, "documentId", 64);
            String documentCode = optionalText(node, "documentCode", 64);
            Integer version = nullableInteger(node, "documentVersion");
            if (documentId != null || documentCode != null || version != null) {
                require(documentId != null && documentCode != null && version != null);
                @SuppressWarnings("unchecked")
                List<Map<String, Object>> sources = (List<Map<String, Object>>) properties.get("sources");
                require(sources.stream().anyMatch(source -> documentId.equals(source.get("documentId"))
                        && documentCode.equals(source.get("documentCode")) && version.equals(source.get("documentVersion"))));
            }
            nodes.add(new RagGraphNodeVO(id, type, text(node, "name", 255), text(node, "label", 255),
                    documentId, documentCode, version, optionalText(node, "sourceLocator", 1000), properties));
        }
        Set<String> edgeIds = new HashSet<>();
        List<RagGraphEdgeVO> edges = new ArrayList<>();
        for (JsonNode edge : edgeArray) {
            scope(edge, tenant, knowledgeBase);
            String id = text(edge, "id", 128);
            String source = text(edge, "source", 128);
            String target = text(edge, "target", 128);
            String type = text(edge, "type", 32);
            require(edgeIds.add(id) && ids.contains(source) && ids.contains(target)
                    && enumContains(RagGraphRelationType.class, type));
            edges.add(new RagGraphEdgeVO(id, source, target, type, text(edge, "label", 255),
                    optionalText(edge, "sourceLocator", 1000),
                    properties(edge.path("properties"), tenant, knowledgeBase, allowed)));
        }
        Map<String, Long> nodeCounts = nodes.stream().collect(Collectors.groupingBy(
                RagGraphNodeVO::type, LinkedHashMap::new, Collectors.counting()));
        Map<String, Long> edgeCounts = edges.stream().collect(Collectors.groupingBy(
                RagGraphEdgeVO::type, LinkedHashMap::new, Collectors.counting()));
        requireCounts(result.path("nodeTypeCounts"), nodeCounts);
        requireCounts(result.path("relationTypeCounts"), edgeCounts);
        JsonNode pending = result.path("build");
        GraphBuildVO build = pending.isNull() || pending.isMissingNode() ? null : build(pending, tenant, knowledgeBase);
        return new RagKnowledgeGraphVO(knowledgeBase, viewMode.name(), nodes.size(), edges.size(),
                result.path("truncated").asBoolean(), nodeCounts, edgeCounts, List.copyOf(nodes),
                List.copyOf(edges), "neo4j", build);
    }

    private static Map<String, Object> properties(JsonNode value, String tenant, String base,
                                                   Map<String, ActiveGraphSource> allowed) {
        require(value.isObject());
        scope(value, tenant, base);
        JsonNode sources = value.path("sources");
        require(sources.isArray() && !sources.isEmpty() && sources.size() <= 8);
        int count = integer(value, "sourceCount", sources.size(), allowed.size());
        require(value.path("sourceTruncated").isBoolean()
                && value.path("sourceTruncated").asBoolean() == (count > sources.size()));
        List<Map<String, Object>> evidence = new ArrayList<>();
        Set<String> ids = new HashSet<>();
        for (JsonNode source : sources) {
            require(source.isObject());
            scope(source, tenant, base);
            String pointId = text(source, "pointId", 128);
            ActiveGraphSource trusted = allowed.get(pointId);
            require(trusted != null && ids.add(pointId));
            require(trusted.documentId().equals(text(source, "documentId", 64))
                    && trusted.documentCode().equals(text(source, "documentCode", 64))
                    && trusted.documentVersion() == integer(source, "documentVersion", 1, Integer.MAX_VALUE)
                    && trusted.documentRevision() == longInteger(source, "documentRevision")
                    && trusted.chunkId().equals(text(source, "chunkId", 128))
                    && trusted.contentSha256().equals(text(source, "contentSha256", 64)));
            Map<String, Object> safe = new LinkedHashMap<>();
            safe.put("pointId", pointId);
            safe.put("documentId", trusted.documentId());
            safe.put("documentCode", trusted.documentCode());
            safe.put("documentVersion", trusted.documentVersion());
            safe.put("documentRevision", trusted.documentRevision());
            safe.put("chunkId", trusted.chunkId());
            safe.put("contentSha256", trusted.contentSha256());
            for (String field : List.of("sourceName", "sourceType", "sourceLocator", "chunkType", "sectionPath", "evidence")) {
                String text = optionalText(source, field, "evidence".equals(field) ? 6000 : 1000);
                if (text != null) safe.put(field, text);
            }
            JsonNode metadata = source.path("sourceMetadata");
            if (!metadata.isMissingNode() && !metadata.isNull()) safe.put("sourceMetadata", metadata(metadata));
            for (String field : List.of("evidenceTruncated", "evidenceNormalized")) {
                JsonNode flag = source.path(field);
                if (!flag.isMissingNode() && !flag.isNull()) {
                    require(flag.isBoolean());
                    safe.put(field, flag.asBoolean());
                }
            }
            evidence.add(Map.copyOf(safe));
        }
        Map<String, Object> safe = new LinkedHashMap<>();
        safe.put("sources", List.copyOf(evidence));
        safe.put("sourceCount", count);
        safe.put("sourceTruncated", count > sources.size());
        for (String field : List.of("entityType", "predicate", "field")) {
            String text = optionalText(value, field, 255);
            if (text != null) safe.put(field, text);
        }
        return Map.copyOf(safe);
    }

    private static Map<String, Object> metadata(JsonNode metadata) {
        require(metadata.isObject());
        Map<String, Object> safe = new LinkedHashMap<>();
        if (metadata.hasNonNull("page")) safe.put("page", integer(metadata, "page", 1, 10000));
        for (String field : List.of("block_type", "source_name")) {
            String text = optionalText(metadata, field, 1000);
            if (text != null) safe.put(field, text);
        }
        JsonNode bbox = metadata.path("bbox");
        if (!bbox.isNull() && !bbox.isMissingNode()) {
            require(bbox.isArray() && bbox.size() == 4);
            List<Double> coordinates = new ArrayList<>();
            for (JsonNode coordinate : bbox) {
                require(coordinate.isNumber() && Double.isFinite(coordinate.asDouble()) && coordinate.asDouble() >= 0);
                coordinates.add(coordinate.asDouble());
            }
            require(coordinates.get(2) >= coordinates.get(0) && coordinates.get(3) >= coordinates.get(1));
            safe.put("bbox", List.copyOf(coordinates));
        }
        return Map.copyOf(safe);
    }

    private static void scope(JsonNode value, String tenant, String base) {
        for (String field : List.of("tenantId", "tenant_id")) {
            if (value.has(field)) require(value.path(field).isTextual() && tenant.equals(value.path(field).asText()));
        }
        for (String field : List.of("knowledgeBase", "knowledge_base")) {
            if (value.has(field)) require(value.path(field).isTextual() && base.equals(value.path(field).asText()));
        }
        for (String field : List.of("documentStatus", "document_status")) {
            if (value.has(field)) require(value.path(field).isTextual() && "ACTIVE".equals(value.path(field).asText()));
        }
    }

    private static void requireCounts(JsonNode value, Map<String, Long> expected) {
        require(value.isObject() && value.size() == expected.size());
        expected.forEach((key, count) -> require(value.path(key).isIntegralNumber()
                && value.path(key).canConvertToLong() && value.path(key).asLong() == count));
    }

    private static Integer nullableInteger(JsonNode value, String key) {
        return value.path(key).isMissingNode() || value.path(key).isNull() ? null : integer(value, key, 1, Integer.MAX_VALUE);
    }

    private static int integer(JsonNode value, String key, int minimum, int maximum) {
        JsonNode number = value.path(key);
        require(number.isIntegralNumber() && number.canConvertToInt() && number.asInt() >= minimum && number.asInt() <= maximum);
        return number.asInt();
    }

    private static long longInteger(JsonNode value, String key) {
        JsonNode number = value.path(key);
        require(number.isIntegralNumber() && number.canConvertToLong() && number.asLong() > 0);
        return number.asLong();
    }

    private static String text(JsonNode value, String key, int maximum) {
        JsonNode text = value.path(key);
        require(text.isTextual() && !text.asText().isBlank()
                && text.asText().codePointCount(0, text.asText().length()) <= maximum);
        return text.asText();
    }

    private static String optionalText(JsonNode value, String key, int maximum) {
        JsonNode text = value.path(key);
        return text.isMissingNode() || text.isNull() ? null : text(value, key, maximum);
    }

    private static String timestamp(JsonNode value, String key) {
        String text = optionalText(value, key, 64);
        if (text != null) {
            try { Instant.parse(text); } catch (RuntimeException exception) { require(false); }
        }
        return text;
    }

    private static <E extends Enum<E>> boolean enumContains(Class<E> type, String value) {
        try { Enum.valueOf(type, value); return true; } catch (IllegalArgumentException exception) { return false; }
    }

    private static void require(boolean condition) {
        if (!condition) throw ExternalServiceException.httpFailure("RAG engine graph response", 502);
    }
}
