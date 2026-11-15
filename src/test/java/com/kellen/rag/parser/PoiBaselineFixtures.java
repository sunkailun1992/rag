package com.kellen.rag.parser;

import org.apache.poi.ss.usermodel.CellStyle;
import org.apache.poi.ss.usermodel.DataFormat;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.apache.poi.xwpf.usermodel.XWPFDocument;
import org.openxmlformats.schemas.wordprocessingml.x2006.main.CTP;
import org.openxmlformats.schemas.wordprocessingml.x2006.main.CTSdtBlock;
import org.openxmlformats.schemas.wordprocessingml.x2006.main.CTSdtRun;

import java.io.ByteArrayOutputStream;
import java.time.LocalDateTime;

/**
 * 在测试里用 POI 现场生成 xlsx 与 docx，不提交二进制文件；POI 5.5.1 与 6.0.0 下用同一份生成逻辑比对解析输出。
 */
final class PoiBaselineFixtures {

    private PoiBaselineFixtures() {
    }

    static byte[] formulaWorkbook(boolean evaluate) throws Exception {
        try (XSSFWorkbook workbook = new XSSFWorkbook(); ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            var sheet = workbook.createSheet("公式");
            var header = sheet.createRow(0);
            header.createCell(0).setCellValue("名称");
            header.createCell(1).setCellValue("数量");
            header.createCell(2).setCellValue("合计");
            var row = sheet.createRow(1);
            row.createCell(0).setCellValue("甲");
            row.createCell(1).setCellValue(2);
            row.createCell(2).setCellFormula("B2*3");
            if (evaluate) {
                workbook.getCreationHelper().createFormulaEvaluator().evaluateAll();
            }
            workbook.write(output);
            return output.toByteArray();
        }
    }

    static byte[] formatWorkbook() throws Exception {
        try (XSSFWorkbook workbook = new XSSFWorkbook(); ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            DataFormat format = workbook.createDataFormat();
            CellStyle date = workbook.createCellStyle();
            date.setDataFormat(format.getFormat("yyyy-mm-dd hh:mm:ss"));
            CellStyle percent = workbook.createCellStyle();
            percent.setDataFormat(format.getFormat("0.00%"));
            CellStyle thousands = workbook.createCellStyle();
            thousands.setDataFormat(format.getFormat("#,##0.00"));
            var sheet = workbook.createSheet("格式");
            var header = sheet.createRow(0);
            header.createCell(0).setCellValue("时间");
            header.createCell(1).setCellValue("比例");
            header.createCell(2).setCellValue("金额");
            var row = sheet.createRow(1);
            var dateCell = row.createCell(0);
            dateCell.setCellValue(LocalDateTime.of(2026, 11, 15, 8, 30, 15, 678_000_000));
            dateCell.setCellStyle(date);
            var percentCell = row.createCell(1);
            percentCell.setCellValue(0.1234);
            percentCell.setCellStyle(percent);
            var amountCell = row.createCell(2);
            amountCell.setCellValue(1234567.891);
            amountCell.setCellStyle(thousands);
            workbook.write(output);
            return output.toByteArray();
        }
    }

    static byte[] paragraphsAndTable() throws Exception {
        try (XWPFDocument word = new XWPFDocument(); ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            var heading = word.createParagraph();
            heading.setStyle("Heading1");
            heading.createRun().setText("运维手册");
            word.createParagraph().createRun().setText("超时：三十秒");
            var table = word.createTable(3, 2);
            table.getRow(0).getCell(0).setText("组件");
            table.getRow(0).getCell(1).setText("说明");
            table.getRow(1).getCell(0).setText("索引");
            table.getRow(1).getCell(1).setText("可重建");
            table.getRow(2).getCell(0).setText("缓存");
            table.getRow(2).getCell(1).setText("可丢弃");
            word.write(output);
            return output.toByteArray();
        }
    }

    static byte[] inlineContentControl() throws Exception {
        try (XWPFDocument word = new XWPFDocument(); ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            var paragraph = word.createParagraph();
            paragraph.createRun().setText("前缀");
            CTP ctp = paragraph.getCTP();
            CTSdtRun sdt = ctp.addNewSdt();
            sdt.addNewSdtContent().addNewR().addNewT().setStringValue("控件内文字");
            ctp.addNewR().addNewT().setStringValue("后缀");
            word.write(output);
            return output.toByteArray();
        }
    }

    static byte[] blockContentControl() throws Exception {
        try (XWPFDocument word = new XWPFDocument(); ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            word.createParagraph().createRun().setText("正文第一段");
            CTSdtBlock block = word.getDocument().getBody().addNewSdt();
            block.addNewSdtContent().addNewP().addNewR().addNewT().setStringValue("块级控件文字");
            word.createParagraph().createRun().setText("正文第二段");
            word.write(output);
            return output.toByteArray();
        }
    }
}
