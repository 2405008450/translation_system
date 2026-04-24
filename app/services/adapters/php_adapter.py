"""
PHP 适配器模块 - 解析 PHP 文件中的可翻译字符串

提取 PHP 文件中的翻译函数调用，如：
- __('text')
- _e('text')
- _x('text', 'context')
- _n('singular', 'plural', $count)
- esc_html__('text')
- esc_attr__('text')
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


# PHP 翻译函数模式
TRANSLATION_PATTERNS = [
    # WordPress 风格
    r"__\s*\(\s*['\"](.+?)['\"]\s*(?:,|\))",
    r"_e\s*\(\s*['\"](.+?)['\"]\s*(?:,|\))",
    r"_x\s*\(\s*['\"](.+?)['\"]\s*,",
    r"_n\s*\(\s*['\"](.+?)['\"]\s*,\s*['\"](.+?)['\"]\s*,",
    r"esc_html__\s*\(\s*['\"](.+?)['\"]\s*(?:,|\))",
    r"esc_attr__\s*\(\s*['\"](.+?)['\"]\s*(?:,|\))",
    r"esc_html_e\s*\(\s*['\"](.+?)['\"]\s*(?:,|\))",
    r"esc_attr_e\s*\(\s*['\"](.+?)['\"]\s*(?:,|\))",
    # Laravel 风格
    r"trans\s*\(\s*['\"](.+?)['\"]\s*(?:,|\))",
    r"@lang\s*\(\s*['\"](.+?)['\"]\s*\)",
    # 通用 gettext
    r"gettext\s*\(\s*['\"](.+?)['\"]\s*\)",
    r"ngettext\s*\(\s*['\"](.+?)['\"]\s*,\s*['\"](.+?)['\"]\s*,",
]


class PhpAdapter(FormatAdapter):
    """PHP 文件适配器
    
    解析 PHP 文件，提取翻译函数中的字符串。
    """

    def supported_extensions(self) -> List[str]:
        return [".php"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".php"),
                segments=[],
                metadata={},
            )
        
        content = self._decode_content(raw_bytes)
        strings = self._extract_translation_strings(content)
        
        nodes = []
        seen = set()  # 去重
        
        for text, func_name, line_num in strings:
            if text and text not in seen:
                seen.add(text)
                nodes.append(BlockNode(
                    node_type=NodeType.PARAGRAPH,
                    text_content=text,
                    metadata={
                        "function": func_name,
                        "line": line_num,
                    },
                ))
        
        ast = DocumentAST(nodes=nodes, source_format=".php")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"string_count": len(nodes)},
        )

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ParseError(filename="<unknown>", reason="无法识别文件编码")

    def _extract_translation_strings(self, content: str) -> List[Tuple[str, str, int]]:
        """提取翻译字符串
        
        Returns:
            List[Tuple[str, str, int]]: (文本, 函数名, 行号) 列表
        """
        results = []
        lines = content.split('\n')
        
        for line_num, line in enumerate(lines, 1):
            for pattern in TRANSLATION_PATTERNS:
                matches = re.finditer(pattern, line, re.DOTALL)
                for match in matches:
                    # 提取函数名
                    func_match = re.match(r'(\w+)', pattern.replace(r'\s*', ''))
                    func_name = func_match.group(1) if func_match else "unknown"
                    
                    # 提取所有捕获组（可能有多个，如 _n 的单复数）
                    for group in match.groups():
                        if group:
                            # 处理转义字符
                            text = self._unescape_php_string(group)
                            results.append((text, func_name, line_num))
        
        return results

    def _unescape_php_string(self, text: str) -> str:
        """处理 PHP 字符串转义"""
        replacements = [
            (r"\'", "'"),
            (r'\"', '"'),
            (r"\\n", "\n"),
            (r"\\t", "\t"),
            (r"\\\\", "\\"),
        ]
        for old, new in replacements:
            text = text.replace(old, new)
        return text
