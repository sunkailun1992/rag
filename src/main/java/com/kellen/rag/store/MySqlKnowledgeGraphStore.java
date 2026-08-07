package com.kellen.rag.store;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.kellen.rag.entity.RagGraphEdge;
import com.kellen.rag.entity.RagGraphNode;
import com.kellen.rag.entity.enums.RagGraphNodeType;
import com.kellen.rag.entity.enums.RagGraphRelationType;
import com.kellen.rag.mapper.RagGraphEdgeMapper;
import com.kellen.rag.mapper.RagGraphNodeMapper;
import com.kellen.rag.model.KnowledgeGraphEdge;
import com.kellen.rag.model.KnowledgeGraphNode;
import com.kellen.rag.model.KnowledgeGraphProjection;
import com.kellen.rag.model.KnowledgeGraphQuery;
import com.kellen.rag.model.KnowledgeGraphSnapshot;
import com.kellen.rag.model.KnowledgeGraphViewMode;
import com.kellen.utils.context.TenantContextHolder;
import com.kellen.utils.json.JsonUtil;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 基于 MySQL 邻接表的知识图谱存储适配器。
 */
@Repository
@ConditionalOnProperty(prefix = "rag.graph", name = "store", havingValue = "mysql", matchIfMissing = true)
public class MySqlKnowledgeGraphStore implements KnowledgeGraphStore {

    private static final int BATCH_SIZE = 200;

    private final RagGraphNodeMapper nodeMapper;
    private final RagGraphEdgeMapper edgeMapper;

    public MySqlKnowledgeGraphStore(RagGraphNodeMapper nodeMapper, RagGraphEdgeMapper edgeMapper) {
        this.nodeMapper = nodeMapper;
        this.edgeMapper = edgeMapper;
    }

    /**
     * 保存一个文档的稳定图谱，并停用相同文档编码的旧版本结构。
     *
     * <p>租户条件由 MyBatis-Plus 租户插件统一追加；方法仍校验传入租户与线程上下文一致，
     * 防止未来更换为不带 SQL 拦截器的图数据库适配器时丢失边界。</p>
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public void save(KnowledgeGraphProjection projection) {
        requireCurrentTenant(projection.tenantId());
        // 文档图谱完全可由活动向量重建；同一稳定文档重新激活时先物理清理私有结构，
        // 既避免逻辑删除主键冲突，也让投影规则升级后能替换旧关系。共享概念节点不受影响。
        edgeMapper.purgeByDocumentId(projection.documentId());
        nodeMapper.purgeByDocumentId(projection.documentId());
        List<RagGraphNode> missingNodes = missingNodes(projection.nodes()).stream()
                .map(node -> toEntity(projection, node))
                .toList();
        if (!missingNodes.isEmpty()) {
            nodeMapper.insert(missingNodes, BATCH_SIZE);
        }

        List<RagGraphEdge> missingEdges = missingEdges(projection.edges()).stream()
                .map(edge -> toEntity(projection, edge))
                .toList();
        if (!missingEdges.isEmpty()) {
            edgeMapper.insert(missingEdges, BATCH_SIZE);
        }

        long ownedNodeCount = nodeMapper.selectCount(new LambdaQueryWrapper<RagGraphNode>()
                .eq(RagGraphNode::getKnowledgeBase, projection.knowledgeBase())
                .eq(RagGraphNode::getDocumentId, projection.documentId()));
        long edgeCount = edgeMapper.selectCount(new LambdaQueryWrapper<RagGraphEdge>()
                .eq(RagGraphEdge::getKnowledgeBase, projection.knowledgeBase())
                .eq(RagGraphEdge::getDocumentId, projection.documentId()));
        long expectedOwnedNodes = projection.nodes().stream()
                .filter(node -> projection.documentId().equals(node.documentId()))
                .count();
        if (ownedNodeCount != expectedOwnedNodes || edgeCount != projection.edges().size()) {
            throw new IllegalStateException("知识图谱写入后的数量校验失败");
        }

        // 新版本完成图谱写入后再逻辑删除旧版本的文档私有节点和关系，共享概念节点保留复用。
        edgeMapper.delete(new LambdaQueryWrapper<RagGraphEdge>()
                .eq(RagGraphEdge::getKnowledgeBase, projection.knowledgeBase())
                .eq(RagGraphEdge::getDocumentCode, projection.documentCode())
                .ne(RagGraphEdge::getDocumentId, projection.documentId()));
        nodeMapper.delete(new LambdaQueryWrapper<RagGraphNode>()
                .eq(RagGraphNode::getKnowledgeBase, projection.knowledgeBase())
                .eq(RagGraphNode::getDocumentCode, projection.documentCode())
                .ne(RagGraphNode::getDocumentId, projection.documentId()));
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void deactivateDocument(String tenantId, String documentId) {
        requireCurrentTenant(tenantId);
        edgeMapper.delete(new LambdaQueryWrapper<RagGraphEdge>()
                .eq(RagGraphEdge::getDocumentId, documentId));
        nodeMapper.delete(new LambdaQueryWrapper<RagGraphNode>()
                .eq(RagGraphNode::getDocumentId, documentId));
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void retainActiveDocuments(String tenantId, String knowledgeBase,
                                      Set<String> activeDocumentIds) {
        requireCurrentTenant(tenantId);
        LambdaQueryWrapper<RagGraphEdge> edgeWrapper = new LambdaQueryWrapper<RagGraphEdge>()
                .eq(RagGraphEdge::getKnowledgeBase, knowledgeBase);
        LambdaQueryWrapper<RagGraphNode> nodeWrapper = new LambdaQueryWrapper<RagGraphNode>()
                .eq(RagGraphNode::getKnowledgeBase, knowledgeBase)
                .isNotNull(RagGraphNode::getDocumentId);
        if (!activeDocumentIds.isEmpty()) {
            edgeWrapper.notIn(RagGraphEdge::getDocumentId, activeDocumentIds);
            nodeWrapper.notIn(RagGraphNode::getDocumentId, activeDocumentIds);
        }
        edgeMapper.delete(edgeWrapper);
        nodeMapper.delete(nodeWrapper);
    }

    @Override
    public KnowledgeGraphSnapshot query(KnowledgeGraphQuery query) {
        requireCurrentTenant(query.tenantId());
        int edgeLimit = Math.min(query.limit() * 4, 2000);
        List<RagGraphNode> matchedNodes = StringUtils.hasText(query.searchText())
                || query.nodeType() != null ? findMatchedNodes(query, Math.min(query.limit(), 80)) : List.of();
        List<RagGraphEdge> candidateEdges = findEdges(query, matchedNodes, edgeLimit);
        PrunedGraph pruned = prune(candidateEdges, query.limit());

        List<RagGraphNode> nodes = pruned.nodeIds().isEmpty()
                ? findMatchedNodes(query, query.limit())
                : nodeMapper.selectByIds(pruned.nodeIds());
        Set<String> returnedIds = new LinkedHashSet<>();
        List<KnowledgeGraphNode> graphNodes = nodes.stream()
                .filter(node -> acceptNodeForView(node, query.viewMode()))
                .sorted(Comparator.comparing(RagGraphNode::getSorting,
                                Comparator.nullsLast(Comparator.naturalOrder()))
                        .thenComparing(RagGraphNode::getName, Comparator.nullsLast(String::compareTo)))
                .limit(query.limit())
                .map(node -> {
                    returnedIds.add(node.getId());
                    return fromEntity(node);
                })
                .toList();
        List<KnowledgeGraphEdge> graphEdges = pruned.edges().stream()
                .filter(edge -> returnedIds.contains(edge.getSourceNodeId())
                        && returnedIds.contains(edge.getTargetNodeId()))
                .map(this::fromEntity)
                .toList();
        boolean truncated = candidateEdges.size() >= edgeLimit
                || pruned.truncated() || nodes.size() > graphNodes.size();
        return new KnowledgeGraphSnapshot(graphNodes, graphEdges, truncated);
    }

    private List<RagGraphNode> findMatchedNodes(KnowledgeGraphQuery query, int limit) {
        LambdaQueryWrapper<RagGraphNode> wrapper = new LambdaQueryWrapper<RagGraphNode>()
                .eq(RagGraphNode::getKnowledgeBase, query.knowledgeBase());
        if (query.viewMode() == KnowledgeGraphViewMode.OVERVIEW) {
            wrapper.in(RagGraphNode::getNodeType,
                    List.of(RagGraphNodeType.DOCUMENT, RagGraphNodeType.CONCEPT));
        }
        if (query.nodeType() != null) {
            wrapper.eq(RagGraphNode::getNodeType, query.nodeType());
        }
        if (StringUtils.hasText(query.searchText())) {
            String text = query.searchText().trim();
            wrapper.and(value -> value.like(RagGraphNode::getName, text)
                    .or().like(RagGraphNode::getLabel, text)
                    .or().like(RagGraphNode::getDocumentCode, text)
                    .or().like(RagGraphNode::getSourceLocator, text));
        }
        return nodeMapper.selectList(wrapper.orderByAsc(RagGraphNode::getSorting)
                .last("LIMIT " + Math.max(1, limit)));
    }

    private List<RagGraphEdge> findEdges(KnowledgeGraphQuery query, List<RagGraphNode> matchedNodes,
                                         int limit) {
        LambdaQueryWrapper<RagGraphEdge> wrapper = new LambdaQueryWrapper<RagGraphEdge>()
                .eq(RagGraphEdge::getKnowledgeBase, query.knowledgeBase());
        if (query.viewMode() == KnowledgeGraphViewMode.OVERVIEW) {
            wrapper.eq(RagGraphEdge::getRelationType, RagGraphRelationType.MENTIONS);
        }
        if (!matchedNodes.isEmpty()) {
            List<String> ids = matchedNodes.stream().map(RagGraphNode::getId).toList();
            wrapper.and(value -> value.in(RagGraphEdge::getSourceNodeId, ids)
                    .or().in(RagGraphEdge::getTargetNodeId, ids));
        } else if (StringUtils.hasText(query.searchText()) || query.nodeType() != null) {
            return List.of();
        }
        return edgeMapper.selectList(wrapper.orderByAsc(RagGraphEdge::getSorting)
                .last("LIMIT " + Math.max(1, limit)));
    }

    private PrunedGraph prune(List<RagGraphEdge> edges, int nodeLimit) {
        LinkedHashSet<String> nodeIds = new LinkedHashSet<>();
        List<RagGraphEdge> accepted = new ArrayList<>();
        boolean truncated = false;
        for (RagGraphEdge edge : edges) {
            int additions = (nodeIds.contains(edge.getSourceNodeId()) ? 0 : 1)
                    + (nodeIds.contains(edge.getTargetNodeId()) ? 0 : 1);
            if (nodeIds.size() + additions > nodeLimit) {
                truncated = true;
                continue;
            }
            nodeIds.add(edge.getSourceNodeId());
            nodeIds.add(edge.getTargetNodeId());
            accepted.add(edge);
        }
        return new PrunedGraph(List.copyOf(nodeIds), accepted, truncated);
    }

    private boolean acceptNodeForView(RagGraphNode node, KnowledgeGraphViewMode mode) {
        return mode == KnowledgeGraphViewMode.DETAIL
                || node.getNodeType() == RagGraphNodeType.DOCUMENT
                || node.getNodeType() == RagGraphNodeType.CONCEPT;
    }

    private List<KnowledgeGraphNode> missingNodes(List<KnowledgeGraphNode> nodes) {
        Set<String> existing = existingNodeIds(nodes.stream().map(KnowledgeGraphNode::id).toList());
        return nodes.stream().filter(node -> !existing.contains(node.id())).toList();
    }

    private List<KnowledgeGraphEdge> missingEdges(List<KnowledgeGraphEdge> edges) {
        Set<String> existing = existingEdgeIds(edges.stream().map(KnowledgeGraphEdge::id).toList());
        return edges.stream().filter(edge -> !existing.contains(edge.id())).toList();
    }

    private Set<String> existingNodeIds(List<String> ids) {
        Set<String> found = new LinkedHashSet<>();
        for (List<String> batch : batches(ids, 500)) {
            nodeMapper.selectByIds(batch).forEach(node -> found.add(node.getId()));
        }
        return found;
    }

    private Set<String> existingEdgeIds(List<String> ids) {
        Set<String> found = new LinkedHashSet<>();
        for (List<String> batch : batches(ids, 500)) {
            edgeMapper.selectByIds(batch).forEach(edge -> found.add(edge.getId()));
        }
        return found;
    }

    private <T> List<List<T>> batches(List<T> values, int size) {
        List<List<T>> batches = new ArrayList<>();
        for (int start = 0; start < values.size(); start += size) {
            batches.add(values.subList(start, Math.min(values.size(), start + size)));
        }
        return batches;
    }

    private RagGraphNode toEntity(KnowledgeGraphProjection projection, KnowledgeGraphNode node) {
        RagGraphNode entity = new RagGraphNode();
        entity.setId(node.id());
        entity.setCode(node.code());
        entity.setTenantId(projection.tenantId());
        entity.setKnowledgeBase(projection.knowledgeBase());
        entity.setDocumentId(node.documentId());
        entity.setDocumentCode(node.documentCode());
        entity.setDocumentVersion(node.documentVersion());
        entity.setNodeType(node.type());
        entity.setName(node.name());
        entity.setLabel(node.label());
        entity.setSourceLocator(node.sourceLocator());
        entity.setPropertiesJson(JsonUtil.json(node.properties()));
        entity.setSorting(node.sorting());
        return entity;
    }

    private RagGraphEdge toEntity(KnowledgeGraphProjection projection, KnowledgeGraphEdge edge) {
        RagGraphEdge entity = new RagGraphEdge();
        entity.setId(edge.id());
        entity.setCode(edge.code());
        entity.setTenantId(projection.tenantId());
        entity.setKnowledgeBase(projection.knowledgeBase());
        entity.setDocumentId(edge.documentId());
        entity.setDocumentCode(edge.documentCode());
        entity.setDocumentVersion(edge.documentVersion());
        entity.setSourceNodeId(edge.sourceNodeId());
        entity.setTargetNodeId(edge.targetNodeId());
        entity.setRelationType(edge.type());
        entity.setRelationLabel(edge.label());
        entity.setLabel(edge.label());
        entity.setSourceLocator(edge.sourceLocator());
        entity.setPropertiesJson(JsonUtil.json(edge.properties()));
        entity.setSorting(edge.sorting());
        return entity;
    }

    private KnowledgeGraphNode fromEntity(RagGraphNode entity) {
        return new KnowledgeGraphNode(entity.getId(), entity.getCode(), entity.getNodeType(),
                entity.getName(), entity.getLabel(), entity.getDocumentId(), entity.getDocumentCode(),
                entity.getDocumentVersion(), entity.getSourceLocator(), properties(entity.getPropertiesJson()),
                entity.getSorting() == null ? 0 : entity.getSorting());
    }

    private KnowledgeGraphEdge fromEntity(RagGraphEdge entity) {
        return new KnowledgeGraphEdge(entity.getId(), entity.getCode(), entity.getSourceNodeId(),
                entity.getTargetNodeId(), entity.getRelationType(), entity.getRelationLabel(),
                entity.getDocumentId(), entity.getDocumentCode(), entity.getDocumentVersion(),
                entity.getSourceLocator(), properties(entity.getPropertiesJson()),
                entity.getSorting() == null ? 0 : entity.getSorting());
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> properties(String value) {
        Map<String, Object> properties = JsonUtil.bean(value, Map.class);
        return properties == null ? Map.of() : Map.copyOf(properties);
    }

    private void requireCurrentTenant(String tenantId) {
        if (!StringUtils.hasText(tenantId) || !tenantId.equals(TenantContextHolder.getTenantId())) {
            throw new IllegalStateException("知识图谱租户上下文不一致");
        }
    }

    private record PrunedGraph(List<String> nodeIds, List<RagGraphEdge> edges, boolean truncated) {
    }
}
