package com.kellen.rag.controller;

import com.kellen.rag.entity.vo.GraphBuildVO;
import com.kellen.rag.service.RagKnowledgeGraphService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.SpringBootConfiguration;
import org.springframework.boot.autoconfigure.ImportAutoConfiguration;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.core.userdetails.User;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.provisioning.InMemoryUserDetailsManager;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import tools.jackson.databind.ObjectMapper;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/** 真实监听端口验证新状态入口的路由、权限和 KB 参数；认证用户均为本地合成夹具。 */
@SpringBootTest(classes = GraphBuildHttpIntegrationTest.TestApplication.class,
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "spring.config.name=graph-build-http-isolated",
        "spring.cloud.nacos.config.enabled=false",
        "spring.cloud.nacos.config.import-check.enabled=false",
        "spring.cloud.nacos.discovery.enabled=false"
})
class GraphBuildHttpIntegrationTest {
    private static final String SECRET = UUID.randomUUID().toString();

    @SpringBootConfiguration
    @ImportAutoConfiguration({
            org.springframework.boot.tomcat.autoconfigure.servlet.TomcatServletWebServerAutoConfiguration.class,
            org.springframework.boot.webmvc.autoconfigure.DispatcherServletAutoConfiguration.class,
            org.springframework.boot.webmvc.autoconfigure.WebMvcAutoConfiguration.class,
            org.springframework.boot.jackson.autoconfigure.JacksonAutoConfiguration.class,
            org.springframework.boot.http.converter.autoconfigure.HttpMessageConvertersAutoConfiguration.class,
            org.springframework.boot.security.autoconfigure.SecurityAutoConfiguration.class,
            org.springframework.boot.security.autoconfigure.web.servlet.SecurityFilterAutoConfiguration.class
    })
    @EnableWebSecurity
    @EnableMethodSecurity
    @Import(RagKnowledgeGraphController.class)
    static class TestApplication {
        @Bean
        SecurityFilterChain security(HttpSecurity http) throws Exception {
            return http.csrf(csrf -> csrf.disable()).authorizeHttpRequests(request -> request.anyRequest().authenticated())
                    .httpBasic(Customizer.withDefaults()).build();
        }

        @Bean
        UserDetailsService users() {
            return new InMemoryUserDetailsManager(
                    User.withUsername("viewer").password("{noop}" + SECRET).authorities("rag:graph:view").build(),
                    User.withUsername("manager").password("{noop}" + SECRET).authorities("rag:graph:manage").build(),
                    User.withUsername("unrelated").password("{noop}" + SECRET).authorities("rag:document:manage").build());
        }
    }

    @LocalServerPort private int port;
    @MockitoBean private RagKnowledgeGraphService graphs;
    @Autowired private ObjectMapper mapper;
    private final HttpClient http = HttpClient.newHttpClient();

    @Test
    void shouldAllowViewerAndManagerAndReturnTaskAsynchronousState() throws Exception {
        when(graphs.build("build-1", "kb-1")).thenReturn(new GraphBuildVO("build-1", "kb-1", "QUEUED",
                2, 0, 0, 0, null, null, null));
        for (String user : new String[]{"viewer", "manager"}) {
            HttpResponse<String> response = get("/api/rag/graphs/builds/build-1?knowledgeBase=kb-1", user);
            assertThat(response.statusCode()).isEqualTo(200);
            var json = mapper.readTree(response.body());
            assertThat(json.path("data").path("status").asText()).isEqualTo("QUEUED");
            assertThat(json.path("data").path("processedDocumentCount").asInt()).isZero();
        }
        verify(graphs, org.mockito.Mockito.times(2)).build("build-1", "kb-1");
    }

    @Test
    void shouldRejectAnonymousAndUnrelatedPermissionBeforeService() throws Exception {
        assertThat(get("/api/rag/graphs/builds/build-1?knowledgeBase=kb-1", null).statusCode()).isEqualTo(401);
        assertThat(get("/api/rag/graphs/builds/build-1?knowledgeBase=kb-1", "unrelated").statusCode()).isEqualTo(403);
        verifyNoInteractions(graphs);
    }

    @Test
    void shouldRequireKnowledgeBaseBeforeService() throws Exception {
        assertThat(get("/api/rag/graphs/builds/build-1", "viewer").statusCode()).isEqualTo(400);
        verifyNoInteractions(graphs);
    }

    @Test
    void shouldProtectScopedSubmissionRecoveryRoute() throws Exception {
        String path = "/api/rag/graphs/submissions/synthetic-submission-0001?knowledgeBase=kb-1";
        assertThat(get(path, null).statusCode()).isEqualTo(401);
        assertThat(get(path, "unrelated").statusCode()).isEqualTo(403);
        var missing = get(path, "manager");
        assertThat(missing.statusCode()).isEqualTo(200);
        // ApiResponse空值字段可能按全局序列化配置省略；两种都表示无任务，不伪造build。
        var data = mapper.readTree(missing.body()).path("data");
        assertThat(data.isNull() || data.isMissingNode()).isTrue();
    }

    private HttpResponse<String> get(String path, String user) throws Exception {
        HttpRequest.Builder request = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + path)).GET();
        if (user != null) request.header("Authorization", basic(user));
        return http.send(request.build(), HttpResponse.BodyHandlers.ofString());
    }

    static String basic(String user) {
        return "Basic " + Base64.getEncoder().encodeToString((user + ":" + SECRET).getBytes(StandardCharsets.UTF_8));
    }
}
