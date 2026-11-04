package com.kellen.rag.client;

import com.kellen.rag.config.RagEngineProperties;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.exception.ExternalServiceException;
import com.kellen.rag.model.KnowledgeChunkDraft;
import com.kellen.rag.model.ParsedKnowledgeDocument;
import com.kellen.rag.model.VectorSearchHit;
import com.kellen.utils.context.TenantContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.io.ByteArrayOutputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.Flow;
import java.util.concurrent.TimeUnit;

/**
 * RAG 引擎专属 HTTP 适配器。凭据、租户来自服务器配置与认证上下文，
 * 响应体有大小和总时间上限；返回内容再次核对租户，不能信任上游过滤一定正确。
 */
@Component
public class RagEngineClient {
    private final RagEngineProperties properties;
    private final RagVectorProperties vectorProperties;
    private final ObjectMapper mapper;
    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofMillis(1000)).followRedirects(HttpClient.Redirect.NEVER).build();

    public RagEngineClient(RagEngineProperties properties, RagVectorProperties vectorProperties,
                           ObjectMapper mapper) {
        this.properties = properties;
        this.vectorProperties = vectorProperties;
        this.mapper = mapper;
    }

    public boolean isParseEnabled() { return properties.isParseEnabled(); }
    public boolean isRetrievalEnabled() { return properties.isRetrievalEnabled(); }

    /** 只转发新格式，保留经 Java 规范化后的文件名及页块定位。 */
    public ParsedKnowledgeDocument parse(String sourceName, String title, String extension, byte[] bytes) {
        if (!isParseEnabled()) {
            throw new IllegalArgumentException("PDF/图片解析尚未启用，请先配置 RAG 引擎");
        }
        JsonNode result = request("/v1/parses", Map.of("sourceName", sourceName, "title", title,
                "contentBase64", Base64.getEncoder().encodeToString(bytes),
                "maxChunkCharacters", vectorProperties.getMaxChunkCharacters()),
                bounded(properties.getParseTimeoutMillis(), 1000, 120000));
        JsonNode chunks = result.path("chunks");
        require(chunks.isArray() && !chunks.isEmpty() && chunks.size() <= properties.getMaxChunks());
        List<KnowledgeChunkDraft> parsed = new ArrayList<>();
        for (JsonNode chunk : chunks) {
            String content = requiredText(chunk, "content", vectorProperties.getMaxChunkCharacters());
            Map<String, Object> metadata = new LinkedHashMap<>();
            JsonNode source = chunk.path("metadata");
            require(source.isObject());
            JsonNode page = source.path("page");
            require(page.isIntegralNumber() && page.canConvertToInt()
                    && page.asInt() > 0 && page.asInt() <= 10000);
            metadata.put("page", page.asInt());
            metadata.put("block_type", requiredText(source, "block_type", 128));
            JsonNode capabilities = source.path("ocr_capabilities");
            if (!capabilities.isMissingNode() && !capabilities.isNull()) {
                require(capabilities.isObject());
                Map<String, Boolean> flags = new LinkedHashMap<>();
                for (String name : List.of("textRecognition", "tableStructure", "layoutAnalysis")) {
                    require(capabilities.path(name).isBoolean());
                    flags.put(name, capabilities.path(name).asBoolean());
                }
                metadata.put("ocr_capabilities", Map.copyOf(flags));
            }
            metadata.put("source_name", sourceName);
            JsonNode bbox = source.path("bbox");
            if (!bbox.isMissingNode() && !bbox.isNull()) {
                require(bbox.isArray() && bbox.size() == 4);
                List<Double> coordinates = new ArrayList<>();
                for (JsonNode coordinate : bbox) {
                    require(coordinate.isNumber() && Double.isFinite(coordinate.asDouble())
                            && coordinate.asDouble() >= 0);
                    coordinates.add(coordinate.asDouble());
                }
                require(coordinates.get(2) >= coordinates.get(0) && coordinates.get(3) >= coordinates.get(1));
                metadata.put("bbox", List.copyOf(coordinates));
            }
            parsed.add(new KnowledgeChunkDraft(requiredText(chunk, "chunkType", 128),
                    requiredText(chunk, "sectionPath", 1000), requiredText(chunk, "locator", 1000),
                    content, metadata));
        }
        return new ParsedKnowledgeDocument(title, sourceName, extension, List.copyOf(parsed));
    }

    /** 查询引擎只接受当前租户；响应跨租户或跨知识库时整体拒绝。 */
    public List<VectorSearchHit> retrieve(String query, String knowledgeBase, int topK, int candidates,
                                         double threshold, int contextLimit) {
        String tenant = requireTenant();
        JsonNode response = request("/v1/retrievals", Map.of("query", query, "knowledgeBase", knowledgeBase,
                "topK", topK, "candidateCount", candidates, "similarityThreshold", threshold,
                "maxContextCharacters", contextLimit), bounded(properties.getRetrievalTimeoutMillis(), 100, 3500));
        // 只有明确的 dense/hybrid 模式可进入原有 RPC 契约；隔离校验对两种模式完全相同。
        require(Set.of("dense", "hybrid").contains(response.path("mode").asText()));
        JsonNode hits = response.path("hits");
        require(hits.isArray() && hits.size() <= topK);
        List<VectorSearchHit> output = new ArrayList<>();
        Set<String> ids = new HashSet<>();
        for (JsonNode hit : hits) {
            JsonNode payload = hit.path("payload");
            require(tenant.equals(payload.path("tenant_id").asText())
                    && knowledgeBase.equals(payload.path("knowledge_base").asText())
                    && "ACTIVE".equals(payload.path("document_status").asText()));
            JsonNode dimension = payload.path("embedding_dimension");
            require(vectorProperties.getEmbeddingModel().equals(payload.path("embedding_model").asText())
                    && dimension.isIntegralNumber() && dimension.canConvertToInt()
                    && vectorProperties.getEmbeddingDimension() == dimension.asInt());
            String id = requiredText(hit, "id", 128);
            require(ids.add(id) && hit.path("score").isNumber() && Double.isFinite(hit.path("score").asDouble()));
            requiredText(payload, "content", vectorProperties.getMaxChunkCharacters());
            for (String field : List.of("document_id", "chunk_id", "document_title", "source_name", "content_sha256")) {
                requiredText(payload, field, 1000);
            }
            @SuppressWarnings("unchecked")
            Map<String, Object> data = mapper.convertValue(payload, Map.class);
            output.add(new VectorSearchHit(id, hit.path("score").asDouble(), data));
        }
        return List.copyOf(output);
    }

    private JsonNode request(String path, Object body, int timeoutMillis) {
        CompletableFuture<HttpResponse<byte[]>> pending = null;
        try {
            String tenant = requireTenant();
            if (!StringUtils.hasText(properties.getApiKey()) || properties.getApiKey().length() < 32) {
                throw new IllegalStateException("RAG 引擎需要至少 32 字符的服务密钥");
            }
            URI base = URI.create(properties.getBaseUrl());
            require(Set.of("http", "https").contains(base.getScheme()) && base.getHost() != null
                    && base.getUserInfo() == null && base.getQuery() == null && base.getFragment() == null
                    && (base.getPath().isEmpty() || base.getPath().equals("/")));
            HttpRequest request = HttpRequest.newBuilder(base.resolve(path))
                    .timeout(Duration.ofMillis(timeoutMillis)).header("Content-Type", "application/json")
                    .header("Authorization", "Bearer " + properties.getApiKey()).header("X-Tenant-Id", tenant)
                    .POST(HttpRequest.BodyPublishers.ofString(mapper.writeValueAsString(body))).build();
            pending = http.sendAsync(request, info -> new LimitedBody(bounded(properties.getMaxResponseBytes(),
                    1024, 32 * 1024 * 1024)));
            HttpResponse<byte[]> response = pending.get(timeoutMillis, TimeUnit.MILLISECONDS);
            if (response.statusCode() != 200) {
                if (path.equals("/v1/parses") && Set.of(400, 413, 422).contains(response.statusCode())) {
                    throw new IllegalArgumentException("PDF/图片无法解析或超过限制，请检查文件格式、页数及大小");
                }
                throw ExternalServiceException.httpFailure("RAG engine", response.statusCode());
            }
            return mapper.readTree(response.body());
        } catch (ExternalServiceException exception) {
            throw exception;
        } catch (IllegalArgumentException exception) {
            if ("PDF/图片无法解析或超过限制，请检查文件格式、页数及大小".equals(exception.getMessage())) throw exception;
            // URI/认证头构建异常可能包含私有配置原文，不转发JDK异常消息或cause。
            throw new IllegalArgumentException("RAG 引擎配置或请求不合法");
        } catch (Exception exception) {
            if (exception instanceof InterruptedException) Thread.currentThread().interrupt();
            // 不把上游 URL、响应正文或认证头带入异常日志。
            throw ExternalServiceException.connectionFailure("RAG engine", null);
        } finally {
            if (pending != null && !pending.isDone()) pending.cancel(true);
        }
    }

    private String requireTenant() {
        String tenant = TenantContextHolder.getTenantId();
        if (tenant == null || !tenant.matches("[A-Za-z0-9_-]{1,64}")) {
            throw new IllegalArgumentException("缺少合法租户上下文");
        }
        return tenant;
    }

    private static String requiredText(JsonNode node, String key, int maximum) {
        JsonNode value = node.path(key);
        require(value.isTextual() && StringUtils.hasText(value.asText())
                && value.asText().codePointCount(0, value.asText().length()) <= maximum);
        return value.asText();
    }

    private static void require(boolean valid) {
        if (!valid) throw ExternalServiceException.httpFailure("RAG engine response", 502);
    }

    private static int bounded(int value, int minimum, int maximum) {
        return Math.max(minimum, Math.min(value, maximum));
    }

    /** 收到超限数据立即取消订阅，不能先把完整恶意响应读入内存再校验。 */
    private static final class LimitedBody implements HttpResponse.BodySubscriber<byte[]> {
        private final int maximum;
        private final ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        private final CompletableFuture<byte[]> body = new CompletableFuture<>();
        private Flow.Subscription subscription;
        private LimitedBody(int maximum) { this.maximum = maximum; }
        @Override public CompletionStage<byte[]> getBody() { return body; }
        @Override public void onSubscribe(Flow.Subscription value) { subscription = value; value.request(1); }
        @Override public void onNext(List<ByteBuffer> buffers) {
            for (ByteBuffer buffer : buffers) {
                if (buffer.remaining() > maximum - bytes.size()) {
                    subscription.cancel(); body.completeExceptionally(new IllegalStateException("response too large")); return;
                }
                byte[] chunk = new byte[buffer.remaining()]; buffer.get(chunk); bytes.writeBytes(chunk);
            }
            subscription.request(1);
        }
        @Override public void onError(Throwable error) { body.completeExceptionally(error); }
        @Override public void onComplete() { body.complete(bytes.toByteArray()); }
    }
}
