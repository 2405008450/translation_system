"""PDF 文件解析 - 支持表格提取和句子级分割"""

import os
import re
from .base import BaseParser, Document, Paragraph, Table


class PdfParser(BaseParser):

    @staticmethod
    def supported_extensions():
        return [".pdf"]

    def parse(self, file_path: str) -> Document:
        # 优先用 pdfplumber（表格提取更好）
        try:
            import pdfplumber
            return self._parse_with_pdfplumber(file_path)
        except ImportError:
            pass
        
        # 备选 PyMuPDF (fitz)
        try:
            import fitz  # PyMuPDF
            return self._parse_with_pymupdf(file_path)
        except ImportError:
            return Document(
                paragraphs=[],
                tables=[],
                filename=os.path.basename(file_path),
                raw_text="[PDF解析失败: 请安装 pdfplumber 或 pymupdf]",
            )
        except Exception as e:
            return Document(
                paragraphs=[],
                tables=[],
                filename=os.path.basename(file_path),
                raw_text=f"[PDF解析失败: {e}]",
            )

    def _parse_with_pdfplumber(self, file_path: str) -> Document:
        """使用 pdfplumber 解析 PDF，支持表格提取"""
        import pdfplumber

        paragraphs = []
        tables = []
        all_table_texts = set()

        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                # 1. 先提取表格
                page_tables = page.extract_tables()
                for tbl in page_tables:
                    if not tbl or not tbl[0]:
                        continue
                    
                    for row in tbl:
                        for cell in row:
                            if cell:
                                cell_text = str(cell).strip()
                                if cell_text:
                                    all_table_texts.add(cell_text)
                    
                    headers = [str(cell).strip() if cell else "" for cell in tbl[0]]
                    rows = []
                    for row in tbl[1:]:
                        row_data = [str(cell).strip() if cell else "" for cell in row]
                        rows.append(row_data)
                    
                    if headers or rows:
                        tables.append(Table(headers=headers, rows=rows))
                        
                        for row_idx, row in enumerate([tbl[0]] + tbl[1:]):
                            for cell in row:
                                if cell:
                                    cell_text = str(cell).strip()
                                    cell_sentences = self._split_into_sentences(cell_text)
                                    for sent in cell_sentences:
                                        if sent:
                                            paragraphs.append(Paragraph(text=sent))

                # 2. 提取非表格的文本
                text = page.extract_text()
                if text:
                    for line in text.split("\n"):
                        stripped = line.strip()
                        if not stripped:
                            continue
                        if stripped in all_table_texts:
                            continue
                        sentences = self._split_into_sentences(stripped)
                        for sent in sentences:
                            if sent:
                                paragraphs.append(Paragraph(text=sent))

        raw_text = "\n".join(p.text for p in paragraphs)

        return Document(
            paragraphs=paragraphs,
            tables=tables,
            filename=os.path.basename(file_path),
            raw_text=raw_text,
        )

    def _parse_with_pymupdf(self, file_path: str) -> Document:
        """使用 PyMuPDF 解析 PDF，尝试提取表格"""
        import fitz
        
        paragraphs = []
        tables = []
        
        doc = fitz.open(file_path)
        for page in doc:
            try:
                page_tables = page.find_tables()
                for tbl in page_tables:
                    table_data = tbl.extract()
                    if not table_data or not table_data[0]:
                        continue
                    
                    headers = [str(cell).strip() if cell else "" for cell in table_data[0]]
                    rows = []
                    for row in table_data[1:]:
                        row_data = [str(cell).strip() if cell else "" for cell in row]
                        rows.append(row_data)
                    
                    if headers or rows:
                        tables.append(Table(headers=headers, rows=rows))
                        
                        for row in table_data:
                            for cell in row:
                                if cell:
                                    cell_text = str(cell).strip()
                                    cell_sentences = self._split_into_sentences(cell_text)
                                    for sent in cell_sentences:
                                        if sent:
                                            paragraphs.append(Paragraph(text=sent))
            except AttributeError:
                pass
            
            text = page.get_text()
            if text:
                for line in text.split("\n"):
                    stripped = line.strip()
                    if stripped:
                        sentences = self._split_into_sentences(stripped)
                        for sent in sentences:
                            if sent:
                                paragraphs.append(Paragraph(text=sent))
        
        doc.close()
        
        raw_text = "\n".join(p.text for p in paragraphs)
        
        return Document(
            paragraphs=paragraphs,
            tables=tables,
            filename=os.path.basename(file_path),
            raw_text=raw_text,
        )

    def _split_into_sentences(self, text: str) -> list:
        """将文本按句子分割"""
        if not text or not text.strip():
            return []
        
        text = text.strip()
        
        if len(text) < 50 and not any(c in text for c in '。！？；.!?;'):
            return [text] if text else []
        
        pattern = r'(?<=[。！？；])|(?<=[.!?;])(?=\s|$)'
        parts = re.split(pattern, text)
        
        sentences = []
        current_sentence = ""
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            if part and part[-1] in '。！？；.!?;':
                if current_sentence:
                    sentences.append(current_sentence + part)
                    current_sentence = ""
                else:
                    sentences.append(part)
            else:
                if current_sentence:
                    current_sentence += " " + part
                else:
                    current_sentence = part
        
        if current_sentence:
            sentences.append(current_sentence)
        
        return [s.strip() for s in sentences if s.strip() and len(s.strip()) >= 2]
