package com.kellen.rag.service.impl;

import com.kellen.rag.client.VectorStoreClient;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.entity.enums.RagGraphNodeType;
import com.kellen.rag.entity.vo.RagGraphEdgeVO;
import com.kellen.rag.entity.vo.RagGraphNodeVO;
import com.kellen.rag.entity.vo.RagGraphSyncVO;
import com.kellen.rag.entity.vo.RagKnowledgeGraphVO;
import com.kellen.rag.graph.KnowledgeGraphProjector;
import com.kellen.rag.model.KnowledgeGraphEdge;
import com.kellen.rag.model.KnowledgeGraphNode;
import com.kellen.rag.model.KnowledgeGraphProjection;
import com.kellen.rag.model.KnowledgeGraphQuery;
import com.kellen.rag.model.KnowledgeGraphSnapshot;
import com.kellen.rag.model.KnowledgeGraphViewMode;
import com.kellen.rag.model.StoredVectorPoint;
import com.kellen.rag.service.RagKnowledgeGraphService;
import com.kellen.rag.store.KnowledgeGraphStore;
import com.kellen.utils.context.TenantContextHolder;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

/**
 * 从 Qdrant 活动切片构建图谱，并通过存储端口提供查询结果。
 */
@Service
public class DefaultRagKnowledgeGraphService implements RagKnowledgeGraphService {

    private static final Pattern SAFE_CODE = Pattern.compile("[A-Za-z0-9_-]{1,64}");
    private static final int MAX_SYNC_POINTS = 10000;

    private final RagVectorProperties properties;
    private final VectorStoreClient vectorStoreClient;
    private final KnowledgeGraphProjector projector;
    private final KnowledgeGraphStore graphStore;

    public DefaultRagKnowledgeGraphService(RagVectorProperties properties,
                                           VectorStoreClient vectorStoreClient,
                                           KnowledgeGraphProjector projector,
                                           KnowledgeGraphStore graphStore) {
        this.properties = properties;
        this.vectorStoreClient = vectorStoreClient;
        this.projector = projector;
        this.graphStore = graphStore;
    }

    @Override
    public RagGraphSyncVO sync(String knowledgeBase) {
        String tenantId = requireTenant();
        String base = safeCode(knowledgeBase, properties.getDefaultKnowledgeBase());
        if (!vectorStoreClient.collectionExists()) {
            return new RagGraphSyncVO(base, 0, 0, 0);
        }
        Map<String, Object> filter = filter(tenantId, base, null);
        requireBoundedPointCount(filter);
        Map<String, List<StoredVectorPoint>> documents = vectorStoreClient
                .scroll(filter, MAX_SYNC_POINTS, false).stream()
                .collect(Collectors.groupingBy(point -> text(point.payload(), "document_id"),
                        LinkedHashMap::new, Collectors.toList()));
        int nodes = 0;
        int edges = 0;
        for (List<StoredVectorPoint> points : documents.values()) {
            KnowledgeGraphProjection projection = projector.project(tenantId, points);
            graphStore.save(projection);
            nodes += projection.nodes().size();
            edges += projection.edges().size();
        }
        graphStore.retainActiveDocuments(tenantId, base, documents.keySet());
        return new RagGraphSyncVO(base, documents.size(), nodes, edges);
    }

    @Override
    public RagGraphSyncVO syncDocument(String documentId) {
        String tenantId = requireTenant();
        String id = requiredId(documentId);
        Map<String, Object> filter = filter(tenantId, null, id);
        requireBoundedPointCount(filter);
        List<StoredVectorPoint> points = vectorStoreClient.scroll(filter, MAX_SYNC_POINTS, false);
        if (points.isEmpty()) {
            return new RagGraphSyncVO("", 0, 0, 0);
        }
        KnowledgeGraphProjection projection = projector.project(tenantId, points);
        graphStore.save(projection);
        return new RagGraphSyncVO(projection.knowledgeBase(), 1,
                projection.nodes().size(), projection.edges().size());
    }

    @Override
    public void deactivateDocument(String documentId) {
        graphStore.deactivateDocument(requireTenant(), requiredId(documentId));
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
        KnowledgeGraphSnapshot snapshot = graphStore.query(new KnowledgeGraphQuery(
                tenantId, base, search, nodeType, mode, boundedLimit));
        List<RagGraphNodeVO> nodes = snapshot.nodes().stream().map(this::toNodeVO).toList();
        List<RagGraphEdgeVO> edges = snapshot.edges().stream().map(this::toEdgeVO).toList();
        Map<String, Long> nodeCounts = snapshot.nodes().stream().collect(Collectors.groupingBy(
                node -> node.type().name(), LinkedHashMap::new, Collectors.counting()));
        Map<String, Long> relationCounts = snapshot.edges().stream().collect(Collectors.groupingBy(
                edge -> edge.type().name(), LinkedHashMap::new, Collectors.counting()));
        return new RagKnowledgeGraphVO(base, mode.name(), nodes.size(), edges.size(),
                snapshot.truncated(), nodeCounts, relationCounts, nodes, edges);
    }

    private RagGraphNodeVO toNodeVO(KnowledgeGraphNode node) {
        return new RagGraphNodeVO(node.id(), node.type().name(), node.name(), node.label(),
                node.documentId(), node.documentCode(), node.documentVersion(), node.sourceLocator(),
                node.properties());
    }

    private RagGraphEdgeVO toEdgeVO(KnowledgeGraphEdge edge) {
        return new RagGraphEdgeVO(edge.id(), edge.sourceNodeId(), edge.targetNodeId(),
                edge.type().name(), edge.label(), edge.sourceLocator(), edge.properties());
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
