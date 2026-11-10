package com.kellen.rag.exception;

/** 同一提交键不能绑定不同执行输入；只提供固定安全消息。 */
public class GraphSubmissionConflictException extends RuntimeException {
    public GraphSubmissionConflictException() {
        super("图谱提交键已用于不同的构建输入，请先查回原任务");
    }
}
