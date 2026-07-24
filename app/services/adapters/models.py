"""
数据模型模块 - 定义 Document AST、Block Node、Segment 等核心数据结构

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


class NodeType(Enum):
    """块级节点类型枚举"""
    PARAGRAPH = "paragraph"
    TABLE = "table"
    TABLE_ROW = "table_row"
    TABLE_CELL = "table_cell"
    HEADING = "heading"
    LIST = "list"
    LIST_ITEM = "list_item"
    SLIDE = "slide"
    TEXT = "text"
    NOTE = "note"
    CODEBLOCK = "codeblock"
    CODE_BLOCK = "code_block"
    TITLE = "title"
    SHORTDESC = "shortdesc"
    SECTION = "section"
    INLINE = "inline"


@dataclass
class BlockNode:
    """块级节点 - Document AST 中的结构单元
    
    Attributes:
        node_type: 节点类型
        children: 子节点列表
        text_content: 文本内容（叶子节点）
        metadata: 元数据字典（如 heading level, table rows/columns 等）
    """
    node_type: NodeType
    children: List[BlockNode] = field(default_factory=list)
    text_content: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "node_type": self.node_type.value,
            "children": [c.to_dict() for c in self.children],
            "text_content": self.text_content,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BlockNode:
        """从字典反序列化"""
        return cls(
            node_type=NodeType(data["node_type"]),
            children=[cls.from_dict(c) for c in data.get("children", [])],
            text_content=data.get("text_content"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class DocumentAST:
    """文档抽象语法树
    
    Attributes:
        nodes: 顶层块级节点列表
        source_format: 源文件格式（如 .docx, .txt）
        metadata: 文档级元数据
    """
    nodes: List[BlockNode] = field(default_factory=list)
    source_format: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "source_format": self.source_format,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> DocumentAST:
        """从字典反序列化"""
        return cls(
            nodes=[BlockNode.from_dict(n) for n in data.get("nodes", [])],
            source_format=data.get("source_format", ""),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> DocumentAST:
        """从 JSON 字符串反序列化"""
        data = json.loads(json_str)
        return cls.from_dict(data)


@dataclass
class Segment:
    """翻译片段 - 最小翻译单元
    
    Attributes:
        segment_id: 稳定的唯一标识符
        source_text: 规范化后的源文本（用于匹配）
        display_text: 原始显示文本（保留格式）
        block_path: 在 AST 中的路径（如 "0.children.1"）
        position: 文档内顺序位置
        metadata: 元数据字典（如 DXF 的 handle, layer, 合并信息等）
    """
    segment_id: str
    source_text: str
    display_text: str
    block_path: str
    position: int
    metadata: dict = field(default_factory=dict)
    # 版式原文：保留行内格式标签（⟦1⟧…⟦/1⟧）的带标签句子文本。
    # 仅当来源段落存在 run 级格式差异时才非空，否则留空走无标签路径。
    source_layout_text: str = ""
    # 原文 HTML：把 run 级格式渲染为基础格式标签，供前端原文列展示样式。
    source_html: str = ""
    # 行内样式格式表：{标签 id / "base": [开 span, 闭 span]}，供前端按 ⟦n⟧ 标记渲染译文样式。
    source_format_map: dict = field(default_factory=dict)

    @staticmethod
    def generate_id(block_path: str, position: int, content_hash: str) -> str:
        """生成稳定的 Segment ID
        
        基于块路径、位置和内容哈希生成，确保相同内容在不同位置有不同 ID
        """
        raw = f"{block_path}:{position}:{content_hash}"
        return f"seg-{hashlib.md5(raw.encode()).hexdigest()[:12]}"

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "segment_id": self.segment_id,
            "source_text": self.source_text,
            "display_text": self.display_text,
            "block_path": self.block_path,
            "position": self.position,
            "metadata": self.metadata,
            "source_layout_text": self.source_layout_text,
            "source_html": self.source_html,
            "source_format_map": self.source_format_map,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Segment:
        """从字典反序列化"""
        return cls(
            segment_id=data["segment_id"],
            source_text=data["source_text"],
            display_text=data["display_text"],
            block_path=data["block_path"],
            position=data["position"],
            metadata=data.get("metadata", {}),
            source_layout_text=data.get("source_layout_text", ""),
            source_html=data.get("source_html", ""),
            source_format_map=data.get("source_format_map", {}) or {},
        )


@dataclass
class ParseResult:
    """解析结果
    
    Attributes:
        ast: 文档抽象语法树
        segments: 提取的翻译片段列表
        metadata: 解析元数据（如解析时间、警告等）
    """
    ast: DocumentAST
    segments: List[Segment]
    metadata: dict = field(default_factory=dict)


@dataclass
class TMEntry:
    """翻译记忆条目"""
    source_text: str
    target_text: str
    metadata: dict = field(default_factory=dict)


@dataclass
class TMImportResult:
    """TM 导入结果（XLSX 适配器专用）
    
    Attributes:
        entries: 导入的 TM 条目列表
        skipped_rows: 跳过的行数
        total_rows: 总行数
    """
    entries: List[TMEntry]
    skipped_rows: int
    total_rows: int
