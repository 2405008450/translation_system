"""
Markdown 适配器模块 - 解析 Markdown 文件

提取 Markdown 中的可翻译文本，保留结构信息。
"""
import re
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


class MarkdownAdapter(FormatAdapter):
    """Markdown 文件适配器"""

    def supported_extensions(self) -> List[str]:
        return [".md", ".markdown"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        return self._parse_with_code_block_option(raw_bytes, include_code_blocks=False)

    def parse_with_options(self, raw_bytes: bytes, filename: str = "<unknown>", options: dict | None = None) -> ParseResult:
        self.validate_file_size(raw_bytes, filename)
        include_code_blocks = bool((options or {}).get("translate_code_blocks", True))
        return self._parse_with_code_block_option(raw_bytes, include_code_blocks=include_code_blocks)

    def _parse_with_code_block_option(self, raw_bytes: bytes, include_code_blocks: bool) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".md"),
                segments=[],
                metadata={},
            )
        
        content = self._decode_content(raw_bytes)
        nodes = self._parse_markdown(content, include_code_blocks=include_code_blocks)
        
        ast = DocumentAST(nodes=nodes, source_format=".md")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"node_count": len(nodes), "include_code_blocks": include_code_blocks},
        )

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ParseError(filename="<unknown>", reason="无法识别文件编码")

    def _parse_markdown(self, content: str, include_code_blocks: bool = False) -> List[BlockNode]:
        """解析 Markdown 内容"""
        nodes = []
        lines = content.replace('\r\n', '\n').split('\n')
        
        i = 0
        in_code_block = False
        code_lang = ""
        code_start_line = 0
        code_lines = []
        current_para = []
        
        while i < len(lines):
            line = lines[i]
            
            # 代码块
            if line.startswith('```'):
                if in_code_block:
                    if include_code_blocks:
                        self._flush_code_block(code_lines, nodes, code_lang, code_start_line)
                        code_lines = []
                    in_code_block = False
                    code_lang = ""
                    code_start_line = 0
                else:
                    self._flush_paragraph(current_para, nodes)
                    current_para = []
                    in_code_block = True
                    code_lang = line[3:].strip()
                    code_start_line = i + 1
                i += 1
                continue
            
            if in_code_block:
                if include_code_blocks:
                    code_lines.append(line)
                i += 1
                continue
            
            # 空行
            if not line.strip():
                self._flush_paragraph(current_para, nodes)
                current_para = []
                i += 1
                continue
            
            # 标题
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if heading_match:
                self._flush_paragraph(current_para, nodes)
                current_para = []
                level = len(heading_match.group(1))
                text = heading_match.group(2).strip()
                nodes.append(BlockNode(
                    node_type=NodeType.HEADING,
                    text_content=text,
                    metadata={"level": level, "line": i + 1},
                ))
                i += 1
                continue
            
            # 列表项
            list_match = re.match(r'^(\s*)([-*+]|\d+\.)\s+(.+)$', line)
            if list_match:
                self._flush_paragraph(current_para, nodes)
                current_para = []
                indent = len(list_match.group(1))
                marker = list_match.group(2)
                text = list_match.group(3).strip()
                nodes.append(BlockNode(
                    node_type=NodeType.LIST_ITEM,
                    text_content=text,
                    metadata={
                        "indent": indent,
                        "marker": marker,
                        "ordered": marker[-1] == '.',
                        "line": i + 1,
                    },
                ))
                i += 1
                continue
            
            # 引用
            if line.startswith('>'):
                self._flush_paragraph(current_para, nodes)
                current_para = []
                text = line.lstrip('>').strip()
                if text:
                    nodes.append(BlockNode(
                        node_type=NodeType.PARAGRAPH,
                        text_content=text,
                        metadata={"blockquote": True, "line": i + 1},
                    ))
                i += 1
                continue
            
            # 普通段落
            current_para.append(line)
            i += 1
        
        self._flush_paragraph(current_para, nodes)
        if in_code_block and include_code_blocks:
            self._flush_code_block(code_lines, nodes, code_lang, code_start_line)
        return nodes

    def _flush_paragraph(self, lines: List[str], nodes: List[BlockNode]) -> None:
        """保存段落"""
        if not lines:
            return
        text = ' '.join(line.strip() for line in lines if line.strip())
        if text:
            nodes.append(BlockNode(
                node_type=NodeType.PARAGRAPH,
                text_content=text,
                metadata={},
            ))

    def _flush_code_block(
        self,
        lines: List[str],
        nodes: List[BlockNode],
        language: str,
        start_line: int,
    ) -> None:
        """保存可翻译代码块内容。"""
        text = "\n".join(lines).strip()
        if text:
            nodes.append(BlockNode(
                node_type=NodeType.CODE_BLOCK,
                text_content=text,
                metadata={"language": language, "line": start_line},
            ))
