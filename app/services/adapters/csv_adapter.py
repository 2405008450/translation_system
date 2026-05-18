"""
CSV 适配器模块 - 解析 CSV 文件

支持标准 CSV 格式，自动检测分隔符。
提取所有文本单元格用于翻译。
"""
import csv
import io
from typing import List

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import (
    BlockNode,
    DocumentAST,
    NodeType,
    ParseResult,
)
from app.services.adapters.segment_extractor import extract_segments


class CsvAdapter(FormatAdapter):
    """CSV 文件适配器"""

    def supported_extensions(self) -> List[str]:
        return [".csv"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".csv"),
                segments=[],
                metadata={},
            )
        
        content = self._decode_content(raw_bytes)
        nodes = self._parse_csv(content)
        
        ast = DocumentAST(nodes=nodes, source_format=".csv")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"cell_count": len(nodes)},
        )

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "gb18030", "iso-8859-1"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ParseError(filename="<unknown>", reason="无法识别文件编码")

    def _parse_csv(self, content: str) -> List[BlockNode]:
        """解析 CSV 内容"""
        nodes = []
        
        # 检测分隔符
        try:
            dialect = csv.Sniffer().sniff(content[:4096], delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel
        
        reader = csv.reader(io.StringIO(content), dialect)
        
        for row_idx, row in enumerate(reader):
            for col_idx, cell in enumerate(row):
                text = cell.strip()
                if text:
                    # 跳过纯数字单元格
                    if self._is_numeric(text):
                        continue
                    
                    nodes.append(BlockNode(
                        node_type=NodeType.TABLE_CELL,
                        text_content=text,
                        metadata={
                            "row": row_idx,
                            "col": col_idx,
                        },
                    ))
        
        return nodes

    def _is_numeric(self, text: str) -> bool:
        """检查是否为纯数字"""
        try:
            float(text.replace(',', ''))
            return True
        except ValueError:
            return False
