package com.kellen.rag.controller;

import com.kellen.rag.entity.enums.RagGraphNodeType;
import com.kellen.rag.entity.vo.RagGraphSyncVO;
import com.kellen.rag.entity.vo.GraphBuildVO;
import com.kellen.rag.entity.vo.RagKnowledgeGraphVO;
import com.kellen.rag.model.KnowledgeGraphViewMode;
import com.kellen.rag.service.RagKnowledgeGraphService;
import com.kellen.utils.response.ApiResponse;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * RAG 知识图谱查询与同步入口。
 */
@RestController
@RequestMapping("/api/rag/graphs")
@Tag(name = "RAG 知识图谱", description = "租户隔离的知识关系查询和向量文档图谱同步")
public class RagKnowledgeGraphController {

    private final RagKnowledgeGraphService graphService;

    public RagKnowledgeGraphController(RagKnowledgeGraphService graphService) {
        this.graphService = graphService;
    }

    @GetMapping
    @PreAuthorize("hasAuthority('rag:graph:view')")
    @Operation(summary = "查询知识图谱", description = "默认返回文档与共享概念网络，可切换为来源结构明细")
    public ApiResponse<RagKnowledgeGraphVO> query(
            @RequestParam(required = false) String knowledgeBase,
            @RequestParam(required = false) String query,
            @RequestParam(required = false) RagGraphNodeType nodeType,
            @RequestParam(required = false) KnowledgeGraphViewMode viewMode,
            @RequestParam(required = false) Integer limit) {
        return ApiResponse.success(graphService.query(knowledgeBase, query, nodeType, viewMode, limit));
    }

    @PostMapping("/sync")
    @PreAuthorize("hasAuthority('rag:graph:manage')")
    @Operation(summary = "同步知识图谱", description = "从当前租户活动向量切片幂等生成节点和关系")
    public ApiResponse<RagGraphSyncVO> sync(@RequestParam(required = false) String knowledgeBase,
            @RequestHeader(value = "X-Graph-Submission-Key", required = false) String submissionKey) {
        return ApiResponse.success(submissionKey == null ? graphService.sync(knowledgeBase)
                : graphService.sync(knowledgeBase, submissionKey));
    }

    @GetMapping("/submissions/{submissionKey}")
    @PreAuthorize("hasAnyAuthority('rag:graph:view', 'rag:graph:manage')")
    @Operation(summary = "查回图谱提交", description = "按不透明键只读查回当前租户和知识库的原任务，未找到时返回空")
    public ApiResponse<GraphBuildVO> submission(@PathVariable String submissionKey,
                                               @RequestParam String knowledgeBase) {
        return ApiResponse.success(graphService.submission(submissionKey, knowledgeBase));
    }

    @GetMapping("/builds/{id}")
    @PreAuthorize("hasAnyAuthority('rag:graph:view', 'rag:graph:manage')")
    @Operation(summary = "查询图谱构建任务", description = "只读取当前租户和指定知识库的异步任务状态")
    public ApiResponse<GraphBuildVO> build(@PathVariable String id,
                                           @RequestParam String knowledgeBase) {
        return ApiResponse.success(graphService.build(id, knowledgeBase));
    }
}
