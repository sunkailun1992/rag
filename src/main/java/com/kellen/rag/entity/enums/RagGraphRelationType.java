package com.kellen.rag.entity.enums;

import com.baomidou.mybatisplus.annotation.IEnum;

/**
 * 知识图谱关系类型。
 */
public enum RagGraphRelationType implements IEnum<String> {

    CONTAINS,
    HAS_ATTRIBUTE,
    MENTIONS,
    RELATED_TO;

    @Override
    public String getValue() {
        return name();
    }
}
