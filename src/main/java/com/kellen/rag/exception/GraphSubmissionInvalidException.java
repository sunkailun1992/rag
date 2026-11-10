package com.kellen.rag.exception;

/** 不回显不透明键的受控参数错误。 */
public class GraphSubmissionInvalidException extends IllegalArgumentException {
    public GraphSubmissionInvalidException() {
        super("图谱提交键格式不合法");
    }
}
