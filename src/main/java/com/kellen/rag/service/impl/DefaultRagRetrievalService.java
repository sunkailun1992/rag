package com.kellen.rag.service.impl;

import com.kellen.rag.service.RagRetrievalService;
import com.kellen.rpc.rag.RagRetrievalRpcDTO;
import com.kellen.rpc.rag.RagRetrievalRpcRequest;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.util.List;

/**
 * 当前阶段的 RAG 检索占位实现。
 *
 * <p>服务先发布稳定 RPC/HTTP 边界，真实 embedding、Qdrant 检索和知识库权限在后续
 * RAG 业务阶段接入。未命中或未启用时返回空上下文，调用方应按无资料处理。</p>
 */
@Service
public class DefaultRagRetrievalService implements RagRetrievalService {

    @Override
    public RagRetrievalRpcDTO retrieve(RagRetrievalRpcRequest request) {
        RagRetrievalRpcDTO response = new RagRetrievalRpcDTO();
        response.setEnabled(false);
        response.setMatched(false);
        response.setContext("");
        response.setSourceCount(0);
        response.setChunks(List.of());
        if (request == null || !StringUtils.hasText(request.getQuery())) {
            response.setMessage("query is blank");
            return response;
        }
        response.setMessage("RAG retrieval provider is wired; vector retrieval is not implemented yet");
        return response;
    }
}
