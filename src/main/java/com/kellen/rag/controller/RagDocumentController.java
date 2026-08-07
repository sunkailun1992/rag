package com.kellen.rag.controller;

import com.kellen.rag.entity.vo.RagDocumentIngestionVO;
import com.kellen.rag.entity.vo.RagDocumentVO;
import com.kellen.rag.entity.vo.RagDocumentValidationVO;
import com.kellen.rag.service.RagDocumentService;
import com.kellen.utils.response.ApiResponse;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.http.MediaType;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

/**
 * RAG 知识文档上传、列表与向量校验入口。
 */
@RestController
@RequestMapping("/api/rag/documents")
@PreAuthorize("hasAuthority('rag:document:manage')")
@Tag(name = "RAG 文档", description = "租户隔离的知识文档摄取与向量读回校验")
public class RagDocumentController {

    private final RagDocumentService ragDocumentService;

    public RagDocumentController(RagDocumentService ragDocumentService) {
        this.ragDocumentService = ragDocumentService;
    }

    @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @Operation(summary = "上传知识文档", description = "当前 知识库仅支持 xlsx；相同文件指纹幂等，同文档编码新内容生成新版本")
    public ApiResponse<RagDocumentIngestionVO> upload(
            @RequestPart("file") MultipartFile file,
            @RequestParam(required = false) String knowledgeBase,
            @RequestParam(required = false) String documentCode,
            @RequestParam(required = false) String title) {
        return ApiResponse.success(ragDocumentService.ingest(file, knowledgeBase, documentCode, title));
    }

    @GetMapping
    @Operation(summary = "查询知识文档", description = "仅返回当前租户可见的文档摘要，不返回向量和租户主键")
    public ApiResponse<List<RagDocumentVO>> list(@RequestParam(required = false) String knowledgeBase) {
        return ApiResponse.success(ragDocumentService.list(knowledgeBase));
    }

    @PostMapping("/{documentId}/validation")
    @Operation(summary = "校验文档向量", description = "从 Qdrant 读回全部点，检查数量、内容 SHA-256 和向量维度")
    public ApiResponse<RagDocumentValidationVO> validate(@PathVariable String documentId) {
        return ApiResponse.success(ragDocumentService.validate(documentId));
    }

    @DeleteMapping("/{documentId}")
    @Operation(summary = "停用知识文档", description = "按当前租户和文档 ID 标记为 DELETED，不执行跨租户或物理删除")
    public ApiResponse<Boolean> deactivate(@PathVariable String documentId) {
        return ApiResponse.success(ragDocumentService.deactivate(documentId));
    }
}
