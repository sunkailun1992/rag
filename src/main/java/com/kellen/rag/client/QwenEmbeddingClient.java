package com.kellen.rag.client;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.exception.ExternalServiceException;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;

/**
 * 千问 text-embedding-v4 OpenAI 兼容客户端。
 */
@Component
public class QwenEmbeddingClient extends JsonHttpSupport implements EmbeddingClient {

    private final RagVectorProperties properties;

    public QwenEmbeddingClient(ObjectMapper objectMapper, RagVectorProperties properties) {
        super(objectMapper, "千问 Embedding");
        this.properties = properties;
    }

    @Override
    public List<float[]> embedDocuments(List<String> texts) {
        if (texts == null || texts.isEmpty()) {
            return List.of();
        }
        List<float[]> vectors = new ArrayList<>(texts.size());
        int batchSize = Math.min(Math.max(properties.getEmbeddingBatchSize(), 1), 10);
        for (int start = 0; start < texts.size(); start += batchSize) {
            int end = Math.min(texts.size(), start + batchSize);
            vectors.addAll(embed(texts.subList(start, end)));
        }
        return vectors;
    }

    private List<float[]> embed(List<String> texts) {
        requireConfig();
        JsonNode response;
        try {
            response = post(baseUrl() + "/embeddings", Map.of(
                    "model", properties.getEmbeddingModel(),
                    "input", texts,
                    "dimensions", properties.getEmbeddingDimension(),
                    "encoding_format", "float"
            ), authHeaders(), 90);
        } catch (ExternalServiceException exception) {
            if (exception.getStatusCode() != null) throw exception;
            // HTTP/JSON 库的 cause 可能包含地址或正文，不能带出模型边界。
            throw ExternalServiceException.connectionFailure("千问 Embedding", null);
        } catch (RuntimeException exception) {
            throw ExternalServiceException.connectionFailure("千问 Embedding", null);
        }
        JsonNode data = response.path("data");
        if (!data.isArray() || data.size() != texts.size()) {
            throw new IllegalStateException("Embedding 返回数量与输入不一致");
        }
        List<JsonNode> ordered = new ArrayList<>();
        boolean[] seen = new boolean[texts.size()];
        for (JsonNode item : data) {
            JsonNode index = item.path("index");
            if (!index.isIntegralNumber() || !index.canConvertToInt()) {
                throw new IllegalStateException("Embedding 返回索引不完整或不合法");
            }
            int position = index.asInt();
            if (position < 0 || position >= texts.size() || seen[position]) {
                throw new IllegalStateException("Embedding 返回索引不完整或不合法");
            }
            seen[position] = true;
            ordered.add(item);
        }
        ordered.sort(Comparator.comparingInt(node -> node.path("index").asInt()));
        List<float[]> vectors = new ArrayList<>(ordered.size());
        for (JsonNode item : ordered) {
            JsonNode embedding = item.path("embedding");
            if (!embedding.isArray() || embedding.size() != properties.getEmbeddingDimension()) {
                throw new IllegalStateException("Embedding 向量维度不一致");
            }
            float[] vector = new float[embedding.size()];
            for (int index = 0; index < embedding.size(); index++) {
                JsonNode value = embedding.get(index);
                if (!value.isNumber()) {
                    throw new IllegalStateException("Embedding 向量数值不合法");
                }
                double number = value.asDouble();
                float converted = (float) number;
                if (!Double.isFinite(number) || !Float.isFinite(converted)) {
                    throw new IllegalStateException("Embedding 向量数值不合法");
                }
                vector[index] = converted;
            }
            vectors.add(vector);
        }
        return vectors;
    }

    private void requireConfig() {
        if (!StringUtils.hasText(properties.getQwenBaseUrl()) || !StringUtils.hasText(properties.getQwenApiKey())) {
            throw new IllegalStateException("Embedding 模型配置不完整");
        }
    }

    private String baseUrl() {
        return properties.getQwenBaseUrl().replaceAll("/+$", "");
    }

    private Map<String, String> authHeaders() {
        return Map.of("Authorization", "Bearer " + properties.getQwenApiKey());
    }
}
