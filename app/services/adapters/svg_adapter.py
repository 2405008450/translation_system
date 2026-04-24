"""
SVG 适配器模块 - 解析 SVG 文件中的文本元素

Requirements: 11.1, 11.2, 11.3, 11.4, 11.5
"""
from typing import List, Optional

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
            parser = etree.XMLParser(remove_blank_text=True, recover=False)
            root = etree.fromstring(raw_bytes, parser=parser)
        except etree.XMLSyntaxError as e:
            raise ParseError(
                filename="<unknown>",
                reason=f"无法解析 SVG 文件: {str(e)}"
            )
        
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
        
        for idx, text_elem in enumerate(text_elements):
            text_node = self._parse_text_element(text_elem, idx)
            if text_node:
                nodes.append(text_node)
        
        return nodes

    def _parse_text_element(
        self,
        element: etree._Element,
        index: int,
    ) -> Optional[BlockNode]:
        """解析单个 text 元素
        
        Args:
            element: text 元素
            index: 元素索引
            
        Returns:
            Optional[BlockNode]: 文本节点
        """
        # 提取位置属性
        x = element.get("x", "0")
        y = element.get("y", "0")
        
        # 提取样式属性
        style_attrs = {}
        for attr in ("font-family", "font-size", "font-weight", "font-style",
                     "fill", "stroke", "text-anchor", "transform"):
            value = element.get(attr)
            if value:
                style_attrs[attr] = value
        
        # 检查是否有 tspan 子元素
        tspans = element.xpath("svg:tspan | tspan", namespaces=NSMAP)
        
        if tspans:
            # 处理 tspan 子元素
            children = []
            for tspan in tspans:
                tspan_node = self._parse_tspan_element(tspan)
                if tspan_node:
                    children.append(tspan_node)
            
            if not children:
                return None
            
            return BlockNode(
                node_type=NodeType.PARAGRAPH,
                children=children,
                metadata={
                    "svg_element": "text",
                    "index": index,
                    "x": x,
                    "y": y,
                    **style_attrs,
                },
            )
        else:
            # 直接提取文本
            text = self._get_element_text(element)
            if not text.strip():
                return None
            
            return BlockNode(
                node_type=NodeType.PARAGRAPH,
                text_content=text.strip(),
                metadata={
                    "svg_element": "text",
                    "index": index,
                    "x": x,
                    "y": y,
                    **style_attrs,
                },
            )

    def _parse_tspan_element(self, element: etree._Element) -> Optional[BlockNode]:
        """解析 tspan 元素
        
        Args:
            element: tspan 元素
            
        Returns:
            Optional[BlockNode]: 文本节点
        """
        text = self._get_element_text(element)
        if not text.strip():
            return None
        
        # 提取位置属性
        x = element.get("x")
        y = element.get("y")
        dx = element.get("dx")
        dy = element.get("dy")
        
        # 提取样式属性
        style_attrs = {}
        for attr in ("font-family", "font-size", "font-weight", "font-style",
                     "fill", "stroke", "baseline-shift"):
            value = element.get(attr)
            if value:
                style_attrs[attr] = value
        
        metadata = {"svg_element": "tspan", **style_attrs}
        if x:
            metadata["x"] = x
        if y:
            metadata["y"] = y
        if dx:
            metadata["dx"] = dx
        if dy:
            metadata["dy"] = dy
        
        return BlockNode(
            node_type=NodeType.INLINE,
            text_content=text.strip(),
            metadata=metadata,
        )

    def _get_element_text(self, element: etree._Element) -> str:
        """获取元素的直接文本内容
        
        Args:
            element: XML 元素
            
        Returns:
            str: 文本内容
        """
        # 只获取直接文本，不包括子元素
        text = element.text or ""
        return text
