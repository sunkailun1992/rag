package com.kellen.rag.client;

import com.kellen.rag.config.RagEngineProperties;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.security.oauth2.client.FleetOAuth2ClientAutoConfiguration;
import com.kellen.security.oauth2.client.FleetOAuth2Clients;
import com.kellen.utils.context.TenantContextHolder;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.ImportAutoConfiguration;
import org.springframework.boot.autoconfigure.context.ConfigurationPropertiesAutoConfiguration;
import org.springframework.boot.autoconfigure.context.PropertyPlaceholderAutoConfiguration;
import org.springframework.boot.security.oauth2.client.autoconfigure.OAuth2ClientAutoConfiguration;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.oauth2.client.OAuth2AuthorizedClientService;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import tools.jackson.databind.ObjectMapper;

import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executors;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * rag 调 RAG 引擎带客户端凭证令牌（客户端 github-rag，受众 github-rag-engine）：真实 OAuth2 客户端自动配置与 utils 出站工具，
 * Keycloak 令牌端点与引擎是本机回环替身。没取到令牌时不调用引擎。
 */
@SpringBootTest(classes = RagEngineClientAuthTest.Config.class, webEnvironment = SpringBootTest.WebEnvironment.NONE,
        properties = {"spring.config.name=rag-engine-auth-isolated", "spring.cloud.nacos.config.enabled=false",
                "spring.cloud.nacos.discovery.enabled=false", "spring.cloud.nacos.config.import-check.enabled=false",
                "spring.security.oauth2.client.registration.github-rag-engine.provider=keycloak",
                "spring.security.oauth2.client.registration.github-rag-engine.client-id=github-rag",
                "spring.security.oauth2.client.registration.github-rag-engine.client-secret=fixture-rag-secret",
                "spring.security.oauth2.client.registration.github-rag-engine.authorization-grant-type=client_credentials"})
class RagEngineClientAuthTest {

    private static final String TOKEN_PATH = "/realms/github-dev/protocol/openid-connect/token";

    static final List<Map<String, String>> TOKEN_REQUESTS = new CopyOnWriteArrayList<>();

    static final List<Map<String, String>> ENGINE_REQUESTS = new CopyOnWriteArrayList<>();

    static volatile boolean rejectTokenRequests;

    static final HttpServer SERVER = server();

    @Autowired
    FleetOAuth2Clients clients;

    @Autowired
    OAuth2AuthorizedClientService authorizedClients;

    @DynamicPropertySource
    static void endpoints(DynamicPropertyRegistry registry) {
        registry.add("spring.security.oauth2.client.provider.keycloak.token-uri",
                () -> "http://127.0.0.1:" + SERVER.getAddress().getPort() + TOKEN_PATH);
    }

    @AfterAll
    static void stop() {
        SERVER.stop(0);
    }

    @BeforeEach
    void reset() {
        TOKEN_REQUESTS.clear();
        ENGINE_REQUESTS.clear();
        rejectTokenRequests = false;
        authorizedClients.removeAuthorizedClient("github-rag-engine", "oauth2-client:github-rag-engine");
        TenantContextHolder.setTenantId("t1");
    }

    @AfterEach
    void clear() {
        TenantContextHolder.clear();
    }

    @Test
    void engineCallCarriesClientCredentialsToken() {
        RagEngineClient engine = client();
        assertThat(engine.retrieve("synthetic question", "kb", 3, 10, 0.0, 2000)).isEmpty();
        assertThat(engine.retrieve("synthetic question", "kb", 3, 10, 0.0, 2000)).isEmpty();
        // 客户端凭证令牌在请求间共享，到期前只申请一次。
        assertThat(TOKEN_REQUESTS).singleElement().satisfies(request -> assertThat(request)
                .containsEntry("grant_type", "client_credentials")
                .containsEntry("authorization", "Basic " + Base64.getEncoder().encodeToString("github-rag:fixture-rag-secret".getBytes(StandardCharsets.UTF_8))));
        assertThat(ENGINE_REQUESTS).hasSize(2).allSatisfy(request -> {
            assertThat(request).containsEntry("authorization", "Bearer cc-engine-token").containsEntry("x-tenant-id", "t1");
            assertThat(request.keySet()).noneMatch(name -> name.startsWith("x-api-key"));
        });
    }

    @Test
    void tokenEndpointFailureNeverCallsTheEngine() {
        rejectTokenRequests = true;
        assertThatThrownBy(() -> client().retrieve("synthetic question", "kb", 3, 10, 0.0, 2000)).isInstanceOf(RuntimeException.class)
                .hasMessageNotContaining("fixture-rag-secret");
        assertThat(TOKEN_REQUESTS).hasSize(1);
        assertThat(ENGINE_REQUESTS).isEmpty();
    }

    private RagEngineClient client() {
        RagEngineProperties properties = new RagEngineProperties();
        properties.setBaseUrl("http://127.0.0.1:" + SERVER.getAddress().getPort());
        properties.setRetrievalTimeoutMillis(3000);
        properties.setMaxResponseBytes(1024 * 1024);
        RagVectorProperties vector = new RagVectorProperties();
        vector.setEnabled(true);
        vector.setDefaultKnowledgeBase("kb");
        return new RagEngineClient(properties, vector, new ObjectMapper(), clients);
    }

    private static HttpServer server() {
        try {
            HttpServer server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
            server.setExecutor(Executors.newCachedThreadPool());
            server.createContext(TOKEN_PATH, exchange -> {
                Map<String, String> form = form(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
                form.put("authorization", exchange.getRequestHeaders().getFirst("Authorization"));
                TOKEN_REQUESTS.add(form);
                boolean reject = rejectTokenRequests;
                byte[] body = (reject ? "{\"error\":\"unauthorized_client\",\"error_description\":\"synthetic rejection\"}"
                        : "{\"access_token\":\"cc-engine-token\",\"token_type\":\"Bearer\",\"expires_in\":300}").getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().set("Content-Type", "application/json");
                exchange.sendResponseHeaders(reject ? 401 : 200, body.length);
                try (OutputStream output = exchange.getResponseBody()) {
                    output.write(body);
                }
            });
            server.createContext("/v1/", exchange -> {
                exchange.getRequestBody().readAllBytes();
                Map<String, String> headers = new LinkedHashMap<>();
                exchange.getRequestHeaders().forEach((name, values) -> headers.put(name.toLowerCase(), String.join(",", values)));
                ENGINE_REQUESTS.add(headers);
                byte[] body = "{\"mode\":\"dense\",\"degraded\":false,\"hits\":[]}".getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().set("Content-Type", "application/json");
                exchange.sendResponseHeaders(200, body.length);
                try (OutputStream output = exchange.getResponseBody()) {
                    output.write(body);
                }
            });
            server.start();
            return server;
        } catch (java.io.IOException e) {
            throw new IllegalStateException(e);
        }
    }

    private static Map<String, String> form(String body) {
        Map<String, String> values = new LinkedHashMap<>();
        for (String pair : body.split("&")) {
            if (!pair.isEmpty()) {
                String[] parts = pair.split("=", 2);
                values.put(URLDecoder.decode(parts[0], StandardCharsets.UTF_8), parts.length > 1 ? URLDecoder.decode(parts[1], StandardCharsets.UTF_8) : "");
            }
        }
        return values;
    }

    /**
     * 测试上下文：Spring Boot 的 OAuth2 客户端注册与 utils 的出站工具。
     */
    @Configuration(proxyBeanMethods = false)
    @ImportAutoConfiguration({PropertyPlaceholderAutoConfiguration.class, ConfigurationPropertiesAutoConfiguration.class,
            OAuth2ClientAutoConfiguration.class, FleetOAuth2ClientAutoConfiguration.class})
    static class Config {
    }
}
