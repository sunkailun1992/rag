package com.kellen.rag.rpc;

import com.kellen.rag.exception.ExternalServiceException;
import com.kellen.rag.service.RagRetrievalService;
import com.kellen.rpc.rag.RagRetrievalRpcDTO;
import com.kellen.rpc.rag.RagRetrievalRpcRequest;
import com.kellen.rpc.rag.RagRetrievalRpcService;
import com.kellen.traffic.CanaryContext;
import org.apache.dubbo.config.annotation.DubboService;
import org.apache.dubbo.rpc.RpcException;

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
        if (CanaryContext.get() == null) {
            return ragRetrievalService.retrieve(request);
        }
        try {
            return ragRetrievalService.retrieveCanary(request);
        } catch (ExternalServiceException unavailable) {
            throw new RpcException("RAG_CANARY_ENGINE_UNAVAILABLE");
        }
    }
}
