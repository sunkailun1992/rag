package com.kellen.rag.rpc;

import com.kellen.rag.exception.ExternalServiceException;
import com.kellen.rag.service.RagRetrievalService;
import com.kellen.rpc.rag.RagRetrievalRpcDTO;
import com.kellen.rpc.rag.RagRetrievalRpcRequest;
import com.kellen.rpc.rag.RagRetrievalRpcService;
import com.kellen.traffic.CanaryContext;
import org.apache.dubbo.config.annotation.DubboService;
import org.apache.dubbo.config.annotation.Method;
import org.apache.dubbo.rpc.RpcException;

/**
 * RAG 知识检索 Dubbo RPC 服务实现。
 *
 * <p>{@code retrieve} 声明为幂等（{@code idempotent}）：它只向检索引擎查询，不写任何数据。utils 2.1.1 起的 Dubbo 调用端
 * 统一重试据此在请求发出后的连接类失败时重试它。重试会再调一次检索引擎，引擎里的向量化与重排模型也随之再调一次，
 * 只多产生模型调用费用（第二层最多再试 2 次）。</p>
 */
@DubboService(methods = @Method(name = "retrieve", parameters = {"idempotent", "true"}))
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
