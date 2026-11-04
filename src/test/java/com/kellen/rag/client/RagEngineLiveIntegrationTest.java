package com.kellen.rag.client;

import com.kellen.rag.config.RagEngineProperties;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.exception.ExternalServiceException;
import com.kellen.rag.parser.KnowledgeDocumentParser;
import com.kellen.utils.context.TenantContextHolder;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** 由 scripts/verify-rag-engine.py 启动真实 Python 进程，校验跨语言边界而非模拟响应。 */
class RagEngineLiveIntegrationTest {
    private RagEngineProperties config;
    private RagEngineClient client;
    private KnowledgeDocumentParser parser;

    @BeforeEach void prepare() {
        config = new RagEngineProperties();
        config.setParseEnabled(true);
        config.setBaseUrl(System.getenv("RAG_ENGINE_LIVE_URL"));
        config.setApiKey(System.getenv("RAG_ENGINE_LIVE_KEY"));
        assertThat(config.getBaseUrl()).as("Run scripts/verify-rag-engine.py").isNotBlank();
        RagVectorProperties vector = new RagVectorProperties();
        client = new RagEngineClient(config, vector, new ObjectMapper());
        parser = new KnowledgeDocumentParser(vector, client);
        TenantContextHolder.setTenantId("engine-live-fixture");
    }

    @AfterEach void clear() { TenantContextHolder.clear(); }

    @Test void shouldParseRealPdfThroughJavaFacadeAndPythonWorker() {
        var document = parser.parse("fixture.pdf", "Contract fixture", pdf());
        assertThat(document.sourceType()).isEqualTo("pdf");
        assertThat(document.chunks()).hasSize(1);
        var chunk = document.chunks().get(0);
        assertThat(chunk.content()).contains("RAG engine contract fixture");
        assertThat(chunk.metadata()).containsEntry("page", 1).containsEntry("source_name", "fixture.pdf");
        assertThat(chunk.locator()).contains("1");
    }

    @Test void shouldRejectSpoofedPdfInRealParser() {
        assertThatThrownBy(() -> parser.parse("spoof.pdf", "Fixture", "not a PDF".getBytes(StandardCharsets.UTF_8)))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test void shouldRejectIncorrectServiceIdentity() {
        config.setApiKey("deliberately-invalid-contract-credential");
        assertThatThrownBy(() -> parser.parse("fixture.pdf", "Fixture", pdf()))
                .isInstanceOf(ExternalServiceException.class)
                .hasMessageContaining("401");
    }

    @Test void shouldPreserveDisabledParserBoundary() {
        config.setParseEnabled(false);
        assertThatThrownBy(() -> parser.parse("fixture.pdf", "Fixture", pdf()))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("尚未启用");
    }

    private static byte[] pdf() {
        String content = "BT /F1 12 Tf 20 100 Td (RAG engine contract fixture) Tj ET";
        List<String> objects = List.of(
                "<< /Type /Catalog /Pages 2 0 R >>",
                "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
                "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
                "<< /Length " + content.length() + " >>\nstream\n" + content + "\nendstream");
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        bytes.writeBytes("%PDF-1.4\n".getBytes(StandardCharsets.US_ASCII));
        List<Integer> offsets = new ArrayList<>();
        for (int i = 0; i < objects.size(); i++) {
            offsets.add(bytes.size());
            bytes.writeBytes(((i + 1) + " 0 obj\n" + objects.get(i) + "\nendobj\n").getBytes(StandardCharsets.US_ASCII));
        }
        int xref = bytes.size();
        bytes.writeBytes("xref\n0 6\n0000000000 65535 f \n".getBytes(StandardCharsets.US_ASCII));
        offsets.forEach(offset -> bytes.writeBytes(String.format("%010d 00000 n \n", offset).getBytes(StandardCharsets.US_ASCII)));
        bytes.writeBytes(("trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + xref + "\n%%EOF\n").getBytes(StandardCharsets.US_ASCII));
        return bytes.toByteArray();
    }
}
