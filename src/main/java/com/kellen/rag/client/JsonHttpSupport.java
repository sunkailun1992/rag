package com.kellen.rag.client;

import com.kellen.rag.exception.ExternalServiceException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;

/**
 * 模型与向量库共用的最小 JSON HTTP 支持。
 */
abstract class JsonHttpSupport {

    protected final ObjectMapper objectMapper;
    private final HttpClient httpClient;
    private final String serviceName;

    protected JsonHttpSupport(ObjectMapper objectMapper, String serviceName) {
        this.objectMapper = objectMapper;
        this.serviceName = serviceName;
        this.httpClient = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();
    }

    protected JsonNode get(String url, Map<String, String> headers) {
        return send(HttpRequest.newBuilder(URI.create(url)).GET(), headers, 30);
    }

    protected JsonNode post(String url, Object body, Map<String, String> headers, int timeoutSeconds) {
        return send(jsonRequest(url, body).POST(HttpRequest.BodyPublishers.ofString(write(body))), headers, timeoutSeconds);
    }

    protected JsonNode put(String url, Object body, Map<String, String> headers, int timeoutSeconds) {
        return send(jsonRequest(url, body).PUT(HttpRequest.BodyPublishers.ofString(write(body))), headers, timeoutSeconds);
    }

    protected int status(String url, Map<String, String> headers) {
        try {
            HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(url)).GET().timeout(Duration.ofSeconds(15));
            headers.forEach(builder::header);
            return httpClient.send(builder.build(), HttpResponse.BodyHandlers.discarding()).statusCode();
        } catch (Exception exception) {
            throw ExternalServiceException.connectionFailure(serviceName, exception);
        }
    }

    private HttpRequest.Builder jsonRequest(String url, Object ignoredBody) {
        return HttpRequest.newBuilder(URI.create(url)).header("Content-Type", "application/json");
    }

    private JsonNode send(HttpRequest.Builder builder, Map<String, String> headers, int timeoutSeconds) {
        try {
            headers.forEach(builder::header);
            HttpResponse<String> response = httpClient.send(
                    builder.timeout(Duration.ofSeconds(timeoutSeconds)).build(), HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw ExternalServiceException.httpFailure(serviceName, response.statusCode());
            }
            return response.body() == null || response.body().isBlank()
                    ? objectMapper.createObjectNode()
                    : objectMapper.readTree(response.body());
        } catch (ExternalServiceException exception) {
            throw exception;
        } catch (Exception exception) {
            throw ExternalServiceException.connectionFailure(serviceName, exception);
        }
    }

    private String write(Object body) {
        try {
            return objectMapper.writeValueAsString(body);
        } catch (Exception exception) {
            throw new IllegalArgumentException("JSON 请求序列化失败", exception);
        }
    }
}
