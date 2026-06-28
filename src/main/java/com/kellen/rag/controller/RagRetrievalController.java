package com.kellen.rag.controller;

import com.kellen.rag.service.RagRetrievalService;
import com.kellen.rpc.rag.RagRetrievalRpcDTO;
import com.kellen.rpc.rag.RagRetrievalRpcRequest;
import com.kellen.utils.response.ApiResponse;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * RAG 知识检索 HTTP 入口。
 */
@RestController
@RequestMapping("/api/rag")
@Tag(name = "RAG", description = "知识检索上下文查询")
public class RagRetrievalController {

    private final RagRetrievalService ragRetrievalService;

    public RagRetrievalController(RagRetrievalService ragRetrievalService) {
        this.ragRetrievalService = ragRetrievalService;
    }

    @PostMapping("/retrievals")
    @Operation(summary = "检索知识上下文", description = "按查询文本返回可传给 AI Agent 的知识上下文")
    public ApiResponse<RagRetrievalRpcDTO> retrieve(@RequestBody RagRetrievalRpcRequest request) {
        return ApiResponse.success(ragRetrievalService.retrieve(request));
    }
}
