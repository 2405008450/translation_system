"""
MIF 适配器模块 - 解析 Adobe FrameMaker MIF 文件

MIF (Maker Interchange Format) 是 FrameMaker 的文本交换格式。
提取 ParaLine 和 String 元素中的文本内容。
"""
import re
from typing import List, Optional, Tuple

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import (
    BlockNode,
    DocumentAST,
    NodeType,
    ParseResult,
)
from app.services.adapters.segment_extractor import extract_segments


class MifAdapter(FormatAdapter):
    """Adobe FrameMaker MIF 文件适配器"""

    def supported_extensions(self) -> List[str]:
        return [".mif"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".mif"),
                segments=[],
                metadata={},
            )
        
        content = self._decode_content(raw_bytes)
        
        # 验证 MIF 文件头
        if not content.strip().startswith('<MIFFile'):
            raise ParseError(filename="<unknown>", reason="无效的 MIF 文件格式")
        
        nodes = self._parse_mif(content)
        
        ast = DocumentAST(nodes=nodes, source_format=".mif")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"paragraph_count": len(nodes)},
        )

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "iso-8859-1", "cp1252"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ParseError(filename="<unknown>", reason="无法识别文件编码")

    def _parse_mif(self, content: str) -> List[BlockNode]:
        """解析 MIF 文件提取文本"""
        nodes = []
        
        # 提取所有 String 内容
        # MIF 格式: <String `text content'>
        string_pattern = re.compile(r"<String\s+`([^']*)'?>", re.MULTILINE)
        
        current_para = []
        para_id = 0
        
        lines = content.split('\n')
        in_para = False
        
        for line in lines:
            stripped = line.strip()
            
            # 段落开始
            if stripped.startswith('<Para'):
                in_para = True
                current_para = []
                continue
            
            # 段落结束
            if in_para and stripped == '> # end of Para':
                if current_para:
                    text = ' '.join(current_para)
                    if text.strip():
                        nodes.append(BlockNode(
                            node_type=NodeType.PARAGRAPH,
                            text_content=text.strip(),
                            metadata={"para_id": para_id},
                        ))
                        para_id += 1
                in_para = False
                current_para = []
                continue
            
            # 提取 String 内容
            if in_para:
                matches = string_pattern.findall(line)
                for match in matches:
                    # 处理 MIF 转义
                    text = self._unescape_mif(match)
                    if text:
                        current_para.append(text)
        
        return nodes

    def _unescape_mif(self, text: str) -> str:
        """处理 MIF 转义字符"""
        # MIF 使用反斜杠转义
        replacements = [
            ('\\q', "'"),      # 单引号
            ('\\Q', '"'),      # 双引号
            ('\\>', '>'),      # 大于号
            ('\\t', '\t'),     # 制表符
            ('\\n', '\n'),     # 换行（实际上 MIF 用 \\x09）
            ('\\\\', '\\'),    # 反斜杠
        ]
        
        for old, new in replacements:
            text = text.replace(old, new)
        
        # 处理十六进制转义 \xNN
        def hex_replace(match):
            try:
                return chr(int(match.group(1), 16))
            except ValueError:
                return match.group(0)
        
        text = re.sub(r'\\x([0-9a-fA-F]{2})', hex_replace, text)
        
        return text
