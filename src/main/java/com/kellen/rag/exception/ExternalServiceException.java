package com.kellen.rag.exception;

/**
 * RAG 调用外部模型或向量存储失败。
 *
 * <p>异常只保留可安全返回给前端的服务名和 HTTP 状态，不携带请求地址、密钥或响应正文。</p>
 */
public class ExternalServiceException extends RuntimeException {

    private final String serviceName;
    private final Integer statusCode;
    private final String publicMessage;

    private ExternalServiceException(String serviceName, Integer statusCode,
                                     String publicMessage, Throwable cause) {
        super(publicMessage, cause);
        this.serviceName = serviceName;
        this.statusCode = statusCode;
        this.publicMessage = publicMessage;
    }

    public static ExternalServiceException httpFailure(String serviceName, int statusCode) {
        String message;
        if ("Qdrant".equals(serviceName) && statusCode == 404) {
            message = "Qdrant collection 不存在或配置不匹配";
        } else {
            message = serviceName + " 请求失败，HTTP " + statusCode;
        }
        return new ExternalServiceException(serviceName, statusCode, message, null);
    }

    public static ExternalServiceException connectionFailure(String serviceName, Throwable cause) {
        return new ExternalServiceException(serviceName, null,
                serviceName + " 连接失败，请检查服务地址和网络", cause);
    }

    public String getServiceName() {
        return serviceName;
    }

    public Integer getStatusCode() {
        return statusCode;
    }

    public String getPublicMessage() {
        return publicMessage;
    }
}
