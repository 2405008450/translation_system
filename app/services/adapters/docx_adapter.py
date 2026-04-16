"""
DOCX 适配器模块 - 解析 Word 文档

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5
"""
from io import BytesIO
from typing import Iterator, List

from docx import Document
from docx.document import Document as DocxDocument
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import (
    BlockNode,
    DocumentAST,
    NodeType,
    ParseResult,
)
from app.services.adapters.segment_extractor import extract_segments


class DocxAdapter(FormatAdapter):
    """DOCX 文件适配器
    
    使用 python-docx 解析 Word 文档，提取段落、表格和标题。
    """

    def supported_extensions(self) -> List[str]:
        return [".docx"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        """解析 DOCX 文件
        
        Args:
            raw_bytes: 文件字节内容
            
        Returns:
            ParseResult: 解析结果
            
        Raises:
            ParseError: 当文件损坏或无法解析时
        """
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".docx"),
                segments=[],
                metadata={},
            )
        
        try:
            document = Document(BytesIO(raw_bytes))
        except Exception as e:
            raise ParseError(
                filename="<unknown>",
                reason=f"无法解析 DOCX 文件: {str(e)}"
            )
        
        nodes = []
        for block in self._iter_block_items(document):
            if isinstance(block, Paragraph):
                node = self._parse_paragraph(block)
                if node:  # 跳过空段落
                    nodes.append(node)
            elif isinstance(block, Table):
                node = self._parse_table(block)
                nodes.append(node)
        
        ast = DocumentAST(nodes=nodes, source_format=".docx")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"paragraph_count": len([n for n in nodes if n.node_type == NodeType.PARAGRAPH])},
        )

    def _parse_paragraph(self, paragraph: Paragraph) -> BlockNode | None:
        """解析段落
        
        Args:
            paragraph: python-docx 段落对象
            
        Returns:
            BlockNode | None: 段落节点，如果段落为空则返回 None
        """
        text = paragraph.text.strip()
        if not text:
            return None
        
        # 检查是否是标题
        style_name = paragraph.style.name if paragraph.style else ""
        if style_name.startswith("Heading"):
            try:
                level = int(style_name.replace("Heading ", "").replace("Heading", "1"))
            except ValueError:
                level = 1
            
            return BlockNode(
                node_type=NodeType.HEADING,
                text_content=text,
                metadata={"level": level, "style": style_name},
            )
        
        return BlockNode(
            node_type=NodeType.PARAGRAPH,
            text_content=text,
            metadata={"style": style_name} if style_name else {},
        )

    def _parse_table(self, table: Table) -> BlockNode:
        """解析表格
        
        Args:
            table: python-docx 表格对象
            
        Returns:
            BlockNode: 表格节点
        """
        rows = []
        row_count = len(table.rows)
        col_count = len(table.columns) if table.rows else 0
        
        for row in table.rows:
            cells = []
            for cell in row.cells:
                cell_node = self._parse_cell(cell)
                cells.append(cell_node)
            
            row_node = BlockNode(
                node_type=NodeType.TABLE_ROW,
                children=cells,
            )
            rows.append(row_node)
        
        return BlockNode(
            node_type=NodeType.TABLE,
            children=rows,
            metadata={"rows": row_count, "columns": col_count},
        )

    def _parse_cell(self, cell: _Cell) -> BlockNode:
        """解析单元格
        
        Args:
            cell: python-docx 单元格对象
            
        Returns:
            BlockNode: 单元格节点
        """
        # 合并单元格内所有段落的文本
        paragraphs = []
        for para in cell.paragraphs:
            text = para.text.strip()
            if text:
                para_node = BlockNode(
                    node_type=NodeType.PARAGRAPH,
                    text_content=text,
                )
                paragraphs.append(para_node)
        
        # 如果只有一个段落，直接使用其文本
        if len(paragraphs) == 1:
            return BlockNode(
                node_type=NodeType.TABLE_CELL,
                text_content=paragraphs[0].text_content,
            )
        
        # 多个段落作为子节点
        return BlockNode(
            node_type=NodeType.TABLE_CELL,
            children=paragraphs,
        )

    def _iter_block_items(self, parent: DocxDocument | _Cell) -> Iterator[Paragraph | Table]:
        """迭代文档中的块级元素
        
        Args:
            parent: 文档或单元格对象
            
        Yields:
            Paragraph | Table: 段落或表格对象
        """
        if isinstance(parent, DocxDocument):
            parent_element = parent.element.body
        else:
            parent_element = parent._tc

        for child in parent_element.iterchildren():
            if child.tag.endswith("}p"):
                yield Paragraph(child, parent)
            elif child.tag.endswith("}tbl"):
                yield Table(child, parent)
