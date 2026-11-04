package com.kellen.rag.client;

import com.kellen.rag.model.StoredVectorPoint;
import com.kellen.rag.model.VectorPoint;
import com.kellen.rag.model.VectorSearchHit;

import java.util.List;
import java.util.Map;

/**
 * Qdrant 向量存储端口。
 */
public interface VectorStoreClient {

    boolean collectionExists();

    void ensureCollection();

    void upsert(List<VectorPoint> points);

    List<StoredVectorPoint> scroll(Map<String, Object> exactFilter, int limit, boolean withVector);

    default com.kellen.rag.model.VectorPointPage scrollPage(Map<String, Object> exactFilter,
                                                            int limit, String offset) {
        throw new UnsupportedOperationException("向量存储未实现分页查询");
    }

    List<VectorSearchHit> search(float[] vector, Map<String, Object> exactFilter,
                                 int limit, double scoreThreshold);

    void setPayload(Map<String, Object> exactFilter, Map<String, Object> payload);

    long count(Map<String, Object> exactFilter);
}
