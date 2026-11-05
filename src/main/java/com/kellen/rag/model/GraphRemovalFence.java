package com.kellen.rag.model;

/** 仅供向量删除失败补偿使用的内部屏障票据，不能返回前端或用于普通同步。 */
public record GraphRemovalFence(String documentId, long barrierEpoch, long documentRevision) {
}
