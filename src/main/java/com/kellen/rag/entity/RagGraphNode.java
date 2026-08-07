package com.kellen.rag.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import com.kellen.entity.EntityBase;
import com.kellen.rag.entity.enums.RagGraphNodeType;
import lombok.Getter;
import lombok.Setter;

/**
 * RAG 知识图谱节点。
 *
 * <p>完整切片正文仍保存在向量库；节点只保存展示名称、来源定位和结构化属性，
 * 使关系数据保持轻量并可迁移到 Neo4j。</p>
 */
@Getter
@Setter
@TableName("rag_graph_node")
public class RagGraphNode extends EntityBase {

    private String knowledgeBase;

    private String documentId;

    private String documentCode;

    private Integer documentVersion;

    private RagGraphNodeType nodeType;

    private String name;

    private String sourceLocator;

    private String propertiesJson;
}
