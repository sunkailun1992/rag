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
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

import java.io.ByteArrayInputStream;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * XLSX 知识文档解析器。
 *
 * <p>表格按“表头 + 单行记录”切片，避免字段、层级、措施和依据失去列语义。
 * 过长记录只按字段边界拆分，并为每个子片重复文档、工作表和定位上下文。</p>
 */
@Component
public class KnowledgeDocumentParser {

    private static final int MAX_ZIP_ENTRIES = 10000;
    private static final int MAX_COLUMNS = 100;

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
            if (!"xlsx".equals(extension)) {
                throw new IllegalArgumentException("当前 RAG 知识库仅接收 xlsx 表格文档");
            }
            return parseWorkbook(title, sourceName, bytes);
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

    private List<KnowledgeChunkDraft> tableRowChunks(String title, String section, String locator,
                                                     List<String> headers, List<String> values, String type) {
        String prefix = "文档: " + title + "\n位置: " + section + " / " + locator + "\n表头语义与记录:\n";
        List<String> fields = new ArrayList<>();
        int count = Math.min(headers.size(), values.size());
        for (int index = 0; index < count; index++) {
            if (StringUtils.hasText(values.get(index))) {
                fields.add("- " + normalizeText(headers.get(index)) + ": " + normalizeText(values.get(index)));
            }
        }
        List<KnowledgeChunkDraft> chunks = new ArrayList<>();
        StringBuilder current = new StringBuilder(prefix);
        int part = 1;
        for (String field : fields) {
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
}
