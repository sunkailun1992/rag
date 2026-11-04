package com.kellen.rag.service.impl;

import com.kellen.rag.client.EmbeddingClient;
import com.kellen.rag.client.RagEngineClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import com.kellen.rag.client.RerankClient;
import com.kellen.rag.client.VectorStoreClient;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.model.RerankResult;
import com.kellen.rag.model.VectorSearchHit;
import com.kellen.rag.service.RagRetrievalService;
import com.kellen.rpc.rag.RagRetrievalChunkRpcDTO;
import com.kellen.rpc.rag.RagRetrievalRpcDTO;
import com.kellen.rpc.rag.RagRetrievalRpcRequest;
import com.kellen.utils.context.TenantContextHolder;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 千问向量召回、Qdrant 租户过滤与 qwen3-rerank 重排实现。
 */
@Service
public class DefaultRagRetrievalService implements RagRetrievalService {

    private static final Logger log = LoggerFactory.getLogger(DefaultRagRetrievalService.class);
    private final RagEngineClient engineClient;
    private final RagVectorProperties properties;
    private final EmbeddingClient embeddingClient;
    private final VectorStoreClient vectorStoreClient;
    private final RerankClient rerankClient;

    public DefaultRagRetrievalService(RagVectorProperties properties,
                                      EmbeddingClient embeddingClient,
                                      VectorStoreClient vectorStoreClient,
                                      RerankClient rerankClient, RagEngineClient engineClient) {
        this.engineClient = engineClient;
        this.properties = properties;
        this.embeddingClient = embeddingClient;
        this.vectorStoreClient = vectorStoreClient;
        this.rerankClient = rerankClient;
    }

    @Override
    public RagRetrievalRpcDTO retrieve(RagRetrievalRpcRequest request) {
        RagRetrievalRpcDTO response = new RagRetrievalRpcDTO();
        response.setEnabled(properties.isEnabled());
        response.setMatched(false);
        response.setContext("");
        response.setSourceCount(0);
        response.setChunks(List.of());
        if (request == null || !StringUtils.hasText(request.getQuery())) {
            response.setMessage("query is blank");
            return response;
        }
        if (!properties.isEnabled()) {
            response.setMessage("RAG retrieval is disabled");
            return response;
        }
        String tenantId = TenantContextHolder.getTenantId();
        if (!StringUtils.hasText(tenantId)) {
            response.setMessage("tenant context is missing");
            return response;
        }
        String knowledgeBase = StringUtils.hasText(request.getCollectionName())
                ? request.getCollectionName().trim()
                : properties.getDefaultKnowledgeBase();
        if (!knowledgeBase.matches("[A-Za-z0-9_-]{1,64}")) {
            response.setMessage("knowledge base code is invalid");
            return response;
        }
        long started = System.nanoTime();
        String mode = engineClient.isRetrievalEnabled() ? "engine" : "java";
        try {
            int requestedTopK = request.getTopK() == null ? properties.getTopK() : request.getTopK();
            int topK = Math.min(Math.max(requestedTopK, 1), 20);
            int candidates = Math.max(topK, Math.min(properties.getCandidateCount(), 100));
            double threshold = request.getSimilarityThreshold() == null
                    ? properties.getSimilarityThreshold()
                    : Math.max(0D, request.getSimilarityThreshold());
            int contextLimit = request.getMaxContextCharacters() == null
                    ? properties.getMaxContextCharacters()
                    : Math.min(Math.max(request.getMaxContextCharacters(), 1000), 30000);
            List<VectorSearchHit> ordered;
            if (engineClient.isRetrievalEnabled()) {
                // 引擎失败不追加旧链路调用；避免叠加超时突破调用方的 5 秒预算。
                ordered = engineClient.retrieve(request.getQuery().trim(), knowledgeBase, topK,
                        candidates, threshold, contextLimit);
            } else {
                vectorStoreClient.ensureCollection();
                float[] queryVector = embeddingClient.embedQuery(request.getQuery().trim());
                List<VectorSearchHit> vectorHits = vectorStoreClient.search(queryVector, Map.of(
                        "tenant_id", tenantId, "knowledge_base", knowledgeBase, "document_status", "ACTIVE"
                ), candidates, threshold);
                ordered = vectorHits.isEmpty() ? List.of()
                        : rerank(request.getQuery().trim(), deduplicate(vectorHits), topK);
            }
            List<RagRetrievalChunkRpcDTO> chunks = ordered.stream()
                    .limit(topK)
                    .map(this::toChunk)
                    .toList();
            String context = buildContext(chunks, contextLimit);
            Set<String> sourceDocuments = chunks.stream()
                    .map(RagRetrievalChunkRpcDTO::getDocumentId)
                    .collect(Collectors.toSet());
            response.setMatched(!chunks.isEmpty());
            response.setChunks(chunks);
            response.setContext(context);
            response.setSourceCount(sourceDocuments.size());
            response.setMessage(chunks.isEmpty() ? "no knowledge matched" : "ok");
            log.info("rag_retrieval mode={} matched={} chunks={} elapsed_ms={}", mode,
                    response.isMatched(), chunks.size(), (System.nanoTime() - started) / 1_000_000);
            return response;
        } catch (RuntimeException exception) {
            log.warn("rag_retrieval mode={} outcome=unavailable error_type={} elapsed_ms={}", mode,
                    exception.getClass().getSimpleName(), (System.nanoTime() - started) / 1_000_000);
            response.setMessage("RAG retrieval is temporarily unavailable");
            return response;
        }
    }

    private List<VectorSearchHit> deduplicate(List<VectorSearchHit> hits) {
        Map<String, VectorSearchHit> unique = new LinkedHashMap<>();
        for (VectorSearchHit hit : hits) {
            String hash = text(hit.payload(), "content_sha256");
            VectorSearchHit current = unique.get(hash);
            if (current == null || hit.score() > current.score()) {
                unique.put(hash, hit);
            }
        }
        return new ArrayList<>(unique.values());
    }

    private List<VectorSearchHit> rerank(String query, List<VectorSearchHit> hits, int topK) {
        try {
            List<RerankResult> results = rerankClient.rerank(query,
                    hits.stream().map(hit -> text(hit.payload(), "content")).toList(), topK);
            return results.stream()
                    .filter(result -> result.index() >= 0 && result.index() < hits.size())
                    .map(result -> new VectorSearchHit(
                            hits.get(result.index()).id(), result.score(), hits.get(result.index()).payload()))
                    .toList();
        } catch (RuntimeException exception) {
            log.warn("rag_rerank outcome=vector_fallback error_type={}", exception.getClass().getSimpleName());
            return hits.stream().sorted(Comparator.comparingDouble(VectorSearchHit::score).reversed()).toList();
        }
    }

    private RagRetrievalChunkRpcDTO toChunk(VectorSearchHit hit) {
        RagRetrievalChunkRpcDTO chunk = new RagRetrievalChunkRpcDTO();
        chunk.setDocumentId(text(hit.payload(), "document_id"));
        chunk.setChunkId(text(hit.payload(), "chunk_id"));
        chunk.setTitle(text(hit.payload(), "document_title"));
        String source = text(hit.payload(), "source_name");
        String locator = text(hit.payload(), "source_locator");
        chunk.setSource(StringUtils.hasText(locator) ? source + " / " + locator : source);
        chunk.setContent(text(hit.payload(), "content"));
        chunk.setScore(hit.score());
        return chunk;
    }

    private String buildContext(List<RagRetrievalChunkRpcDTO> chunks, int limit) {
        StringBuilder context = new StringBuilder();
        for (RagRetrievalChunkRpcDTO chunk : chunks) {
            String block = "[来源: " + chunk.getSource() + "]\n" + chunk.getContent() + "\n\n";
            if (context.length() + block.length() > limit) {
                break;
            }
            context.append(block);
        }
        return context.toString().trim();
    }

    private String text(Map<String, Object> payload, String key) {
        Object value = payload.get(key);
        return value == null ? "" : String.valueOf(value);
    }
}
