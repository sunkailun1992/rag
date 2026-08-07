package com.kellen.rag.entity.vo;

import java.util.Map;

/**
 * 图谱节点展示对象。
 */
public record RagGraphNodeVO(
        String id,
        String type,
        String name,
        String label,
        String documentId,
        String documentCode,
        Integer documentVersion,
        String sourceLocator,
        Map<String, Object> properties
) {
}
