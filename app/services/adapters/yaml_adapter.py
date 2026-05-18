"""
YAML 适配器模块 - 解析 YAML 配置文件

提取 YAML 文件中的字符串值用于翻译。
"""
from typing import List

import yaml

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import (
    BlockNode,
    DocumentAST,
    NodeType,
    ParseResult,
)
from app.services.adapters.segment_extractor import extract_segments


class YamlAdapter(FormatAdapter):
    """YAML 文件适配器
    
    解析 YAML 文件，提取所有字符串值。
    """

    def supported_extensions(self) -> List[str]:
        return [".yaml", ".yml"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".yaml"),
                segments=[],
                metadata={},
            )
        
        # 尝试解码
        content = self._decode_content(raw_bytes)
        
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise ParseError(filename="<unknown>", reason=f"YAML 解析错误: {str(e)}")
        
        nodes = []
        self._extract_strings(data, nodes, path=[])
        
        ast = DocumentAST(nodes=nodes, source_format=".yaml")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"key_count": len(nodes)},
        )

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ParseError(filename="<unknown>", reason="无法识别文件编码")

    def _extract_strings(self, data, nodes: List[BlockNode], path: List[str]) -> None:
        """递归提取字符串值"""
        if isinstance(data, str):
            if data.strip():
                key_path = ".".join(path) if path else "root"
                nodes.append(BlockNode(
                    node_type=NodeType.PARAGRAPH,
                    text_content=data,
                    metadata={"key": key_path},
                ))
        elif isinstance(data, dict):
            for key, value in data.items():
                self._extract_strings(value, nodes, path + [str(key)])
        elif isinstance(data, list):
            for i, item in enumerate(data):
                self._extract_strings(item, nodes, path + [f"[{i}]"])
