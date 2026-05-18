"""
Strings 适配器模块 - 解析 iOS/macOS .strings 文件

支持标准 Apple strings 格式：
- "key" = "value"; 格式
- /* 注释 */ 格式
- // 单行注释
- Unicode 转义
"""
import re
from typing import List, Tuple

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import (
    BlockNode,
    DocumentAST,
    NodeType,
    ParseResult,
)
from app.services.adapters.segment_extractor import extract_segments


class StringsAdapter(FormatAdapter):
    """iOS/macOS Strings 文件适配器"""

    def supported_extensions(self) -> List[str]:
        return [".strings"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".strings"),
                segments=[],
                metadata={},
            )
        
        content = self._decode_content(raw_bytes)
        entries = self._parse_strings(content)
        
        nodes = []
        for key, value, comment in entries:
            if value.strip():
                nodes.append(BlockNode(
                    node_type=NodeType.PARAGRAPH,
                    text_content=value,
                    metadata={
                        "key": key,
                        "comment": comment,
                    },
                ))
        
        ast = DocumentAST(nodes=nodes, source_format=".strings")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"entry_count": len(nodes)},
        )

    def _decode_content(self, raw_bytes: bytes) -> str:
        # .strings 文件可能是 UTF-16 或 UTF-8
        for encoding in ("utf-16", "utf-8", "utf-8-sig", "utf-16-le", "utf-16-be"):
            try:
                return raw_bytes.decode(encoding)
            except (UnicodeDecodeError, UnicodeError):
                continue
        raise ParseError(filename="<unknown>", reason="无法识别文件编码")

    def _parse_strings(self, content: str) -> List[Tuple[str, str, str]]:
        """解析 strings 文件
        
        Returns:
            List[Tuple[key, value, comment]]
        """
        entries = []
        
        # 移除块注释并记录
        comments = {}
        def save_comment(match):
            pos = match.start()
            comments[pos] = match.group(1).strip()
            return f"__COMMENT_{pos}__"
        
        content_no_block = re.sub(r'/\*(.+?)\*/', save_comment, content, flags=re.DOTALL)
        
        # 移除单行注释
        lines = []
        for line in content_no_block.split('\n'):
            # 保留注释占位符
            if '__COMMENT_' in line:
                lines.append(line)
            else:
                # 移除 // 注释
                line = re.sub(r'//.*$', '', line)
                lines.append(line)
        
        content_clean = '\n'.join(lines)
        
        # 匹配 "key" = "value"; 模式
        pattern = r'(?:__COMMENT_(\d+)__\s*)?"([^"\\]*(?:\\.[^"\\]*)*)"\s*=\s*"([^"\\]*(?:\\.[^"\\]*)*)"\s*;'
        
        for match in re.finditer(pattern, content_clean):
            comment_pos = match.group(1)
            key = self._unescape(match.group(2))
            value = self._unescape(match.group(3))
            
            comment = ""
            if comment_pos and int(comment_pos) in comments:
                comment = comments[int(comment_pos)]
            
            entries.append((key, value, comment))
        
        return entries

    def _unescape(self, text: str) -> str:
        """处理转义字符"""
        replacements = [
            (r'\"', '"'),
            (r"\'", "'"),
            (r'\\n', '\n'),
            (r'\\t', '\t'),
            (r'\\r', '\r'),
            (r'\\\\', '\\'),
        ]
        for old, new in replacements:
            text = text.replace(old, new)
        
        # 处理 Unicode 转义 \U0000
        def replace_unicode(match):
            return chr(int(match.group(1), 16))
        
        text = re.sub(r'\\U([0-9a-fA-F]{4})', replace_unicode, text)
        return text
