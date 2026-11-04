package com.kellen.rag.config;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * RAG 向量存储与模型配置。
 */
@Getter
@Setter
@Component
@ConfigurationProperties(prefix = "rag.vector")
public class RagVectorProperties {

    private boolean enabled = true;
    /** schema 已准备后启用，摄取时原子写 dense+sparse，停用/换版共享同一 payload 状态。 */
    private boolean hybridWriteEnabled = false;
    private String qdrantUrl;
    private String collectionName = "knowledge-vectors";
    private String defaultKnowledgeBase = "general-knowledge";
    private String qwenBaseUrl;
    private String qwenApiKey;
    private String embeddingModel = "text-embedding-v4";
    private int embeddingDimension = 1024;
    private String rerankModel = "qwen3-rerank";
    private int embeddingBatchSize = 10;
    private int vectorBatchSize = 64;
    private int candidateCount = 30;
    private int topK = 6;
    private double similarityThreshold = 0.2D;
    private int maxContextCharacters = 12000;
    private long maxFileBytes = 20L * 1024 * 1024;
    private long maxUncompressedBytes = 50L * 1024 * 1024;
    private int maxRows = 10000;
    private int maxChunkCharacters = 6000;
}
