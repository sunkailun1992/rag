package com.kellen.rag.parser;

import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.model.ParsedKnowledgeDocument;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.apache.poi.xwpf.usermodel.XWPFDocument;
import org.junit.jupiter.api.Test;

import java.io.ByteArrayOutputStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * XLSX 结构化切片契约测试。
 */
class KnowledgeDocumentParserTest {

    private final KnowledgeDocumentParser parser =
            new KnowledgeDocumentParser(new RagVectorProperties(),
                    org.mockito.Mockito.mock(com.kellen.rag.client.RagEngineClient.class));

    @Test
    void shouldRepeatCompleteHeaderSemanticsForEveryDataRow() throws Exception {
        byte[] workbook = workbook();

        ParsedKnowledgeDocument document =
                parser.parse("structured-knowledge.xlsx", "结构化知识", workbook);

        assertThat(document.sourceType()).isEqualTo("xlsx");
        assertThat(document.chunks()).hasSize(2);
        assertThat(document.chunks())
                .allSatisfy(chunk -> assertThat(chunk.content())
                        .contains("表头语义与记录:")
                        .contains("- 分类:")
                        .contains("- 等级:")
                        .contains("- 建议:"));
        assertThat(document.chunks().get(0).content()).contains("- 分类: A", "- 建议: 建议一");
        assertThat(document.chunks().get(1).content()).contains("- 分类: B", "- 建议: 建议二");
    }

    @Test
    void shouldRejectUnsupportedExtensionEvenWhenContentIsAnOfficeArchive() throws Exception {
        assertThatThrownBy(() -> parser.parse("structured-knowledge.txt", null, workbook()))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("xlsx");
    }

    @Test
    void shouldParseDocxParagraphsAndTablesWithSourceLocations() throws Exception {
        byte[] bytes;
        try (XWPFDocument word = new XWPFDocument();
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            var heading = word.createParagraph();
            heading.setStyle("Heading1");
            heading.createRun().setText("部署指南");
            word.createParagraph().createRun().setText("重试策略：使用指数退避");
            var table = word.createTable(2, 2);
            table.getRow(0).getCell(0).setText("名称");
            table.getRow(0).getCell(1).setText("用途");
            table.getRow(1).getCell(0).setText("索引");
            table.getRow(1).getCell(1).setText("可重建派生数据");
            word.write(output);
            bytes = output.toByteArray();
        }
        var parsed = parser.parse("guide.docx", "技术指南", bytes);
        assertThat(parsed.sourceType()).isEqualTo("docx");
        assertThat(parsed.chunks()).hasSize(2);
        assertThat(parsed.chunks().get(0).content()).contains("部署指南", "重试策略: 使用指数退避");
        assertThat(parsed.chunks().get(1).content()).contains("表1第2行", "名称: 索引", "用途: 可重建派生数据");
    }

    @Test
    void shouldRejectSpreadsheetMasqueradingAsWord() throws Exception {
        assertThatThrownBy(() -> parser.parse("guide.docx", null, workbook()))
                .isInstanceOf(IllegalArgumentException.class);
    }

    private byte[] workbook() throws Exception {
        try (XSSFWorkbook workbook = new XSSFWorkbook();
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            var sheet = workbook.createSheet("知识记录");
            var header = sheet.createRow(0);
            header.createCell(0).setCellValue("分类");
            header.createCell(1).setCellValue("等级");
            header.createCell(2).setCellValue("建议");

            var first = sheet.createRow(1);
            first.createCell(0).setCellValue("A");
            first.createCell(1).setCellValue("高");
            first.createCell(2).setCellValue("建议一");

            var second = sheet.createRow(2);
            second.createCell(0).setCellValue("B");
            second.createCell(1).setCellValue("中");
            second.createCell(2).setCellValue("建议二");

            workbook.write(output);
            return output.toByteArray();
        }
    }
}
