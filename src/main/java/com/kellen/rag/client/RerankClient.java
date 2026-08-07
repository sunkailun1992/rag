package com.kellen.rag.client;

import com.kellen.rag.model.RerankResult;

import java.util.List;

/**
 * 检索候选重排模型端口。
 */
public interface RerankClient {

    List<RerankResult> rerank(String query, List<String> documents, int topN);
}
