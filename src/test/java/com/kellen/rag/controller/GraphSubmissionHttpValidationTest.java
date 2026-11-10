package com.kellen.rag.controller;

import com.kellen.bean.GlobalExceptionHandler;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.service.RagKnowledgeGraphService;
import com.kellen.rag.service.impl.DefaultRagKnowledgeGraphService;
import com.kellen.utils.context.TenantContextHolder;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

/** 通过真实HTTP与真实service参数守卫验证安全400；没有来源或数据库客户端。 */
@SpringBootTest(classes = {GraphBuildHttpIntegrationTest.TestApplication.class, GlobalExceptionHandler.class},
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "spring.config.name=graph-submission-http-isolated", "spring.cloud.nacos.config.enabled=false",
        "spring.cloud.nacos.config.import-check.enabled=false", "spring.cloud.nacos.discovery.enabled=false"})
class GraphSubmissionHttpValidationTest {
    @LocalServerPort private int port;
    @MockitoBean private RagKnowledgeGraphService graphs;
    private final HttpClient http = HttpClient.newHttpClient();

    @Test
    void shouldMapInvalidKeysToSafe400BeforeAnySourceOrEngineRead() throws Exception {
        var service = new DefaultRagKnowledgeGraphService(new RagVectorProperties(), null, null);
        when(graphs.sync(anyString(), anyString())).thenAnswer(call -> {
            TenantContextHolder.setTenantId("synthetic-tenant");
            try { return service.sync(call.getArgument(0), call.getArgument(1)); }
            finally { TenantContextHolder.clear(); }
        });
        when(graphs.submission(anyString(), anyString())).thenAnswer(call -> {
            TenantContextHolder.setTenantId("synthetic-tenant");
            try { return service.submission(call.getArgument(0), call.getArgument(1)); }
            finally { TenantContextHolder.clear(); }
        });
        for (String key : new String[]{"short", "   ", "x".repeat(129), "é".repeat(16)}) {
            var post = request("/api/rag/graphs/sync?knowledgeBase=kb-1").header("X-Graph-Submission-Key", key)
                    .POST(HttpRequest.BodyPublishers.noBody()).build();
            var response = http.send(post, HttpResponse.BodyHandlers.ofString());
            assertThat(response.statusCode()).isEqualTo(400);
            assertThat(response.body()).contains("图谱提交键格式不合法").doesNotContain(key.trim().isEmpty() ? "Exception" : key);
        }
        var get = request("/api/rag/graphs/submissions/" + URLEncoder.encode("非法提交键", StandardCharsets.UTF_8)
                + "?knowledgeBase=kb-1").GET().build();
        assertThat(http.send(get, HttpResponse.BodyHandlers.ofString()).statusCode()).isEqualTo(400);
        var denied = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port
                        + "/api/rag/graphs/sync?knowledgeBase=kb-1"))
                .header("Authorization", GraphBuildHttpIntegrationTest.basic("unrelated"))
                .header("X-Graph-Submission-Key", "short").POST(HttpRequest.BodyPublishers.noBody()).build();
        var response = http.send(denied, HttpResponse.BodyHandlers.ofString());
        assertThat(response.statusCode()).isEqualTo(403);
        assertThat(response.body()).doesNotContain("short", "格式不合法");
    }

    private HttpRequest.Builder request(String path) {
        return HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + path))
                .header("Authorization", GraphBuildHttpIntegrationTest.basic("manager"));
    }
}
