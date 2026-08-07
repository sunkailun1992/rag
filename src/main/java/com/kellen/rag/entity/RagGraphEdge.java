package com.kellen.rag.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import com.kellen.entity.EntityBase;
import com.kellen.rag.entity.enums.RagGraphRelationType;
import lombok.Getter;
import lombok.Setter;

/**
 * RAG 知识图谱有向关系。
 */
@Getter
@Setter
@TableName("rag_graph_edge")
public class RagGraphEdge extends EntityBase {

    private String knowledgeBase;

    private String documentId;

    private String documentCode;

    private Integer documentVersion;

    private String sourceNodeId;

    private String targetNodeId;

    private RagGraphRelationType relationType;

    private String relationLabel;

    private String sourceLocator;

    private String propertiesJson;
}
