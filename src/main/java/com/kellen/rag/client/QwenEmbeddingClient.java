package com.kellen.rag.client;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import com.kellen.rag.config.RagVectorProperties;
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

    @Override
    public float[] embedQuery(String query) {
        return embed(List.of(query)).get(0);
    }

    private List<float[]> embed(List<String> texts) {
        requireConfig();
        JsonNode response = post(baseUrl() + "/embeddings", Map.of(
                "model", properties.getEmbeddingModel(),
                "input", texts,
                "dimensions", properties.getEmbeddingDimension(),
                "encoding_format", "float"
        ), authHeaders(), 90);
        List<JsonNode> ordered = new ArrayList<>();
        response.path("data").forEach(ordered::add);
        ordered.sort(Comparator.comparingInt(node -> node.path("index").asInt()));
        if (ordered.size() != texts.size()) {
            throw new IllegalStateException("Embedding 返回数量与输入不一致");
        }
        List<float[]> vectors = new ArrayList<>(ordered.size());
        for (JsonNode item : ordered) {
            JsonNode embedding = item.path("embedding");
            if (!embedding.isArray() || embedding.size() != properties.getEmbeddingDimension()) {
                throw new IllegalStateException("Embedding 向量维度不一致");
            }
            float[] vector = new float[embedding.size()];
            for (int index = 0; index < embedding.size(); index++) {
                vector[index] = (float) embedding.get(index).asDouble();
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
