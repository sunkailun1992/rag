package com.kellen.rag.client;

import java.util.List;

/**
 * 文本向量模型端口。
 */
public interface EmbeddingClient {

    List<float[]> embedDocuments(List<String> texts);

    float[] embedQuery(String query);
}
