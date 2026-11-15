package com.kellen.rag.parser;

import com.kellen.rag.client.RagEngineClient;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.model.ParsedKnowledgeDocument;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

/**
 * 固定 POI 5.5.1 下 xlsx 与 docx 的解析输出；升级 POI 6 后必须逐字一致。
 *
 * <p>测试文件由 {@link PoiBaselineFixtures} 现场生成，期望值是在 POI 5.5.1 下实际得到的切片文本。</p>
 */
class KnowledgeDocumentParserPoiBaselineTest {

    private final KnowledgeDocumentParser parser =
            new KnowledgeDocumentParser(new RagVectorProperties(), mock(RagEngineClient.class));

    @Test
    void xlsxFormulaWithCachedValueKeepsBaselineText() throws Exception {
        assertThat(contents("f.xlsx", PoiBaselineFixtures.formulaWorkbook(true))).containsExactly(
                "文档: 基线\n位置: 公式 / 第2行\n表头语义与记录:\n- 名称: 甲\n- 数量: 2\n- 合计: 6");
    }

    @Test
    void xlsxFormulaWithoutCachedValueKeepsBaselineText() throws Exception {
        assertThat(contents("f.xlsx", PoiBaselineFixtures.formulaWorkbook(false))).containsExactly(
                "文档: 基线\n位置: 公式 / 第2行\n表头语义与记录:\n- 名称: 甲\n- 数量: 2\n- 合计: 6");
    }

    @Test
    void xlsxDateAndNumberFormatsKeepBaselineText() throws Exception {
        // POI 5.5.1 把 08:30:15.678 截断成 15 秒；POI 6 与 Excel 的显示一致，四舍五入为 16 秒，按修正原有错误接受。
        assertThat(contents("m.xlsx", PoiBaselineFixtures.formatWorkbook())).containsExactly(
                "文档: 基线\n位置: 格式 / 第2行\n表头语义与记录:\n- 时间: 2026-11-15 08:30:16\n- 比例: 12.34%\n- 金额: 1,234,567.89");
    }

    @Test
    void docxParagraphsAndTablesKeepBaselineText() throws Exception {
        assertThat(contents("d.docx", PoiBaselineFixtures.paragraphsAndTable())).containsExactly(
                "文档: 基线\n位置: 运维手册 / 第1段\n段落语义与内容:\n- 超时: 三十秒",
                "文档: 基线\n位置: 运维手册 / 表1第2行\n表头语义与记录:\n- 组件: 索引\n- 说明: 可重建",
                "文档: 基线\n位置: 运维手册 / 表1第3行\n表头语义与记录:\n- 组件: 缓存\n- 说明: 可丢弃");
    }

    @Test
    void docxInlineContentControlTextIsKept() throws Exception {
        assertThat(contents("d.docx", PoiBaselineFixtures.inlineContentControl())).containsExactly(
                "文档: 基线\n位置: 正文 / 第1段\n段落语义与内容:\n- 内容: 前缀控件内文字后缀");
    }

    @Test
    void docxBlockContentControlKeepsBaselineText() throws Exception {
        // POI 5.5.1 下块级内容控件里的文字本来就不在输出里；升级只要求输出不变，不在本阶段补齐。
        assertThat(contents("d.docx", PoiBaselineFixtures.blockContentControl())).containsExactly(
                "文档: 基线\n位置: 正文 / 第1段\n段落语义与内容:\n- 内容: 正文第一段",
                "文档: 基线\n位置: 正文 / 第2段\n段落语义与内容:\n- 内容: 正文第二段");
    }

    private List<String> contents(String name, byte[] bytes) {
        ParsedKnowledgeDocument document = parser.parse(name, "基线", bytes);
        return document.chunks().stream().map(chunk -> chunk.content()).toList();
    }
}
