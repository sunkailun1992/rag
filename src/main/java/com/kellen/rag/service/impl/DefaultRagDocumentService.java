package com.kellen.rag.service.impl;

import com.kellen.rag.client.EmbeddingClient;
import com.kellen.rag.client.VectorStoreClient;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.entity.vo.RagDocumentIngestionVO;
import com.kellen.rag.entity.vo.RagDocumentVO;
import com.kellen.rag.entity.vo.RagDocumentValidationVO;
import com.kellen.rag.model.KnowledgeChunkDraft;
import com.kellen.rag.model.ParsedKnowledgeDocument;
import com.kellen.rag.model.StoredVectorPoint;
import com.kellen.rag.model.VectorPoint;
import com.kellen.rag.parser.KnowledgeDocumentParser;
import com.kellen.rag.service.RagDocumentService;
import com.kellen.rag.service.RagKnowledgeGraphService;
import com.kellen.utils.context.TenantContextHolder;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;
import org.springframework.web.multipart.MultipartFile;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.Normalizer;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Pattern;

/**
 * 基于 Qdrant payload 的文档摄取、幂等、版本与读回校验实现。
 */
@Service
public class DefaultRagDocumentService implements RagDocumentService {

    private static final Pattern SAFE_CODE = Pattern.compile("[A-Za-z0-9_-]{1,64}");
    private static final String STATUS_STAGING = "STAGING";
    private static final String STATUS_ACTIVE = "ACTIVE";
    private static final String STATUS_SUPERSEDED = "SUPERSEDED";
    private static final String STATUS_FAILED = "FAILED";
    private static final String STATUS_DELETED = "DELETED";

    private final RagVectorProperties properties;
    private final KnowledgeDocumentParser parser;
    private final EmbeddingClient embeddingClient;
    private final VectorStoreClient vectorStoreClient;
    private final RagKnowledgeGraphService graphService;

    public DefaultRagDocumentService(RagVectorProperties properties,
                                     KnowledgeDocumentParser parser,
                                     EmbeddingClient embeddingClient,
                                     VectorStoreClient vectorStoreClient,
                                     RagKnowledgeGraphService graphService) {
        this.properties = properties;
        this.parser = parser;
        this.embeddingClient = embeddingClient;
        this.vectorStoreClient = vectorStoreClient;
        this.graphService = graphService;
    }

    @Override
    public RagDocumentIngestionVO ingest(MultipartFile file, String knowledgeBase,
                                         String documentCode, String title) {
        String tenantId = requireTenant();
        String base = safeCode(knowledgeBase, properties.getDefaultKnowledgeBase(), "知识库编码");
        try {
            byte[] bytes = file.getBytes();
            String fileHash = sha256(bytes);
            vectorStoreClient.ensureCollection();
            List<StoredVectorPoint> sameFiles = vectorStoreClient.scroll(filter(
                    tenantId, base, "file_sha256", fileHash), 10000, false);
            StoredVectorPoint activeDuplicate = sameFiles.stream()
                    .filter(point -> STATUS_ACTIVE.equals(text(point.payload(), "document_status")))
                    .findFirst().orElse(null);
            if (activeDuplicate != null) {
                String documentId = text(activeDuplicate.payload(), "document_id");
                return withGraphResult(toDocument(activeDuplicate), true, validate(documentId));
            }

            ParsedKnowledgeDocument parsed = parser.parse(file.getOriginalFilename(), title, bytes);
            String code = safeCode(documentCode, generatedCode(parsed.title()), "文档编码");
            List<StoredVectorPoint> sameCode = vectorStoreClient.scroll(filter(
                    tenantId, base, "document_code", code), 10000, false);
            int documentVersion = sameCode.stream()
                    .mapToInt(point -> number(point.payload(), "document_version"))
                    .max().orElse(0) + 1;
            long revision = System.currentTimeMillis();
            String documentId = deterministicId(tenantId + "|" + base + "|" + fileHash);

            LinkedHashMap<String, ChunkAccumulator> uniqueChunks = new LinkedHashMap<>();
            for (KnowledgeChunkDraft chunk : parsed.chunks()) {
                String contentHash = sha256(normalizeForHash(chunk.content()).getBytes(StandardCharsets.UTF_8));
                ChunkAccumulator accumulator = uniqueChunks.computeIfAbsent(contentHash,
                        ignored -> new ChunkAccumulator(chunk));
                accumulator.locators().add(chunk.locator());
                accumulator.metadata().add(chunk.metadata());
            }
            int duplicateCount = parsed.chunks().size() - uniqueChunks.size();
            List<Map.Entry<String, ChunkAccumulator>> entries = new ArrayList<>(uniqueChunks.entrySet());
            List<float[]> vectors = embeddingClient.embedDocuments(entries.stream()
                    .map(entry -> entry.getValue().chunk().content()).toList());
            if (vectors.size() != entries.size()) {
                throw new IllegalStateException("向量数量与切片数量不一致");
            }

            String ingestedAt = Instant.now().toString();
            List<VectorPoint> points = new ArrayList<>(entries.size());
            for (int index = 0; index < entries.size(); index++) {
                Map.Entry<String, ChunkAccumulator> entry = entries.get(index);
                KnowledgeChunkDraft chunk = entry.getValue().chunk();
                Map<String, Object> payload = new LinkedHashMap<>();
                payload.put("tenant_id", tenantId);
                payload.put("knowledge_base", base);
                payload.put("document_id", documentId);
                payload.put("document_code", code);
                payload.put("document_version", documentVersion);
                payload.put("document_revision", revision);
                payload.put("document_status", STATUS_STAGING);
                payload.put("document_title", parsed.title());
                payload.put("source_name", parsed.sourceName());
                payload.put("source_type", parsed.sourceType());
                payload.put("file_sha256", fileHash);
                payload.put("chunk_id", deterministicId(documentId + "|" + entry.getKey()));
                payload.put("chunk_index", index);
                payload.put("chunk_type", chunk.chunkType());
                payload.put("section_path", chunk.sectionPath());
                payload.put("source_locator", chunk.locator());
                // 独立命名空间保存解析器元数据，不能覆盖 tenant_id、状态或版本。
                payload.put("source_metadata", chunk.metadata());
                payload.put("duplicate_source_metadata", entry.getValue().metadata());
                payload.put("duplicate_locators", entry.getValue().locators());
                payload.put("duplicate_occurrence_count", entry.getValue().locators().size());
                payload.put("content", chunk.content());
                payload.put("content_sha256", entry.getKey());
                payload.put("embedding_model", properties.getEmbeddingModel());
                payload.put("embedding_dimension", properties.getEmbeddingDimension());
                if (properties.isHybridWriteEnabled()) {
                    payload.put("sparse_model", com.kellen.rag.client.LexicalSparseVector.MODEL);
                }
                payload.put("document_chunk_count", entries.size());
                payload.put("duplicate_chunk_count", duplicateCount);
                payload.put("ingested_at", ingestedAt);
                points.add(new VectorPoint(text(payload, "chunk_id"), vectors.get(index), payload));
            }

            try {
                int batchSize = Math.max(1, properties.getVectorBatchSize());
                for (int start = 0; start < points.size(); start += batchSize) {
                    vectorStoreClient.upsert(points.subList(start, Math.min(points.size(), start + batchSize)));
                }
                RagDocumentValidationVO stagingValidation = validateInternal(tenantId, documentId);
                if (!stagingValidation.valid()) {
                    vectorStoreClient.setPayload(filter(tenantId, null, "document_id", documentId),
                            Map.of("document_status", STATUS_FAILED));
                    throw new IllegalStateException("向量写入后的读回校验失败");
                }
                vectorStoreClient.setPayload(filter(tenantId, null, "document_id", documentId),
                        Map.of("document_status", STATUS_ACTIVE));
                reconcileVersions(tenantId, base, code);
                StoredVectorPoint stored = vectorStoreClient.scroll(filter(
                        tenantId, base, "document_id", documentId), 1, false).get(0);
                RagDocumentValidationVO validation = validateInternal(tenantId, documentId);
                return withGraphResult(toDocument(stored), false, validation);
            } catch (RuntimeException exception) {
                try {
                    vectorStoreClient.setPayload(filter(tenantId, null, "document_id", documentId),
                            Map.of("document_status", STATUS_FAILED));
                } catch (RuntimeException ignored) {
                    // 原异常优先；远端不可用时无法补写 FAILED，后续可按 STAGING 状态巡检清理。
                }
                try {
                    graphService.deactivateDocument(documentId);
                } catch (RuntimeException ignored) {
                    // 图谱是派生索引；失败补偿不覆盖原异常，后续可从活动向量执行全量同步修复。
                }
                throw exception;
            }
        } catch (RuntimeException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new IllegalStateException("文档摄取失败", exception);
        }
    }

    @Override
    public List<RagDocumentVO> list(String knowledgeBase) {
        String tenantId = requireTenant();
        if (!vectorStoreClient.collectionExists()) {
            return List.of();
        }
        Map<String, Object> filters = new LinkedHashMap<>();
        filters.put("tenant_id", tenantId);
        if (StringUtils.hasText(knowledgeBase)) {
            filters.put("knowledge_base", safeCode(knowledgeBase, null, "知识库编码"));
        }
        Map<String, StoredVectorPoint> documents = new LinkedHashMap<>();
        for (StoredVectorPoint point : vectorStoreClient.scroll(filters, 10000, false)) {
            documents.putIfAbsent(text(point.payload(), "document_id"), point);
        }
        return documents.values().stream()
                .map(this::toDocument)
                .sorted(Comparator.comparing(RagDocumentVO::ingestedAt,
                        Comparator.nullsLast(Comparator.reverseOrder())))
                .toList();
    }

    @Override
    public RagDocumentValidationVO validate(String documentId) {
        return validateInternal(requireTenant(), requiredId(documentId));
    }

    @Override
    public com.kellen.rag.entity.vo.RagChunkPageVO chunks(String documentId, int limit, String cursor) {
        String tenant = requireTenant();
        String id = requiredId(documentId);
        if (limit < 1 || limit > 100) throw new IllegalArgumentException("切片每页数量必须在1到100之间");
        String offset = null;
        if (StringUtils.hasText(cursor)) {
            try {
                if (cursor.length() > 256) throw new IllegalArgumentException();
                String decoded = new String(java.util.Base64.getUrlDecoder().decode(cursor), StandardCharsets.UTF_8);
                String[] parts = decoded.split("\\n", -1);
                if (parts.length != 2 || !id.equals(parts[0])) throw new IllegalArgumentException();
                offset = UUID.fromString(parts[1]).toString();
            } catch (IllegalArgumentException exception) {
                throw new IllegalArgumentException("切片游标不合法");
            }
        }
        if (!vectorStoreClient.collectionExists()) {
            return new com.kellen.rag.entity.vo.RagChunkPageVO(id, List.of(), null);
        }
        // 每页都重新施加可信租户及文档过滤；游标不能改变可见范围。
        var page = vectorStoreClient.scrollPage(filter(tenant, null, "document_id", id), limit, offset);
        var chunks = page.points().stream().map(point -> {
            Map<String, Object> payload = point.payload();
            if (!tenant.equals(text(payload, "tenant_id")) || !id.equals(text(payload, "document_id"))) {
                throw new IllegalStateException("切片读回范围不匹配");
            }
            Map<String, Object> metadata = new LinkedHashMap<>();
            if (payload.get("source_metadata") instanceof Map<?, ?> source) {
                for (String key : List.of("page", "block_type", "bbox", "ocr_capabilities")) {
                    if (source.get(key) != null) metadata.put(key, source.get(key));
                }
            }
            List<String> duplicates = payload.get("duplicate_locators") instanceof List<?> locators
                    ? locators.stream().filter(String.class::isInstance).map(String.class::cast).toList() : List.of();
            return new com.kellen.rag.entity.vo.RagChunkPageVO.Chunk(text(payload, "chunk_id"),
                    number(payload, "chunk_index"), text(payload, "chunk_type"), text(payload, "section_path"),
                    text(payload, "source_name"), text(payload, "source_locator"), text(payload, "content"),
                    Map.copyOf(metadata), duplicates);
        }).toList();
        String next = page.nextOffset() == null ? null : java.util.Base64.getUrlEncoder().withoutPadding()
                .encodeToString((id + "\n" + UUID.fromString(page.nextOffset())).getBytes(StandardCharsets.UTF_8));
        return new com.kellen.rag.entity.vo.RagChunkPageVO(id, chunks, next);
    }

    @Override
    public boolean deactivate(String documentId) {
        String tenantId = requireTenant();
        String id = requiredId(documentId);
        long count = vectorStoreClient.count(filter(tenantId, null, "document_id", id));
        if (count == 0) {
            return false;
        }
        var fence = graphService.deactivateDocumentWithFence(id);
        try {
            vectorStoreClient.setPayload(filter(tenantId, null, "document_id", id),
                    Map.of("document_status", STATUS_DELETED));
        } catch (RuntimeException exception) {
            try {
                graphService.restoreDocument(id, fence); // 仅本次屏障票据可恢复，迟到补偿不能清除新删除。
            } catch (RuntimeException ignored) {
                // 原异常优先；即使恢复失败也只会暂时少展示图谱，不会暴露已停用文档。
            }
            throw exception;
        }
        return true;
    }

    private RagDocumentValidationVO validateInternal(String tenantId, String documentId) {
        List<StoredVectorPoint> points = vectorStoreClient.scroll(
                filter(tenantId, null, "document_id", documentId), 10000, true);
        if (points.isEmpty()) {
            return new RagDocumentValidationVO(false, 0, 0, 0, 0, 0);
        }
        int expected = number(points.get(0).payload(), "document_chunk_count");
        int invalidHash = 0;
        int invalidDimension = 0;
        int valid = 0;
        for (StoredVectorPoint point : points) {
            String actualHash = sha256(normalizeForHash(text(point.payload(), "content"))
                    .getBytes(StandardCharsets.UTF_8));
            boolean hashValid = actualHash.equals(text(point.payload(), "content_sha256"));
            boolean dimensionValid = point.vectorDimension() == properties.getEmbeddingDimension();
            if (!hashValid) {
                invalidHash++;
            }
            if (!dimensionValid) {
                invalidDimension++;
            }
            if (hashValid && dimensionValid) {
                valid++;
            }
        }
        boolean allValid = points.size() == expected && invalidHash == 0 && invalidDimension == 0;
        return new RagDocumentValidationVO(allValid, expected, points.size(), valid, invalidHash, invalidDimension);
    }

    /** 图是派生索引，任务接收或失败都不能撤销已经验证并激活的向量。 */
    private RagDocumentIngestionVO withGraphResult(RagDocumentVO document, boolean idempotent,
                                                   RagDocumentValidationVO validation) {
        try {
            var graph = graphService.syncDocument(document.documentId());
            String status = graph == null || graph.status() == null ? "SUCCEEDED" : graph.status();
            return new RagDocumentIngestionVO(document, idempotent, validation, graph, status);
        } catch (RuntimeException exception) {
            // 不传播外部异常正文；前端可显式重试图谱同步，向量检索继续可用。
            return new RagDocumentIngestionVO(document, idempotent, validation, null, "FAILED");
        }
    }

    private void reconcileVersions(String tenantId, String knowledgeBase, String documentCode) {
        List<StoredVectorPoint> points = vectorStoreClient.scroll(filter(
                tenantId, knowledgeBase, "document_code", documentCode), 10000, false);
        Map<String, StoredVectorPoint> activeDocuments = new LinkedHashMap<>();
        for (StoredVectorPoint point : points) {
            if (STATUS_ACTIVE.equals(text(point.payload(), "document_status"))) {
                activeDocuments.putIfAbsent(text(point.payload(), "document_id"), point);
            }
        }
        long newestRevision = activeDocuments.values().stream()
                .mapToLong(point -> longNumber(point.payload(), "document_revision"))
                .max().orElse(0L);
        for (StoredVectorPoint document : activeDocuments.values()) {
            if (longNumber(document.payload(), "document_revision") < newestRevision) {
                vectorStoreClient.setPayload(filter(tenantId, null,
                                "document_id", text(document.payload(), "document_id")),
                        Map.of("document_status", STATUS_SUPERSEDED));
            }
        }
    }

    private RagDocumentVO toDocument(StoredVectorPoint point) {
        Map<String, Object> payload = point.payload();
        String hash = text(payload, "file_sha256");
        return new RagDocumentVO(
                text(payload, "document_id"),
                text(payload, "knowledge_base"),
                text(payload, "document_code"),
                text(payload, "document_title"),
                text(payload, "source_name"),
                text(payload, "source_type"),
                number(payload, "document_version"),
                text(payload, "document_status"),
                number(payload, "document_chunk_count"),
                number(payload, "duplicate_chunk_count"),
                hash.length() > 12 ? hash.substring(0, 12) : hash,
                text(payload, "ingested_at")
        );
    }

    private Map<String, Object> filter(String tenantId, String knowledgeBase, String field, Object value) {
        Map<String, Object> filter = new LinkedHashMap<>();
        filter.put("tenant_id", tenantId);
        if (StringUtils.hasText(knowledgeBase)) {
            filter.put("knowledge_base", knowledgeBase);
        }
        filter.put(field, value);
        return filter;
    }

    private String safeCode(String value, String fallback, String label) {
        String code = StringUtils.hasText(value) ? value.trim() : fallback;
        if (!StringUtils.hasText(code) || !SAFE_CODE.matcher(code).matches()) {
            throw new IllegalArgumentException(label + "仅允许 1-64 位字母、数字、下划线和连字符");
        }
        return code;
    }

    private String generatedCode(String title) {
        String latin = Normalizer.normalize(title, Normalizer.Form.NFKC)
                .toLowerCase(Locale.ROOT)
                .replaceAll("[^a-z0-9]+", "-")
                .replaceAll("(^-+|-+$)", "");
        return StringUtils.hasText(latin) ? latin.substring(0, Math.min(48, latin.length()))
                : "doc-" + sha256(title.getBytes(StandardCharsets.UTF_8)).substring(0, 12);
    }

    private String requireTenant() {
        String tenantId = TenantContextHolder.getTenantId();
        if (!StringUtils.hasText(tenantId)) {
            throw new IllegalStateException("缺少当前租户上下文");
        }
        return tenantId;
    }

    private String requiredId(String value) {
        if (!StringUtils.hasText(value) || value.length() > 64) {
            throw new IllegalArgumentException("文档 ID 不合法");
        }
        return value;
    }

    private String deterministicId(String value) {
        return UUID.nameUUIDFromBytes(value.getBytes(StandardCharsets.UTF_8)).toString();
    }

    private String normalizeForHash(String value) {
        return Normalizer.normalize(value == null ? "" : value, Normalizer.Form.NFKC)
                .replace("\r\n", "\n")
                .replaceAll("(?m)^位置: (.+?) / 第\\d+行(?: / 片段\\d+)?$", "位置: $1")
                .replaceAll("(?m)^- (序号|行号|记录编号):\\s*[^\\n]*(?:\\n|$)", "")
                .replaceAll("[\\t ]+", " ")
                .trim();
    }

    private String sha256(byte[] bytes) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
            StringBuilder value = new StringBuilder(digest.length * 2);
            for (byte item : digest) {
                value.append(String.format("%02x", item));
            }
            return value.toString();
        } catch (Exception exception) {
            throw new IllegalStateException("SHA-256 不可用", exception);
        }
    }

    private String text(Map<String, Object> payload, String key) {
        Object value = payload.get(key);
        return value == null ? "" : String.valueOf(value);
    }

    private int number(Map<String, Object> payload, String key) {
        Object value = payload.get(key);
        return value instanceof Number number ? number.intValue() : Integer.parseInt(String.valueOf(value));
    }

    private long longNumber(Map<String, Object> payload, String key) {
        Object value = payload.get(key);
        return value instanceof Number number ? number.longValue() : Long.parseLong(String.valueOf(value));
    }

    private record ChunkAccumulator(KnowledgeChunkDraft chunk, List<String> locators,
                                    List<Map<String, Object>> metadata) {

        private ChunkAccumulator(KnowledgeChunkDraft chunk) {
            this(chunk, new ArrayList<>(), new ArrayList<>());
        }
    }
}
