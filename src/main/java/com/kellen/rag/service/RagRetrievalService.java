package com.kellen.rag.service;

import com.kellen.rpc.rag.RagRetrievalRpcDTO;
import com.kellen.rpc.rag.RagRetrievalRpcRequest;

/**
 * RAG 知识检索领域服务。
 */
public interface RagRetrievalService {

    /**
     * 检索可传给调用方 Agent 的知识上下文。
     *
     * @param request 检索请求
     * @return 检索结果
     */
    RagRetrievalRpcDTO retrieve(RagRetrievalRpcRequest request);

    /**
     * 灰度 RPC 检索要求引擎提供完整结果；检索不可用时终止调用。
     */
    RagRetrievalRpcDTO retrieveCanary(RagRetrievalRpcRequest request);
}
