"""
PDF 适配器模块 - 解析 PDF 文件

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5
"""
from __future__ import annotations

from io import BytesIO
from typing import List, Optional

import fitz  # PyMuPDF

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import OCRRequiredError, ParseError
from app.services.adapters.models import (
    BlockNode,
    DocumentAST,
    NodeType,
    ParseResult,
)
from app.services.adapters.segment_extractor import extract_segments


class PdfAdapter(FormatAdapter):
    """PDF 文件适配器
    
    使用 PyMuPDF 解析 PDF 文件，提取文本内容和表格。
    """

    def supported_extensions(self) -> List[str]:
        return [".pdf"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        """解析 PDF 文件
        
        Args:
            raw_bytes: 文件字节内容
            
        Returns:
            ParseResult: 解析结果
            
        Raises:
            ParseError: 当文件损坏或无法解析时
            OCRRequiredError: 当 PDF 是扫描件需要 OCR 时
        """
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".pdf"),
                segments=[],
                metadata={},
            )
        
        try:
            doc = fitz.open(stream=raw_bytes, filetype="pdf")
        except Exception as e:
            raise ParseError(
                filename="<unknown>",
                reason=f"无法解析 PDF 文件: {str(e)}"
            )
        
        nodes = []
        total_text_length = 0
        page_count = len(doc)
        
        for page_num in range(page_count):
            page = doc[page_num]
            page_nodes = self._parse_page(page, page_num + 1)
            nodes.extend(page_nodes)
            
            # 统计文本长度
            for node in page_nodes:
                if node.text_content:
                    total_text_length += len(node.text_content)
        
        doc.close()
        
        # 检查是否是扫描件（几乎没有文本）
        # 只有当页面数大于0且文本长度非常少时才认为需要OCR
        if page_count > 0 and total_text_length == 0:
            raise OCRRequiredError("<unknown>")
        
        ast = DocumentAST(nodes=nodes, source_format=".pdf")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"page_count": page_count},
        )

    def _parse_page(self, page: fitz.Page, page_num: int) -> List[BlockNode]:
        """解析单个页面
        
        Args:
            page: PyMuPDF 页面对象
            page_num: 页码（从 1 开始）
            
        Returns:
            List[BlockNode]: 页面中的块级节点列表
        """
        nodes = []
        
        # 尝试提取表格
        tables = page.find_tables()
        table_rects = []
        
        for table in tables:
            table_node = self._parse_table(table, page_num)
            if table_node:
                nodes.append(table_node)
                table_rects.append(table.bbox)
        
        # 提取文本块（排除表格区域）
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        
        for block in blocks:
            if block.get("type") != 0:  # 只处理文本块
                continue
            
            # 检查是否在表格区域内
            block_rect = fitz.Rect(block["bbox"])
            in_table = any(
                block_rect.intersects(fitz.Rect(tr)) 
                for tr in table_rects
            )
            if in_table:
                continue
            
            # 提取段落文本
            text = self._extract_block_text(block)
            if text.strip():
                node = BlockNode(
                    node_type=NodeType.PARAGRAPH,
                    text_content=text.strip(),
                    metadata={"page": page_num},
                )
                nodes.append(node)
        
        return nodes

    def _parse_table(self, table, page_num: int) -> Optional[BlockNode]:
        """解析表格
        
        Args:
            table: PyMuPDF 表格对象
            page_num: 页码
            
        Returns:
            Optional[BlockNode]: 表格节点
        """
        try:
            data = table.extract()
            if not data:
                return None
            
            rows = []
            for row_data in data:
                cells = []
                for cell_text in row_data:
                    cell_node = BlockNode(
                        node_type=NodeType.TABLE_CELL,
                        text_content=(cell_text or "").strip(),
                    )
                    cells.append(cell_node)
                
                row_node = BlockNode(
                    node_type=NodeType.TABLE_ROW,
                    children=cells,
                )
                rows.append(row_node)
            
            return BlockNode(
                node_type=NodeType.TABLE,
                children=rows,
                metadata={
                    "page": page_num,
                    "rows": len(data),
                    "columns": len(data[0]) if data else 0,
                },
            )
        except Exception:
            return None

    def _extract_block_text(self, block: dict) -> str:
        """从文本块中提取文本
        
        Args:
            block: PyMuPDF 文本块字典
            
        Returns:
            str: 提取的文本
        """
        lines = []
        for line in block.get("lines", []):
            spans_text = []
            for span in line.get("spans", []):
                text = span.get("text", "")
                if text:
                    spans_text.append(text)
            if spans_text:
                lines.append("".join(spans_text))
        
        return " ".join(lines)
