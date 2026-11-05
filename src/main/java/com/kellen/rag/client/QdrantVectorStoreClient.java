package com.kellen.rag.client;

import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.exception.ExternalServiceException;
import com.kellen.rag.model.StoredVectorPoint;
import com.kellen.rag.model.VectorPoint;
import com.kellen.rag.model.VectorSearchHit;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.HashSet;
import java.util.regex.Pattern;

/**
 * Qdrant REST 向量存储客户端。
 */
@Component
public class QdrantVectorStoreClient extends JsonHttpSupport implements VectorStoreClient {

    private static final Pattern SAFE_COLLECTION = Pattern.compile("[A-Za-z0-9_-]{1,128}");
    private static final List<String> KEYWORD_INDEX_FIELDS = List.of(
            "tenant_id", "knowledge_base", "document_id", "document_code", "document_status",
            "file_sha256", "content_sha256"
    );

    private final RagVectorProperties properties;

    public QdrantVectorStoreClient(ObjectMapper objectMapper, RagVectorProperties properties) {
        super(objectMapper, "Qdrant");
        this.properties = properties;
    }

    @Override
    public boolean collectionExists() {
        requireConfig();
        int collectionStatus = status(collectionUrl(), Map.of());
        if (collectionStatus == 404) {
            int apiStatus = status(collectionsUrl(), Map.of());
            if (apiStatus >= 200 && apiStatus < 300) {
                return false;
            }
            throw ExternalServiceException.httpFailure("Qdrant API", apiStatus);
        }
        if (collectionStatus < 200 || collectionStatus >= 300) {
            throw ExternalServiceException.httpFailure("Qdrant", collectionStatus);
        }
        return true;
    }

    @Override
    public void ensureCollection() {
        requireConfig();
        int collectionStatus = status(collectionUrl(), Map.of());
        if (collectionStatus == 404) {
            Map<String, Object> schema = new LinkedHashMap<>(Map.of(
                    "vectors", Map.of(
                            "size", properties.getEmbeddingDimension(),
                            "distance", "Cosine"
                    ),
                    "on_disk_payload", true
            ));
            if (properties.isHybridWriteEnabled()) schema.put("sparse_vectors",
                    Map.of(LexicalSparseVector.MODEL, Map.of("modifier", "idf")));
            put(collectionUrl(), schema, Map.of(), 60);
        } else if (collectionStatus < 200 || collectionStatus >= 300) {
            throw ExternalServiceException.httpFailure("Qdrant", collectionStatus);
        }
        if (properties.isHybridWriteEnabled() && !get(collectionUrl(), Map.of()).path("result")
                .path("config").path("params").path("sparse_vectors").path(LexicalSparseVector.MODEL)
                .path("modifier").asText().equals("idf")) {
            throw new IllegalStateException("混合索引尚未准备，请先执行显式 schema/回填迁移");
        }
        for (String field : KEYWORD_INDEX_FIELDS) {
            put(collectionUrl() + "/index?wait=true", Map.of(
                    "field_name", field,
                    "field_schema", "keyword"
            ), Map.of(), 60);
        }
    }

    @Override
    public void upsert(List<VectorPoint> points) {
        if (points == null || points.isEmpty()) {
            return;
        }
        List<Map<String, Object>> bodyPoints = points.stream().map(point -> {
            Map<String, Object> value = new LinkedHashMap<>();
            value.put("id", point.id());
            value.put("vector", properties.isHybridWriteEnabled()
                    ? Map.of("", point.vector(), LexicalSparseVector.MODEL,
                            LexicalSparseVector.encode(String.valueOf(point.payload().get("content")))) : point.vector());
            value.put("payload", point.payload());
            return value;
        }).toList();
        put(collectionUrl() + "/points?wait=true", Map.of("points", bodyPoints), Map.of(), 120);
    }

    @Override
    public List<StoredVectorPoint> scroll(Map<String, Object> exactFilter, int limit, boolean withVector) {
        int boundedLimit = Math.min(Math.max(limit, 1), 10000);
        List<StoredVectorPoint> points = new ArrayList<>();
        JsonNode offset = null;
        while (points.size() < boundedLimit) {
            Map<String, Object> request = new LinkedHashMap<>();
            request.put("filter", filter(exactFilter));
            request.put("limit", Math.min(256, boundedLimit - points.size()));
            request.put("with_payload", true);
            request.put("with_vector", withVector);
            if (offset != null && !offset.isNull()) {
                request.put("offset", offset);
            }
            JsonNode result = post(collectionUrl() + "/points/scroll", request, Map.of(), 60).path("result");
            for (JsonNode point : result.path("points")) {
                points.add(new StoredVectorPoint(
                        point.path("id").asText(),
                        objectMapper.convertValue(point.path("payload"), new TypeReference<>() { }),
                        vectorDimension(point.path("vector"))
                ));
            }
            offset = result.get("next_page_offset");
            if (offset == null || offset.isNull() || result.path("points").isEmpty()) {
                break;
            }
        }
        return points;
    }

    /** 仅从服务端下载明确选择的身份字段，完整分页；不改变旧全文 scroll 契约。 */
    @Override
    public List<StoredVectorPoint> scrollPayload(Map<String, Object> exactFilter, int limit,
                                                 Set<String> includeFields) {
        if (includeFields == null || includeFields.isEmpty() || includeFields.size() > 32
                || includeFields.stream().anyMatch(field -> field == null || !field.matches("[a-z][a-z0-9_]{0,63}"))) {
            throw new IllegalArgumentException("payload 投影字段不合法");
        }
        List<String> fields = includeFields.stream().sorted().toList();
        int boundedLimit = Math.min(Math.max(limit, 1), 10000);
        List<StoredVectorPoint> points = new ArrayList<>();
        Set<String> seenOffsets = new HashSet<>();
        JsonNode offset = null;
        while (points.size() < boundedLimit) {
            int pageLimit = Math.min(256, boundedLimit - points.size());
            Map<String, Object> request = new LinkedHashMap<>();
            request.put("filter", filter(exactFilter));
            request.put("limit", pageLimit);
            request.put("with_payload", Map.of("include", fields));
            request.put("with_vector", false);
            if (offset != null) request.put("offset", offset);
            JsonNode result = post(collectionUrl() + "/points/scroll", request, Map.of(), 60).path("result");
            JsonNode values = result.path("points");
            if (!values.isArray() || values.size() > pageLimit || !result.has("next_page_offset")) {
                throw ExternalServiceException.httpFailure("Qdrant projection response", 502);
            }
            for (JsonNode point : values) {
                JsonNode payload = point.path("payload");
                JsonNode id = point.path("id");
                if (!payload.isObject() || !(id.isTextual() || id.isIntegralNumber())) {
                    throw ExternalServiceException.httpFailure("Qdrant projection response", 502);
                }
                Map<String, Object> selected = new LinkedHashMap<>();
                for (String field : fields) {
                    if (payload.has(field)) selected.put(field, objectMapper.convertValue(payload.get(field), Object.class));
                }
                points.add(new StoredVectorPoint(id.asText(), selected, 0));
            }
            offset = result.path("next_page_offset");
            if (offset.isNull()) return points;
            // 截断、空页续游标或循环游标均不构成完整快照，不能交给下游发布或清理。
            if (values.isEmpty() || points.size() >= boundedLimit
                    || !(offset.isTextual() || offset.isIntegralNumber()) || !seenOffsets.add(offset.toString())) {
                throw ExternalServiceException.httpFailure("Qdrant projection pagination", 502);
            }
        }
        return points;
    }

    @Override
    public List<VectorSearchHit> search(float[] vector, Map<String, Object> exactFilter,
                                        int limit, double scoreThreshold) {
        JsonNode response = post(collectionUrl() + "/points/search", Map.of(
                "vector", vector,
                "filter", filter(exactFilter),
                "limit", Math.min(Math.max(limit, 1), 100),
                "score_threshold", Math.max(0D, scoreThreshold),
                "with_payload", true,
                "with_vector", false
        ), Map.of(), 60);
        List<VectorSearchHit> hits = new ArrayList<>();
        for (JsonNode item : response.path("result")) {
            hits.add(new VectorSearchHit(
                    item.path("id").asText(),
                    item.path("score").asDouble(),
                    objectMapper.convertValue(item.path("payload"), new TypeReference<>() { })
            ));
        }
        return hits;
    }

    @Override
    public com.kellen.rag.model.VectorPointPage scrollPage(Map<String, Object> exactFilter,
                                                          int limit, String offset) {
        Map<String, Object> request = new LinkedHashMap<>();
        request.put("filter", filter(exactFilter));
        request.put("limit", limit);
        request.put("with_payload", true);
        request.put("with_vector", false);
        if (offset != null) request.put("offset", offset);
        JsonNode result = post(collectionUrl() + "/points/scroll", request, Map.of(), 30).path("result");
        if (!result.path("points").isArray() || result.path("points").size() > limit) {
            throw new IllegalStateException("切片上游响应超过分页范围");
        }
        List<StoredVectorPoint> points = new ArrayList<>();
        for (JsonNode point : result.path("points")) {
            points.add(new StoredVectorPoint(point.path("id").asText(),
                    objectMapper.convertValue(point.path("payload"), new TypeReference<>() { }), 0));
        }
        JsonNode next = result.path("next_page_offset");
        if (offset != null && offset.equals(next.asText())) {
            throw new IllegalStateException("切片上游游标未前进");
        }
        return new com.kellen.rag.model.VectorPointPage(List.copyOf(points),
                next.isMissingNode() || next.isNull() ? null : next.asText());
    }

    @Override
    public void setPayload(Map<String, Object> exactFilter, Map<String, Object> payload) {
        post(collectionUrl() + "/points/payload?wait=true", Map.of(
                "payload", payload,
                "filter", filter(exactFilter)
        ), Map.of(), 60);
    }

    @Override
    public long count(Map<String, Object> exactFilter) {
        return post(collectionUrl() + "/points/count", Map.of(
                "filter", filter(exactFilter),
                "exact", true
        ), Map.of(), 60).path("result").path("count").asLong();
    }

    private Map<String, Object> filter(Map<String, Object> exactFilter) {
        List<Map<String, Object>> must = exactFilter.entrySet().stream()
                .filter(entry -> entry.getValue() != null)
                .map(entry -> Map.<String, Object>of(
                        "key", entry.getKey(),
                        "match", Map.of("value", entry.getValue())
                ))
                .toList();
        return Map.of("must", must);
    }

    private int vectorDimension(JsonNode vector) {
        if (vector.isArray()) {
            return vector.size();
        }
        if (vector.isObject()) {
            JsonNode first = vector.get("");
            return first != null && first.isArray() ? first.size() : 0;
        }
        return 0;
    }

    private String collectionUrl() {
        return collectionsUrl() + "/" + properties.getCollectionName();
    }

    private String collectionsUrl() {
        requireConfig();
        return properties.getQdrantUrl().replaceAll("/+$", "") + "/collections";
    }

    private void requireConfig() {
        if (!StringUtils.hasText(properties.getQdrantUrl())) {
            throw new IllegalStateException("Qdrant 地址未配置");
        }
        if (!SAFE_COLLECTION.matcher(properties.getCollectionName()).matches()) {
            throw new IllegalStateException("Qdrant collection 名称不合法");
        }
    }
}
