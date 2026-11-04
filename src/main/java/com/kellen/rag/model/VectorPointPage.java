package com.kellen.rag.model;

import java.util.List;

/** 单次有界 scroll 的结果；游标只用于继续原租户/文档过滤后的查询。 */
public record VectorPointPage(List<StoredVectorPoint> points, String nextOffset) { }
