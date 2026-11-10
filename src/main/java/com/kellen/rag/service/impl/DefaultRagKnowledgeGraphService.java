package com.kellen.rag.service.impl;

import com.kellen.rag.client.VectorStoreClient;
import com.kellen.rag.client.RagEngineClient;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.entity.enums.RagGraphNodeType;
import com.kellen.rag.entity.vo.RagGraphSyncVO;
import com.kellen.rag.entity.vo.RagKnowledgeGraphVO;
import com.kellen.rag.entity.vo.GraphBuildVO;
import com.kellen.rag.model.ActiveGraphSource;
import com.kellen.rag.model.GraphRemovalFence;
import com.kellen.rag.exception.ExternalServiceException;
import com.kellen.rag.exception.GraphSubmissionInvalidException;
import com.kellen.rag.model.KnowledgeGraphViewMode;
import com.kellen.rag.model.StoredVectorPoint;
import com.kellen.rag.service.RagKnowledgeGraphService;
import com.kellen.utils.context.TenantContextHolder;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * 从 Qdrant 活动切片构建图谱，并通过存储端口提供查询结果。
 */
@Service
public class DefaultRagKnowledgeGraphService implements RagKnowledgeGraphService {

    private static final Pattern SAFE_CODE = Pattern.compile("[A-Za-z0-9_-]{1,64}");
    private static final int MAX_SYNC_POINTS = 10000;
    private static final Set<String> GRAPH_SOURCE_FIELDS = Set.of(
            "tenant_id", "knowledge_base", "document_status", "document_id", "document_code",
            "document_version", "document_revision", "chunk_id", "content_sha256");

    private final RagVectorProperties properties;
    private final VectorStoreClient vectorStoreClient;
    private final RagEngineClient engine;

    public DefaultRagKnowledgeGraphService(RagVectorProperties properties,
                                           VectorStoreClient vectorStoreClient,
                                           RagEngineClient engine) {
        this.properties = properties;
        this.vectorStoreClient = vectorStoreClient;
        this.engine = engine;
    }

    @Override
    public RagGraphSyncVO sync(String knowledgeBase) {
        return sync(knowledgeBase, null);
    }

    @Override
    public RagGraphSyncVO sync(String knowledgeBase, String submissionKey) {
        String tenantId = requireTenant();
        String base = safeCode(knowledgeBase, properties.getDefaultKnowledgeBase());
        if (submissionKey == null) {
            return RagGraphSyncVO.fromBuild(engine.buildGraph(base, snapshot(tenantId, base, null).sources(), true));
        }
        requireSubmissionKey(submissionKey);
        return RagGraphSyncVO.fromBuild(engine.buildGraph(base, snapshot(tenantId, base, null).sources(),
                true, null, submissionKey));
    }

    @Override
    public GraphBuildVO submission(String submissionKey, String knowledgeBase) {
        requireTenant();
        requireSubmissionKey(submissionKey);
        return engine.graphSubmission(submissionKey, safeCode(knowledgeBase, properties.getDefaultKnowledgeBase()));
    }

    @Override
    public RagGraphSyncVO syncDocument(String documentId) {
        String tenantId = requireTenant();
        String id = requiredId(documentId);
        ActiveSnapshot snapshot = snapshot(tenantId, null, id);
        if (snapshot.sources().isEmpty()) {
            deactivateDocumentWithFence(id);
            return new RagGraphSyncVO("", 0, 0, 0, null, "SUCCEEDED");
        }
        // 相同活动版本的重复上传复用同一次派生构建；新的明确重建仍由手动同步新键表达。
        String identity = tenantId + "\n" + snapshot.knowledgeBase() + "\n" + snapshot.sources().stream()
                .sorted(java.util.Comparator.comparing(ActiveGraphSource::pointId)).toList();
        String submissionKey;
        try {
            submissionKey = "upload_" + java.util.HexFormat.of().formatHex(java.security.MessageDigest
                    .getInstance("SHA-256").digest(identity.getBytes(java.nio.charset.StandardCharsets.UTF_8)));
        } catch (java.security.NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 unavailable", exception);
        }
        return RagGraphSyncVO.fromBuild(engine.buildGraph(snapshot.knowledgeBase(), snapshot.sources(), false,
                null, submissionKey));
    }

    private void requireSubmissionKey(String key) {
        if (!key.matches("[A-Za-z0-9_-]{16,128}")) {
            throw new GraphSubmissionInvalidException();
        }
    }

    @Override
    public void deactivateDocument(String documentId) {
        deactivateDocumentWithFence(documentId);
    }

    @Override
    public GraphRemovalFence deactivateDocumentWithFence(String documentId) {
        String tenant = requireTenant();
        String id = requiredId(documentId);
        return engine.deactivateGraphDocument(id, existingRevision(tenant, id));
    }

    @Override
    public RagGraphSyncVO restoreDocument(String documentId, GraphRemovalFence fence) {
        String tenant = requireTenant();
        String id = requiredId(documentId);
        if (fence == null || !id.equals(fence.documentId()) || fence.barrierEpoch() <= 0) {
            throw new IllegalStateException("缺少本次图谱删除屏障票据");
        }
        ActiveSnapshot active = snapshot(tenant, null, id);
        if (active.sources().isEmpty()) return new RagGraphSyncVO("", 0, 0, 0, null, "CANCELLED");
        if (active.sources().stream().anyMatch(source -> source.documentRevision() != fence.documentRevision())) {
            throw new IllegalStateException("图谱补偿来源已换版，不能恢复旧屏障");
        }
        return RagGraphSyncVO.fromBuild(engine.buildGraph(active.knowledgeBase(), active.sources(), false, fence));
    }

    @Override
    public GraphBuildVO build(String buildId, String knowledgeBase) {
        requireTenant();
        String base = safeCode(knowledgeBase, properties.getDefaultKnowledgeBase());
        return engine.graphBuild(buildId, base);
    }

    @Override
    public RagKnowledgeGraphVO query(String knowledgeBase, String searchText,
                                     RagGraphNodeType nodeType, KnowledgeGraphViewMode viewMode,
                                     Integer limit) {
        String tenantId = requireTenant();
        String base = safeCode(knowledgeBase, properties.getDefaultKnowledgeBase());
        String search = normalizeSearch(searchText);
        KnowledgeGraphViewMode mode = viewMode == null ? KnowledgeGraphViewMode.OVERVIEW : viewMode;
        int boundedLimit = Math.min(Math.max(limit == null ? 240 : limit, 20), 500);
        ActiveSnapshot before = snapshot(tenantId, base, null);
        RagKnowledgeGraphVO result = engine.queryGraph(base, before.sources(), search, nodeType, mode, boundedLimit);
        ActiveSnapshot after = snapshot(tenantId, base, null);
        // 查询期间删文档或换版时拒绝旧响应，避免把旧图证据展示成当前活动知识。
        if (!new HashSet<>(before.sources()).equals(new HashSet<>(after.sources()))) {
            throw ExternalServiceException.httpFailure("RAG engine graph source changed", 502);
        }
        return result;
    }

    /** 删除屏障保存当前已知最大业务 revision，包括已停用或失败的同文档切片。 */
    private long existingRevision(String tenant, String documentId) {
        if (!vectorStoreClient.collectionExists()) return 0;
        Map<String, Object> filter = new LinkedHashMap<>();
        filter.put("tenant_id", tenant);
        filter.put("document_id", documentId);
        long expected = vectorStoreClient.count(filter);
        if (expected < 0 || expected > MAX_SYNC_POINTS) throw new IllegalStateException("文档切片超过屏障核对上限");
        List<StoredVectorPoint> points = vectorStoreClient.scroll(filter, MAX_SYNC_POINTS, false);
        if (points.size() != expected || vectorStoreClient.count(filter) != expected) {
            throw new IllegalStateException("文档屏障来源快照不完整或发生变化");
        }
        long revision = 0;
        for (StoredVectorPoint point : points) {
            if (!tenant.equals(point.payload().get("tenant_id"))
                    || !documentId.equals(point.payload().get("document_id"))) {
                throw new IllegalStateException("文档屏障来源超出范围");
            }
            revision = Math.max(revision, sourceNumber(point.payload(), "document_revision"));
        }
        return revision;
    }

    /** 完整来源快照必须计数、读回、逐点核验；禁止截断后构建或清理。 */
    private ActiveSnapshot snapshot(String tenant, String knowledgeBase, String documentId) {
        if (!vectorStoreClient.collectionExists()) return new ActiveSnapshot(knowledgeBase, List.of());
        Map<String, Object> filter = filter(tenant, knowledgeBase, documentId);
        long expected = vectorStoreClient.count(filter);
        if (expected > MAX_SYNC_POINTS) throw new IllegalStateException("活动知识切片超过单次图谱同步上限，请拆分知识库后重试");
        if (expected < 0) throw new IllegalStateException("活动知识切片数量不合法");
        List<StoredVectorPoint> points = vectorStoreClient.scrollPayload(filter, MAX_SYNC_POINTS + 1, GRAPH_SOURCE_FIELDS);
        if (points.size() != expected || vectorStoreClient.count(filter) != expected) {
            throw new IllegalStateException("活动知识切片快照不完整或发生变化，请重试");
        }
        List<ActiveGraphSource> sources = new ArrayList<>();
        HashSet<String> ids = new HashSet<>();
        String base = knowledgeBase;
        for (StoredVectorPoint point : points) {
            Map<String, Object> payload = point.payload();
            String actualBase = safeCode(sourceText(payload, "knowledge_base", 64), null);
            if (base == null) base = actualBase;
            if (!tenant.equals(payload.get("tenant_id")) || !base.equals(actualBase)
                    || !"ACTIVE".equals(payload.get("document_status"))
                    || documentId != null && !documentId.equals(payload.get("document_id"))
                    || point.id() == null || point.id().isBlank() || point.id().length() > 128 || !ids.add(point.id())) {
                throw new IllegalStateException("活动图谱来源超出范围或身份重复");
            }
            long version = sourceNumber(payload, "document_version");
            long revision = sourceNumber(payload, "document_revision");
            String hash = sourceText(payload, "content_sha256", 64);
            if (version > Integer.MAX_VALUE || !hash.matches("[0-9a-f]{64}")) {
                throw new IllegalStateException("活动图谱来源版本或摘要不合法");
            }
            sources.add(new ActiveGraphSource(point.id(), sourceText(payload, "document_id", 64),
                    safeCode(sourceText(payload, "document_code", 64), null), (int) version, revision,
                    sourceText(payload, "chunk_id", 128), hash));
        }
        return new ActiveSnapshot(base, List.copyOf(sources));
    }

    private String sourceText(Map<String, Object> payload, String key, int maximum) {
        Object value = payload.get(key);
        if (!(value instanceof String text) || text.isBlank() || text.length() > maximum) {
            throw new IllegalStateException("活动图谱来源身份不完整");
        }
        return text;
    }

    private long sourceNumber(Map<String, Object> payload, String key) {
        Object value = payload.get(key);
        try {
            if (!(value instanceof Number)) throw new NumberFormatException();
            long number = Long.parseLong(value.toString());
            if (number <= 0) throw new NumberFormatException();
            return number;
        } catch (NumberFormatException exception) {
            throw new IllegalStateException("活动图谱来源版本不合法");
        }
    }

    private record ActiveSnapshot(String knowledgeBase, List<ActiveGraphSource> sources) {
    }

    private Map<String, Object> filter(String tenantId, String knowledgeBase, String documentId) {
        Map<String, Object> filter = new LinkedHashMap<>();
        filter.put("tenant_id", tenantId);
        if (StringUtils.hasText(knowledgeBase)) {
            filter.put("knowledge_base", knowledgeBase);
        }
        if (StringUtils.hasText(documentId)) {
            filter.put("document_id", documentId);
        }
        filter.put("document_status", "ACTIVE");
        return filter;
    }

    private void requireBoundedPointCount(Map<String, Object> filter) {
        if (vectorStoreClient.count(filter) > MAX_SYNC_POINTS) {
            throw new IllegalStateException("活动知识切片超过单次图谱同步上限，请拆分知识库后重试");
        }
    }

    private String normalizeSearch(String value) {
        if (!StringUtils.hasText(value)) {
            return null;
        }
        String text = value.trim();
        if (text.length() > 80) {
            throw new IllegalArgumentException("图谱搜索词不能超过80个字符");
        }
        return text;
    }

    private String safeCode(String value, String fallback) {
        String code = StringUtils.hasText(value) ? value.trim() : fallback;
        if (!StringUtils.hasText(code) || !SAFE_CODE.matcher(code).matches()) {
            throw new IllegalArgumentException("知识库编码仅允许 1-64 位字母、数字、下划线和连字符");
        }
        return code;
    }

    private String requiredId(String value) {
        if (!StringUtils.hasText(value) || value.length() > 64) {
            throw new IllegalArgumentException("文档 ID 不合法");
        }
        return value;
    }

    private String requireTenant() {
        String tenantId = TenantContextHolder.getTenantId();
        if (!StringUtils.hasText(tenantId)) {
            throw new IllegalStateException("缺少当前租户上下文");
        }
        return tenantId;
    }

    private String text(Map<String, Object> payload, String key) {
        Object value = payload.get(key);
        return value == null ? "" : String.valueOf(value);
    }
}
