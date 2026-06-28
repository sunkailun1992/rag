package com.kellen.rag.rpc;

import com.kellen.rag.service.RagRetrievalService;
import com.kellen.rpc.rag.RagRetrievalRpcDTO;
import com.kellen.rpc.rag.RagRetrievalRpcRequest;
import com.kellen.rpc.rag.RagRetrievalRpcService;
import org.apache.dubbo.config.annotation.DubboService;

/**
 * RAG 知识检索 Dubbo RPC 服务实现。
 */
@DubboService
public class RagRetrievalRpcServiceImpl implements RagRetrievalRpcService {

    private final RagRetrievalService ragRetrievalService;

    public RagRetrievalRpcServiceImpl(RagRetrievalService ragRetrievalService) {
        this.ragRetrievalService = ragRetrievalService;
    }

    @Override
    public RagRetrievalRpcDTO retrieve(RagRetrievalRpcRequest request) {
        return ragRetrievalService.retrieve(request);
    }
}
