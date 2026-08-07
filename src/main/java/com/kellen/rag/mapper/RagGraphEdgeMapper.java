package com.kellen.rag.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.kellen.rag.entity.RagGraphEdge;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Param;

/**
 * 知识图谱关系数据访问入口。
 */
public interface RagGraphEdgeMapper extends BaseMapper<RagGraphEdge> {

    /**
     * 物理清理可重建的文档关系，使同一稳定文档再次激活时可以复用主键。
     */
    @Delete("DELETE FROM rag_graph_edge WHERE document_id = #{documentId}")
    int purgeByDocumentId(@Param("documentId") String documentId);
}
