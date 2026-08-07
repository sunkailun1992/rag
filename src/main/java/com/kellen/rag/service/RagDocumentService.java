package com.kellen.rag.service;

import com.kellen.rag.entity.vo.RagDocumentIngestionVO;
import com.kellen.rag.entity.vo.RagDocumentVO;
import com.kellen.rag.entity.vo.RagDocumentValidationVO;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

/**
 * RAG 知识文档管理服务。
 */
public interface RagDocumentService {

    RagDocumentIngestionVO ingest(MultipartFile file, String knowledgeBase, String documentCode, String title);

    List<RagDocumentVO> list(String knowledgeBase);

    RagDocumentValidationVO validate(String documentId);

    boolean deactivate(String documentId);
}
