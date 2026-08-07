package com.kellen.rag.entity.enums;

import com.baomidou.mybatisplus.annotation.IEnum;

/**
 * 知识图谱节点类型。
 */
public enum RagGraphNodeType implements IEnum<String> {

    DOCUMENT,
    SECTION,
    RECORD,
    CONCEPT;

    @Override
    public String getValue() {
        return name();
    }
}
