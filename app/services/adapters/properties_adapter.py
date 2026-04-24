"""
Properties 适配器模块 - 解析 Java .properties 文件

支持标准 Java properties 格式，包括：
- key=value 和 key:value 格式
- 反斜杠续行
- Unicode 转义 (\\uXXXX)
- 注释行 (# 或 !)
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


class PropertiesAdapter(FormatAdapter):
    """Java Properties 文件适配器"""

    def supported_extensions(self) -> List[str]:
        return [".properties"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".properties"),
                segments=[],
                metadata={},
            )
        
        content = self._decode_content(raw_bytes)
        entries = self._parse_properties(content)
        
        nodes = []
        for key, value, line_num, comment in entries:
            if value.strip():
                nodes.append(BlockNode(
                    node_type=NodeType.PARAGRAPH,
                    text_content=value,
                    metadata={
                        "key": key,
                        "line": line_num,
                        "comment": comment,
                    },
                ))
        
        ast = DocumentAST(nodes=nodes, source_format=".properties")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"entry_count": len(nodes)},
        )

    def _decode_content(self, raw_bytes: bytes) -> str:
        # Properties 文件默认是 ISO-8859-1，但现代常用 UTF-8
        for encoding in ("utf-8", "utf-8-sig", "iso-8859-1", "gb18030"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ParseError(filename="<unknown>", reason="无法识别文件编码")

    def _parse_properties(self, content: str) -> List[Tuple[str, str, int, str]]:
        """解析 properties 内容
        
        Returns:
            List[Tuple[key, value, line_num, preceding_comment]]
        """
        entries = []
        lines = content.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        
        current_comment = ""
        i = 0
        while i < len(lines):
            line = lines[i]
            line_num = i + 1
            
            # 跳过空行
            if not line.strip():
                current_comment = ""
                i += 1
                continue
            
            # 注释行
            if line.lstrip().startswith(('#', '!')):
                current_comment = line.lstrip()[1:].strip()
                i += 1
                continue
            
            # 处理续行
            full_line = line
            while full_line.rstrip().endswith('\\') and i + 1 < len(lines):
                full_line = full_line.rstrip()[:-1] + lines[i + 1].lstrip()
                i += 1
            
            # 解析键值对
            key, value = self._parse_line(full_line)
            if key:
                # 处理 Unicode 转义
                value = self._unescape_unicode(value)
                entries.append((key, value, line_num, current_comment))
                current_comment = ""
            
            i += 1
        
        return entries

    def _parse_line(self, line: str) -> Tuple[str, str]:
        """解析单行键值对"""
        # 跳过前导空白
        line = line.lstrip()
        
        # 查找分隔符 (= 或 :)
        key = ""
        value = ""
        in_key = True
        escape = False
        
        for i, char in enumerate(line):
            if escape:
                key += char
                escape = False
                continue
            
            if char == '\\':
                escape = True
                continue
            
            if in_key and char in ('=', ':'):
                value = line[i + 1:].lstrip()
                break
            elif in_key and char in (' ', '\t'):
                # 空白也可以作为分隔符
                rest = line[i:].lstrip()
                if rest and rest[0] in ('=', ':'):
                    value = rest[1:].lstrip()
                else:
                    value = rest
                break
            else:
                key += char
        
        return key.strip(), value

    def _unescape_unicode(self, text: str) -> str:
        r"""处理 Unicode 转义序列 \uXXXX"""
        def replace_unicode(match):
            return chr(int(match.group(1), 16))
        
        return re.sub(r'\\u([0-9a-fA-F]{4})', replace_unicode, text)
