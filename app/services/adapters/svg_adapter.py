"""
SVG 适配器模块 - 解析 SVG 文件中的文本元素

Requirements: 11.1, 11.2, 11.3, 11.4, 11.5
"""
from typing import List

from lxml import etree

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import (
    BlockNode,
    DocumentAST,
    NodeType,
    ParseResult,
)
from app.services.adapters.segment_extractor import extract_segments
from app.services.adapters.svg_text_units import (
    collect_text_slots,
    group_logical_text_units,
    is_simulated_vertical_unit,
    unit_source_text,
)


# SVG 命名空间
SVG_NS = "http://www.w3.org/2000/svg"
NSMAP = {"svg": SVG_NS}


class SvgAdapter(FormatAdapter):
    """SVG 文件适配器
    
    使用 lxml 解析 SVG 文件，提取 text 和 tspan 元素中的文本。
    """

    def supported_extensions(self) -> List[str]:
        return [".svg"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        """解析 SVG 文件
        
        Args:
            raw_bytes: 文件字节内容
            
        Returns:
            ParseResult: 解析结果
            
        Raises:
            ParseError: 当文件损坏或无法解析时
        """
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".svg"),
                segments=[],
                metadata={},
            )
        
        try:
            parser = etree.XMLParser(
                remove_blank_text=False,
                recover=False,
                resolve_entities="internal",
                no_network=True,
            )
            root = etree.fromstring(raw_bytes, parser=parser)
        except etree.XMLSyntaxError as e:
            raise ParseError(
                filename="<unknown>",
                reason=f"无法解析 SVG 文件: {str(e)}"
            )
        
        if etree.QName(root).localname.lower() != "svg":
            raise ParseError(filename="<unknown>", reason="根元素不是 SVG")

        nodes = self._extract_text_elements(root)
        
        # 获取 SVG 尺寸
        width = root.get("width", "unknown")
        height = root.get("height", "unknown")
        viewbox = root.get("viewBox", "")
        
        ast = DocumentAST(nodes=nodes, source_format=".svg")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={
                "width": width,
                "height": height,
                "viewBox": viewbox,
                "text_count": len(nodes),
            },
        )

    def _extract_text_elements(self, root: etree._Element) -> List[BlockNode]:
        """提取所有文本元素
        
        Args:
            root: SVG 根元素
            
        Returns:
            List[BlockNode]: 文本节点列表
        """
        nodes = []
        
        tree = root.getroottree()
        logical_units = group_logical_text_units(collect_text_slots(root))
        for unit_index, unit in enumerate(logical_units):
            first = unit[0]
            node = self._build_text_node(
                owner=first.owner,
                slot_kind=first.slot_kind,
                text=unit_source_text(unit),
                text_element=first.text_element,
                text_index=first.text_index,
                unit_index=unit_index,
                node_path=tree.getpath(first.owner),
            )
            if len(unit) > 1:
                node.metadata["svg_slot_count"] = len(unit)
                node.metadata["svg_node_paths"] = [tree.getpath(slot.owner) for slot in unit]
            if is_simulated_vertical_unit(unit):
                node.metadata["svg_simulated_vertical"] = True
            nodes.append(node)
        
        return nodes

    def _build_text_node(
        self,
        *,
        owner: etree._Element,
        slot_kind: str,
        text: str,
        text_element: etree._Element,
        text_index: int,
        unit_index: int,
        node_path: str,
    ) -> BlockNode:
        """为一个可回写的 SVG 文本槽位创建 AST 节点。"""
        metadata = {
            "svg_element": etree.QName(owner).localname,
            "svg_slot_kind": slot_kind,
            "svg_text_index": text_index,
            "svg_unit_index": unit_index,
            "svg_node_path": node_path,
            "preserve_as_single_segment": True,
        }

        # 位置和样式可能定义在 text、tspan 或其他文本容器上；槽位自身优先。
        for attr in (
            "x", "y", "dx", "dy", "font-family", "font-size", "font-weight",
            "font-style", "fill", "stroke", "text-anchor", "transform",
            "baseline-shift",
        ):
            value = owner.get(attr)
            if value is None and owner is not text_element:
                value = text_element.get(attr)
            if value is not None:
                metadata[attr] = value

        return BlockNode(
            node_type=NodeType.PARAGRAPH,
            text_content=text.strip(),
            metadata=metadata,
        )
