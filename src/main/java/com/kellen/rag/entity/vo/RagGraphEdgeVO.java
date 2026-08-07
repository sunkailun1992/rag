package com.kellen.rag.entity.vo;

import java.util.Map;

/**
 * 图谱关系展示对象。
 */
public record RagGraphEdgeVO(
        String id,
        String source,
        String target,
        String type,
        String label,
        String sourceLocator,
        Map<String, Object> properties
) {
}
