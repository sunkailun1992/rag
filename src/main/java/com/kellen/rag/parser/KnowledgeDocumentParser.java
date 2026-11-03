package com.kellen.rag.parser;

import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.model.KnowledgeChunkDraft;
import com.kellen.rag.model.ParsedKnowledgeDocument;
import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.DataFormatter;
import org.apache.poi.ss.usermodel.FormulaEvaluator;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.Workbook;
import org.apache.poi.ss.usermodel.WorkbookFactory;
import org.apache.poi.xwpf.usermodel.IBodyElement;
import org.apache.poi.xwpf.usermodel.XWPFDocument;
import org.apache.poi.xwpf.usermodel.XWPFParagraph;
import org.apache.poi.xwpf.usermodel.XWPFStyle;
import org.apache.poi.xwpf.usermodel.XWPFTable;
import org.apache.poi.xwpf.usermodel.XWPFTableCell;
import org.apache.poi.xwpf.usermodel.XWPFTableRow;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

import java.io.ByteArrayInputStream;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * Office 知识文档解析器。
 *
 * <p>XLSX 和 DOCX 表格按“表头 + 单行记录”切片，避免排班、量表、层级和措施失去列语义。
 * DOCX 正文按标题层级和段落切片；过长记录只按字段边界拆分，并为每个子片重复文档、
 * 章节和定位上下文。</p>
 */
@Component
public class KnowledgeDocumentParser {

    private static final int MAX_ZIP_ENTRIES = 10000;
    private static final int MAX_COLUMNS = 100;
    private static final Pattern HEADING_STYLE = Pattern.compile("(?i)^(heading|标题)\\s*[1-9].*$");
    private static final int MAX_FIELD_NAME_CHARACTERS = 40;

    private final RagVectorProperties properties;

    public KnowledgeDocumentParser(RagVectorProperties properties) {
        this.properties = properties;
    }

    /**
     * 解析上传文档。
     *
     * @param originalName 原始文件名
     * @param requestedTitle 页面指定标题
     * @param bytes 文件内容
     * @return 结构化文档
     */
    public ParsedKnowledgeDocument parse(String originalName, String requestedTitle, byte[] bytes) {
        if (bytes == null || bytes.length == 0) {
            throw new IllegalArgumentException("上传文件为空");
        }
        if (bytes.length > properties.getMaxFileBytes()) {
            throw new IllegalArgumentException("上传文件超过大小限制");
        }
        String sourceName = safeFileName(originalName);
        String extension = extension(sourceName);
        String title = StringUtils.hasText(requestedTitle)
                ? requestedTitle.trim()
                : sourceName.substring(0, sourceName.length() - extension.length() - 1);
        try {
            if ("xlsx".equals(extension)) {
                return parseWorkbook(title, sourceName, bytes);
            }
            if ("docx".equals(extension)) {
                return parseWordDocument(title, sourceName, bytes);
            }
            throw new IllegalArgumentException("RAG 知识库仅接收 xlsx 或 docx 文档");
        } catch (IllegalArgumentException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new IllegalArgumentException("文档结构无法解析，请检查文件是否损坏", exception);
        }
    }

    private ParsedKnowledgeDocument parseWorkbook(String title, String sourceName, byte[] bytes) throws Exception {
        validateOfficeArchive(bytes, "xl/workbook.xml");
        List<KnowledgeChunkDraft> chunks = new ArrayList<>();
        try (Workbook workbook = WorkbookFactory.create(new ByteArrayInputStream(bytes))) {
            DataFormatter formatter = new DataFormatter(Locale.SIMPLIFIED_CHINESE);
            FormulaEvaluator evaluator = workbook.getCreationHelper().createFormulaEvaluator();
            for (int sheetIndex = 0; sheetIndex < workbook.getNumberOfSheets(); sheetIndex++) {
                Sheet sheet = workbook.getSheetAt(sheetIndex);
                int headerIndex = findHeaderRow(sheet, formatter, evaluator);
                if (headerIndex < 0) {
                    continue;
                }
                Row headerRow = sheet.getRow(headerIndex);
                int columnCount = Math.min(Math.max(headerRow.getLastCellNum(), 0), MAX_COLUMNS);
                List<String> headers = new ArrayList<>(columnCount);
                for (int column = 0; column < columnCount; column++) {
                    String header = cellText(headerRow.getCell(column), formatter, evaluator);
                    headers.add(StringUtils.hasText(header) ? header : "列" + (column + 1));
                }
                int lastRow = Math.min(sheet.getLastRowNum(), headerIndex + properties.getMaxRows());
                for (int rowIndex = headerIndex + 1; rowIndex <= lastRow; rowIndex++) {
                    Row row = sheet.getRow(rowIndex);
                    if (row == null) {
                        continue;
                    }
                    List<String> values = new ArrayList<>(columnCount);
                    boolean nonBlank = false;
                    for (int column = 0; column < columnCount; column++) {
                        String value = cellText(row.getCell(column), formatter, evaluator);
                        values.add(value);
                        nonBlank = nonBlank || StringUtils.hasText(value);
                    }
                    if (nonBlank) {
                        chunks.addAll(tableRowChunks(title, sheet.getSheetName(),
                                "第" + (rowIndex + 1) + "行", headers, values, "xlsx-row"));
                    }
                }
            }
        }
        return requireChunks(new ParsedKnowledgeDocument(title, sourceName, "xlsx", List.copyOf(chunks)));
    }

    private ParsedKnowledgeDocument parseWordDocument(String title, String sourceName, byte[] bytes)
            throws Exception {
        validateOfficeArchive(bytes, "word/document.xml");
        List<KnowledgeChunkDraft> chunks = new ArrayList<>();
        try (XWPFDocument document = new XWPFDocument(new ByteArrayInputStream(bytes))) {
            String section = "正文";
            int paragraphNumber = 0;
            int tableNumber = 0;
            int recordCount = 0;
            for (IBodyElement element : document.getBodyElements()) {
                if (element instanceof XWPFParagraph paragraph) {
                    String text = normalizeFieldText(paragraph.getText());
                    if (!StringUtils.hasText(text)) {
                        continue;
                    }
                    if (isHeading(document, paragraph)) {
                        section = text;
                        continue;
                    }
                    if (++recordCount > properties.getMaxRows()) {
                        throw new IllegalArgumentException("Word 文档超过最大可解析记录数");
                    }
                    paragraphNumber++;
                    String locator = "第" + paragraphNumber + "段";
                    Field field = paragraphField(text);
                    chunks.addAll(structuredFieldChunks(title, section, locator,
                            List.of(field), "docx-paragraph", "段落语义与内容"));
                    continue;
                }
                if (element instanceof XWPFTable table) {
                    tableNumber++;
                    List<XWPFTableRow> rows = table.getRows();
                    int headerIndex = findHeaderRow(rows);
                    if (headerIndex < 0) {
                        continue;
                    }
                    List<String> headers = wordHeaders(rows.get(headerIndex));
                    for (int rowIndex = headerIndex + 1; rowIndex < rows.size(); rowIndex++) {
                        List<String> values = wordValues(rows.get(rowIndex), headers.size());
                        if (values.stream().noneMatch(StringUtils::hasText)) {
                            continue;
                        }
                        if (++recordCount > properties.getMaxRows()) {
                            throw new IllegalArgumentException("Word 文档超过最大可解析记录数");
                        }
                        chunks.addAll(tableRowChunks(title, section,
                                "表" + tableNumber + "第" + (rowIndex + 1) + "行",
                                headers, values, "docx-table-row"));
                    }
                }
            }
        }
        return requireChunks(new ParsedKnowledgeDocument(title, sourceName, "docx", List.copyOf(chunks)));
    }

    private List<KnowledgeChunkDraft> tableRowChunks(String title, String section, String locator,
                                                     List<String> headers, List<String> values, String type) {
        List<Field> fields = new ArrayList<>();
        int count = Math.min(headers.size(), values.size());
        for (int index = 0; index < count; index++) {
            if (StringUtils.hasText(values.get(index))) {
                fields.add(new Field(normalizeFieldText(headers.get(index)),
                        normalizeFieldText(values.get(index))));
            }
        }
        return structuredFieldChunks(title, section, locator, fields, type, "表头语义与记录");
    }

    private List<KnowledgeChunkDraft> structuredFieldChunks(String title, String section,
                                                             String locator, List<Field> fields,
                                                             String type, String contentLabel) {
        String prefix = "文档: " + title + "\n位置: " + section + " / " + locator + "\n"
                + contentLabel + ":\n";
        List<KnowledgeChunkDraft> chunks = new ArrayList<>();
        StringBuilder current = new StringBuilder(prefix);
        int part = 1;
        for (Field item : fields) {
            String field = "- " + normalizeFieldName(item.name()) + ": " + item.value();
            if (current.length() + field.length() + 1 > properties.getMaxChunkCharacters()
                    && current.length() > prefix.length()) {
                chunks.add(new KnowledgeChunkDraft(type, section, locator + " / 片段" + part++, current.toString()));
                current = new StringBuilder(prefix);
            }
            if (field.length() + prefix.length() > properties.getMaxChunkCharacters()) {
                int available = Math.max(500, properties.getMaxChunkCharacters() - prefix.length() - 50);
                for (int offset = 0; offset < field.length(); offset += available) {
                    int end = Math.min(field.length(), offset + available);
                    chunks.add(new KnowledgeChunkDraft(type, section, locator + " / 片段" + part++,
                            prefix + field.substring(offset, end)));
                }
            } else {
                current.append(field).append('\n');
            }
        }
        if (current.length() > prefix.length()) {
            chunks.add(new KnowledgeChunkDraft(type, section, locator + " / 片段" + part, current.toString().trim()));
        }
        return chunks;
    }

    private boolean isHeading(XWPFDocument document, XWPFParagraph paragraph) {
        String styleId = paragraph.getStyle();
        if (!StringUtils.hasText(styleId)) {
            return false;
        }
        if (HEADING_STYLE.matcher(styleId.trim()).matches()) {
            return true;
        }
        if (document.getStyles() == null) {
            return false;
        }
        XWPFStyle style = document.getStyles().getStyle(styleId);
        return style != null && StringUtils.hasText(style.getName())
                && HEADING_STYLE.matcher(style.getName().trim()).matches();
    }

    private Field paragraphField(String text) {
        int chineseColon = text.indexOf('：');
        int asciiColon = text.indexOf(':');
        int colon = chineseColon < 0 ? asciiColon
                : asciiColon < 0 ? chineseColon : Math.min(chineseColon, asciiColon);
        if (colon > 0 && colon <= MAX_FIELD_NAME_CHARACTERS) {
            String name = text.substring(0, colon).trim();
            String value = text.substring(colon + 1).trim();
            if (StringUtils.hasText(name) && StringUtils.hasText(value)) {
                return new Field(name, value);
            }
        }
        return new Field("内容", text);
    }

    private int findHeaderRow(List<XWPFTableRow> rows) {
        int limit = Math.min(rows.size(), 20);
        for (int index = 0; index < limit; index++) {
            long values = rows.get(index).getTableCells().stream()
                    .map(this::cellText)
                    .filter(StringUtils::hasText)
                    .count();
            if (values >= 2) {
                return index;
            }
        }
        return -1;
    }

    private List<String> wordHeaders(XWPFTableRow row) {
        int columnCount = Math.min(row.getTableCells().size(), MAX_COLUMNS);
        List<String> headers = new ArrayList<>(columnCount);
        for (int column = 0; column < columnCount; column++) {
            String header = cellText(row.getCell(column));
            headers.add(StringUtils.hasText(header) ? header : "列" + (column + 1));
        }
        return headers;
    }

    private List<String> wordValues(XWPFTableRow row, int columnCount) {
        List<String> values = new ArrayList<>(columnCount);
        for (int column = 0; column < columnCount; column++) {
            XWPFTableCell cell = column < row.getTableCells().size() ? row.getCell(column) : null;
            values.add(cellText(cell));
        }
        return values;
    }

    private String cellText(XWPFTableCell cell) {
        return cell == null ? "" : normalizeFieldText(cell.getText());
    }

    private void validateOfficeArchive(byte[] bytes, String requiredEntry) throws Exception {
        if (bytes.length < 4 || bytes[0] != 'P' || bytes[1] != 'K') {
            throw new IllegalArgumentException("Office 文档文件头不正确");
        }
        long total = 0;
        int entries = 0;
        boolean found = false;
        byte[] buffer = new byte[8192];
        try (ZipInputStream input = new ZipInputStream(new ByteArrayInputStream(bytes))) {
            ZipEntry entry;
            while ((entry = input.getNextEntry()) != null) {
                entries++;
                String name = entry.getName();
                if (entries > MAX_ZIP_ENTRIES || name.startsWith("/") || name.contains("../")) {
                    throw new IllegalArgumentException("Office 文档压缩结构不安全");
                }
                found = found || requiredEntry.equals(name);
                int read;
                while ((read = input.read(buffer)) >= 0) {
                    total += read;
                    if (total > properties.getMaxUncompressedBytes()) {
                        throw new IllegalArgumentException("Office 文档解压后超过大小限制");
                    }
                }
            }
        }
        if (!found) {
            throw new IllegalArgumentException("文件扩展名与 Office 文档类型不一致");
        }
    }

    private int findHeaderRow(Sheet sheet, DataFormatter formatter, FormulaEvaluator evaluator) {
        int limit = Math.min(sheet.getLastRowNum(), 20);
        for (int index = sheet.getFirstRowNum(); index <= limit; index++) {
            Row row = sheet.getRow(index);
            if (row != null && row.cellIterator().hasNext()) {
                int values = 0;
                for (Cell cell : row) {
                    if (StringUtils.hasText(cellText(cell, formatter, evaluator))) {
                        values++;
                    }
                }
                if (values >= 2) {
                    return index;
                }
            }
        }
        return -1;
    }

    private String cellText(Cell cell, DataFormatter formatter, FormulaEvaluator evaluator) {
        return cell == null ? "" : normalizeText(formatter.formatCellValue(cell, evaluator));
    }

    private ParsedKnowledgeDocument requireChunks(ParsedKnowledgeDocument document) {
        if (document.chunks().isEmpty()) {
            throw new IllegalArgumentException("文档没有可向量化的有效内容");
        }
        return document;
    }

    private String safeFileName(String originalName) {
        if (!StringUtils.hasText(originalName)) {
            throw new IllegalArgumentException("缺少文件名");
        }
        String name = Path.of(originalName).getFileName().toString().trim();
        if (!StringUtils.hasText(name) || name.length() > 255 || name.indexOf('\0') >= 0) {
            throw new IllegalArgumentException("文件名不合法");
        }
        return name;
    }

    private String extension(String name) {
        int index = name.lastIndexOf('.');
        return index < 0 ? "" : name.substring(index + 1).toLowerCase(Locale.ROOT);
    }

    private String normalizeText(String value) {
        if (value == null) {
            return "";
        }
        return Arrays.stream(value.replace('\u00A0', ' ').replace("\r\n", "\n").split("\n"))
                .map(line -> line.trim().replaceAll("[\\t ]+", " "))
                .filter(StringUtils::hasText)
                .reduce((left, right) -> left + "\n" + right)
                .orElse("");
    }

    private String normalizeFieldText(String value) {
        return normalizeText(value).replace("\n", "；");
    }

    private String normalizeFieldName(String value) {
        return normalizeFieldText(value).replaceAll("\\s*[/／]\\s*", "/");
    }

    private record Field(String name, String value) {
    }
}
