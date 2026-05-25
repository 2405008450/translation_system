"""
TXT 适配器模块 - 解析纯文本文件

Requirements: 6.1, 6.2, 6.3, 6.4
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


# 支持的编码列表（utf-8-sig 必须在 utf-8 之前以正确检测 BOM）
SUPPORTED_ENCODINGS = ["utf-8-sig", "utf-8", "gb18030"]


class TxtAdapter(FormatAdapter):
    """TXT 文件适配器
    
    支持 UTF-8、UTF-8-BOM 和 GB18030 编码的纯文本文件。
    按空行分割段落。
    """

    def supported_extensions(self) -> List[str]:
        return [".txt", ".dat"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        """解析 TXT 文件
        
        Args:
            raw_bytes: 文件字节内容
            
        Returns:
            ParseResult: 解析结果
            
        Raises:
            ParseError: 当编码检测失败时
        """
        # 空文件处理
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".txt"),
                segments=[],
                metadata={"encoding": None},
            )
        
        # 尝试解码
        text, encoding = self._decode_text(raw_bytes)
        
        # 检查是否只有空白
        if not text or not text.strip():
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".txt"),
                segments=[],
                metadata={"encoding": encoding},
            )
        
        # 按空行分割段落
        paragraphs = self._split_paragraphs(text)
        
        # 构建 AST
        nodes = []
        for para_text in paragraphs:
            if para_text.strip():  # 跳过空段落
                node = BlockNode(
                    node_type=NodeType.PARAGRAPH,
                    text_content=para_text.strip(),
                )
                nodes.append(node)
        
        ast = DocumentAST(nodes=nodes, source_format=".txt")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"encoding": encoding, "paragraph_count": len(nodes)},
        )

    def _decode_text(self, raw_bytes: bytes) -> tuple[str, str]:
        """尝试使用支持的编码解码文本
        
        Args:
            raw_bytes: 原始字节
            
        Returns:
            tuple[str, str]: (解码后的文本, 使用的编码)
            
        Raises:
            ParseError: 当所有编码都失败时
        """
        for encoding in SUPPORTED_ENCODINGS:
            try:
                text = raw_bytes.decode(encoding)
                return text, encoding
            except UnicodeDecodeError:
                continue
        
        raise ParseError(
            filename="<unknown>",
            reason=f"无法识别文件编码，请使用 UTF-8 或 GB18030 编码。"
                   f"尝试过的编码: {', '.join(SUPPORTED_ENCODINGS)}"
        )

    def _split_paragraphs(self, text: str) -> List[str]:
        """按空行分割段落
        
        Args:
            text: 原始文本
            
        Returns:
            List[str]: 段落列表
        """
        # 统一换行符
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        
        # 按连续空行分割
        paragraphs = re.split(r'\n\s*\n', text)
        
        # 过滤空段落并清理
        return [p.strip() for p in paragraphs if p.strip()]
