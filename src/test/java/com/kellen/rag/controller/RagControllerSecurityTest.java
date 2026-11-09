package com.kellen.rag.controller;

import com.kellen.bean.GlobalExceptionHandler;
import com.kellen.rag.exception.ExternalServiceException;
import com.kellen.rag.service.RagDocumentService;
import com.kellen.rag.service.RagKnowledgeGraphService;
import com.kellen.rag.service.RagRetrievalService;
import com.kellen.rpc.rag.RagRetrievalRpcDTO;
import com.kellen.security.config.SecurityAuthConfig;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.SpringBootConfiguration;
import org.springframework.boot.autoconfigure.EnableAutoConfiguration;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.test.context.ContextConfiguration;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.user;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * RAG HTTP 接口权限边界测试。
 * 独立配置名避免读取运行时 Nacos import 和本机私有引导，业务服务由 MockitoBean 提供。
 */
@WebMvcTest(controllers = {RagDocumentController.class, RagRetrievalController.class,
        RagKnowledgeGraphController.class}, properties = {
        "spring.config.name=rag-controller-isolated",
        "spring.cloud.nacos.config.enabled=false",
        "spring.cloud.nacos.config.import-check.enabled=false",
        "spring.cloud.nacos.discovery.enabled=false"
})
@Import({SecurityAuthConfig.class, GlobalExceptionHandler.class})
@ContextConfiguration(classes = {
        RagDocumentController.class,
        RagRetrievalController.class,
        RagKnowledgeGraphController.class,
        SecurityAuthConfig.class,
        GlobalExceptionHandler.class,
        RagControllerSecurityTest.TestApplication.class
})
class RagControllerSecurityTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private RagDocumentService ragDocumentService;

    @MockitoBean
    private RagRetrievalService ragRetrievalService;

    @MockitoBean
    private RagKnowledgeGraphService ragKnowledgeGraphService;

    @SpringBootConfiguration
    @EnableAutoConfiguration
    static class TestApplication {
    }

    @Test
    void shouldAllowDocumentListWithRagDocumentPermission() throws Exception {
        when(ragDocumentService.list(null)).thenReturn(List.of());

        mockMvc.perform(get("/api/rag/documents")
                        .with(user("rag-admin").authorities(() -> "rag:document:manage")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true));
    }

    @Test
    void shouldRejectDocumentListWithOnlyUserManagePermission() throws Exception {
        mockMvc.perform(get("/api/rag/documents")
                        .with(user("user-admin").authorities(() -> "user:auth:manage")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.success").value(false))
                .andExpect(jsonPath("$.code").value("A0312"));

        verifyNoInteractions(ragDocumentService);
    }

    @Test
    void shouldReturnStructuredBadGatewayWhenQdrantRequestFails() throws Exception {
        when(ragDocumentService.list(null))
                .thenThrow(ExternalServiceException.httpFailure("Qdrant", 404));

        mockMvc.perform(get("/api/rag/documents")
                        .with(user("rag-admin").authorities(() -> "rag:document:manage")))
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.success").value(false))
                .andExpect(jsonPath("$.code").value("C0100"))
                .andExpect(jsonPath("$.errorMessage")
                        .value("Qdrant collection 不存在或配置不匹配"));
    }

    @Test
    void shouldProtectChunkPreviewWithDocumentPermission() throws Exception {
        mockMvc.perform(get("/api/rag/documents/doc-1/chunks"))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value("A0312"));
        mockMvc.perform(get("/api/rag/documents/doc-1/chunks")
                        .with(user("viewer").authorities(() -> "rag:retrieval:use")))
                .andExpect(status().isForbidden());
        verifyNoInteractions(ragDocumentService);
        when(ragDocumentService.chunks("doc-1",20,null)).thenReturn(
                new com.kellen.rag.entity.vo.RagChunkPageVO("doc-1",List.of(),null));
        mockMvc.perform(get("/api/rag/documents/doc-1/chunks")
                        .with(user("admin").authorities(() -> "rag:document:manage")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.documentId").value("doc-1"));
    }

    @Test
    void shouldAllowRetrievalWithRagRetrievalPermission() throws Exception {
        when(ragRetrievalService.retrieve(org.mockito.ArgumentMatchers.any()))
                .thenReturn(new RagRetrievalRpcDTO());

        mockMvc.perform(post("/api/rag/retrievals")
                        .with(user("rag-client").authorities(() -> "rag:retrieval:use"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "query": "结构化知识查询"
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true));
    }

    @Test
    void shouldRejectRetrievalWithoutRagRetrievalPermission() throws Exception {
        mockMvc.perform(post("/api/rag/retrievals")
                        .with(user("rag-client").authorities(() -> "rag:document:manage"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "query": "结构化知识查询"
                                }
                                """))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.success").value(false))
                .andExpect(jsonPath("$.code").value("A0312"));

        verifyNoInteractions(ragRetrievalService);
    }

    /** 真实 MVC/Jackson 绑定失败必须在进入检索服务前返回固定、脱敏的客户端错误。 */
    @ParameterizedTest
    @ValueSource(strings = {"{\"query\":\"SYNTHETIC_JSON_BODY_NOT_FOR_LOGS", "{\"query\":", "[]"})
    void shouldReturnBadRequestForUnreadableRetrievalJsonWithoutCallingService(String body) throws Exception {
        var logger = (ch.qos.logback.classic.Logger) org.slf4j.LoggerFactory.getLogger(GlobalExceptionHandler.class);
        var captured = new ch.qos.logback.core.read.ListAppender<ch.qos.logback.classic.spi.ILoggingEvent>();
        captured.start();
        logger.addAppender(captured);
        try {
            var result = mockMvc.perform(post("/api/rag/retrievals")
                            .with(user("rag-client").authorities(() -> "rag:retrieval:use"))
                            .contentType(MediaType.APPLICATION_JSON).content(body))
                    .andReturn();
            // 先确认生产MVC实际抛出的外层异常，不能只根据最深层JSON解析cause猜映射。
            assertThat(result.getResolvedException()).isExactlyInstanceOf(HttpMessageNotReadableException.class);
            assertThat(result.getResponse().getStatus()).isEqualTo(400);
            var json = tools.jackson.databind.json.JsonMapper.builder().build()
                    .readTree(result.getResponse().getContentAsString());
            assertThat(json.path("success").asBoolean()).isFalse();
            assertThat(json.path("code").asString()).isEqualTo("A0400");
            assertThat(json.path("errorMessage").asString()).isEqualTo("请求体 JSON 格式不正确");
            assertThat(result.getResponse().getContentAsString())
                    .doesNotContain("SYNTHETIC_JSON_BODY", "UnexpectedEndOfInputException", "HttpMessageNotReadableException");
            assertThat(captured.list).isNotEmpty().allSatisfy(event -> {
                assertThat(event.getThrowableProxy()).isNull();
                assertThat(event.getFormattedMessage()).doesNotContain("SYNTHETIC_JSON_BODY", body);
                assertThat(event.getArgumentArray()).isNullOrEmpty();
            });
            verifyNoInteractions(ragRetrievalService);
        } finally {
            logger.detachAppender(captured);
            captured.stop();
        }
    }

    @Test
    void shouldKeepUnexpectedRetrievalFailuresAsSafeServerErrors() throws Exception {
        when(ragRetrievalService.retrieve(org.mockito.ArgumentMatchers.any()))
                .thenThrow(new IllegalStateException("SYNTHETIC_INTERNAL_ONLY"));
        var result = mockMvc.perform(post("/api/rag/retrievals")
                        .with(user("rag-client").authorities(() -> "rag:retrieval:use"))
                        .contentType(MediaType.APPLICATION_JSON).content("{\"query\":\"synthetic\"}"))
                .andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.success").value(false))
                .andExpect(jsonPath("$.errorMessage").value("系统处理失败，请查看服务日志"))
                .andReturn();
        assertThat(result.getResponse().getContentAsString()).doesNotContain("SYNTHETIC_INTERNAL_ONLY");
    }

    @Test
    void shouldAllowGraphQueryWithViewPermission() throws Exception {
        when(ragKnowledgeGraphService.query(null, null, null, null, null))
                .thenReturn(new com.kellen.rag.entity.vo.RagKnowledgeGraphVO(
                        "general-knowledge", "OVERVIEW", 0, 0, false,
                        java.util.Map.of(), java.util.Map.of(), List.of(), List.of()));

        mockMvc.perform(get("/api/rag/graphs")
                        .with(user("rag-viewer").authorities(() -> "rag:graph:view")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true));
    }

    @Test
    void shouldRejectGraphQueryWithOnlyDocumentPermission() throws Exception {
        mockMvc.perform(get("/api/rag/graphs")
                        .with(user("rag-admin").authorities(() -> "rag:document:manage")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("A0312"));

        verifyNoInteractions(ragKnowledgeGraphService);
    }

    @Test
    void shouldRequireManagePermissionForGraphSync() throws Exception {
        mockMvc.perform(post("/api/rag/graphs/sync")
                        .with(user("rag-viewer").authorities(() -> "rag:graph:view")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("A0312"));

        verifyNoInteractions(ragKnowledgeGraphService);
    }

    @Test
    void shouldAllowGraphSyncWithManagePermission() throws Exception {
        when(ragKnowledgeGraphService.sync(null))
                .thenReturn(new com.kellen.rag.entity.vo.RagGraphSyncVO("general-knowledge", 1, 12, 18));

        mockMvc.perform(post("/api/rag/graphs/sync")
                        .with(user("rag-admin").authorities(() -> "rag:graph:manage")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.documentCount").value(1));
    }
}
