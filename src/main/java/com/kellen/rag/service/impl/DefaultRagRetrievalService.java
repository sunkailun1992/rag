package com.kellen.rag.service.impl;

import com.kellen.rag.client.RagEngineClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.model.VectorSearchHit;
import com.kellen.rag.service.RagRetrievalService;
import com.kellen.rpc.rag.RagRetrievalChunkRpcDTO;
import com.kellen.rpc.rag.RagRetrievalRpcDTO;
import com.kellen.rpc.rag.RagRetrievalRpcRequest;
import com.kellen.utils.context.TenantContextHolder;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * Python Engine 检索门面；保留租户前置校验、RPC 组装与受限失败降级。
 */
@Service
public class DefaultRagRetrievalService implements RagRetrievalService {

    private static final Logger log = LoggerFactory.getLogger(DefaultRagRetrievalService.class);
    private final RagEngineClient engineClient;
    private final RagVectorProperties properties;
    public DefaultRagRetrievalService(RagVectorProperties properties, RagEngineClient engineClient) {
        this.engineClient = engineClient;
        this.properties = properties;
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
        String mode = "engine";
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
            // 唯一检索实现由引擎提供；失败直接返回兼容降级，不能叠加另一条超时链。
            List<VectorSearchHit> ordered = engineClient.retrieve(request.getQuery().trim(), knowledgeBase, topK,
                    candidates, threshold, contextLimit);
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
