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
        
        # 查找所有 text 元素（支持带命名空间和不带命名空间）
        text_elements = root.xpath(
            "//svg:text | //text",
            namespaces=NSMAP
        )
        
        unit_index = 0
        tree = root.getroottree()
        for text_index, text_elem in enumerate(text_elements):
            for owner, slot_kind, text in self._iter_text_slots(text_elem):
                node = self._build_text_node(
                    owner=owner,
                    slot_kind=slot_kind,
                    text=text,
                    text_element=text_elem,
                    text_index=text_index,
                    unit_index=unit_index,
                    node_path=tree.getpath(owner),
                )
                nodes.append(node)
                unit_index += 1
        
        return nodes

    def _iter_text_slots(
        self,
        text_element: etree._Element,
    ):
        """按 XML 文档顺序枚举 ``text`` 内的实际文本槽位。"""
        if text_element.text and text_element.text.strip():
            yield text_element, "text", text_element.text
        for child in text_element:
            yield from self._iter_text_slots(child)
            if child.tail and child.tail.strip():
                yield child, "tail", child.tail

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
