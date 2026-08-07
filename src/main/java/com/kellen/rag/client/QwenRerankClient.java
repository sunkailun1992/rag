package com.kellen.rag.client;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.model.RerankResult;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 千问 qwen3-rerank OpenAI 兼容客户端。
 */
@Component
public class QwenRerankClient extends JsonHttpSupport implements RerankClient {

    private final RagVectorProperties properties;

    public QwenRerankClient(ObjectMapper objectMapper, RagVectorProperties properties) {
        super(objectMapper, "千问 Rerank");
        this.properties = properties;
    }

    @Override
    public List<RerankResult> rerank(String query, List<String> documents, int topN) {
        if (documents == null || documents.isEmpty()) {
            return List.of();
        }
        JsonNode response = post(rerankBaseUrl() + "/reranks", Map.of(
                "model", properties.getRerankModel(),
                "query", query,
                "documents", documents,
                "top_n", Math.min(Math.max(topN, 1), documents.size()),
                "instruct", "Retrieve passages that provide accurate domain knowledge relevant to the query."
        ), Map.of("Authorization", "Bearer " + properties.getQwenApiKey()), 60);
        List<RerankResult> results = new ArrayList<>();
        for (JsonNode item : response.path("results")) {
            results.add(new RerankResult(item.path("index").asInt(), item.path("relevance_score").asDouble()));
        }
        return results;
    }

    private String rerankBaseUrl() {
        return properties.getQwenBaseUrl().replaceAll("/+$", "")
                .replace("/compatible-mode/", "/compatible-api/");
    }
}
