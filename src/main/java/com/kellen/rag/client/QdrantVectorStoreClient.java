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
            put(collectionUrl(), Map.of(
                    "vectors", Map.of(
                            "size", properties.getEmbeddingDimension(),
                            "distance", "Cosine"
                    ),
                    "on_disk_payload", true
            ), Map.of(), 60);
        } else if (collectionStatus < 200 || collectionStatus >= 300) {
            throw ExternalServiceException.httpFailure("Qdrant", collectionStatus);
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
            value.put("vector", point.vector());
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
            JsonNode first = vector.values().stream().findFirst().orElse(null);
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
