"""
DITA 适配器模块 - 解析 DITA XML 文件

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6
"""
from io import BytesIO
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


# DITA 元素到 NodeType 的映射
DITA_ELEMENT_MAP = {
    # 块级元素
    "topic": NodeType.SECTION,
    "concept": NodeType.SECTION,
    "task": NodeType.SECTION,
    "reference": NodeType.SECTION,
    "title": NodeType.HEADING,
    "shortdesc": NodeType.PARAGRAPH,
    "abstract": NodeType.PARAGRAPH,
    "p": NodeType.PARAGRAPH,
    "note": NodeType.NOTE,
    "li": NodeType.LIST_ITEM,
    "ul": NodeType.LIST,
    "ol": NodeType.LIST,
    "table": NodeType.TABLE,
    "row": NodeType.TABLE_ROW,
    "entry": NodeType.TABLE_CELL,
    "simpletable": NodeType.TABLE,
    "strow": NodeType.TABLE_ROW,
    "stentry": NodeType.TABLE_CELL,
    "codeblock": NodeType.CODE_BLOCK,
    "pre": NodeType.CODE_BLOCK,
    "section": NodeType.SECTION,
    "body": NodeType.SECTION,
    "conbody": NodeType.SECTION,
    "taskbody": NodeType.SECTION,
    "refbody": NodeType.SECTION,
}

# 内联元素（需要保留到 metadata）
INLINE_ELEMENTS = {
    "ph", "b", "i", "u", "tt", "sup", "sub",
    "codeph", "filepath", "varname", "cmdname",
    "uicontrol", "menucascade", "wintitle",
    "xref", "link", "cite", "q", "term",
    "keyword", "apiname", "option", "parmname",
}


class DitaAdapter(FormatAdapter):
    """DITA XML 文件适配器
    
    使用 lxml 解析 DITA 文档，映射 DITA 元素到 BlockNode。
    """

    def supported_extensions(self) -> List[str]:
        return [".dita", ".ditamap", ".xml"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        """解析 DITA 文件
        
        Args:
            raw_bytes: 文件字节内容
            
        Returns:
            ParseResult: 解析结果
            
        Raises:
            ParseError: 当文件损坏或无法解析时
        """
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".dita"),
                segments=[],
                metadata={},
            )
        
        try:
            # 解析 XML，移除空白
            parser = etree.XMLParser(remove_blank_text=True, recover=False)
            root = etree.fromstring(raw_bytes, parser=parser)
        except etree.XMLSyntaxError as e:
            raise ParseError(
                filename="<unknown>",
                reason=f"无法解析 DITA 文件: {str(e)}"
            )
        
        nodes = self._parse_element(root)
        
        # 获取文档类型
        doc_type = root.tag
        
        ast = DocumentAST(nodes=nodes, source_format=".dita")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"doc_type": doc_type},
        )

    def _parse_element(self, element: etree._Element) -> List[BlockNode]:
        """递归解析 DITA 元素
        
        Args:
            element: lxml 元素
            
        Returns:
            List[BlockNode]: 块级节点列表
        """
        nodes = []
        tag = etree.QName(element).localname if isinstance(element.tag, str) else element.tag
        
        # 检查是否是 conref（内容引用）
        conref = element.get("conref")
        if conref:
            # 创建占位符节点
            node = BlockNode(
                node_type=NodeType.PARAGRAPH,
                text_content=f"[conref: {conref}]",
                metadata={
                    "dita_tag": tag,
                    "conref": conref,
                    "is_placeholder": True,
                },
            )
            return [node]
        
        # 获取节点类型
        node_type = DITA_ELEMENT_MAP.get(tag)
        
        if node_type:
            # 提取文本内容和内联标签
            text_content, inline_tags = self._extract_text_with_inline(element)
            
            # 递归处理子元素
            children = []
            for child in element:
                child_tag = etree.QName(child).localname if isinstance(child.tag, str) else child.tag
                # 只递归处理块级子元素
                if child_tag in DITA_ELEMENT_MAP:
                    child_nodes = self._parse_element(child)
                    children.extend(child_nodes)
            
            # 构建元数据
            metadata = {"dita_tag": tag}
            if inline_tags:
                metadata["inline_tags"] = inline_tags
            
            # 复制元素属性
            for attr, value in element.attrib.items():
                if attr not in ("conref",):
                    metadata[f"attr_{attr}"] = value
            
            # 创建节点
            if text_content.strip() or children:
                node = BlockNode(
                    node_type=node_type,
                    text_content=text_content.strip() if text_content.strip() else None,
                    children=children if children else None,
                    metadata=metadata,
                )
                nodes.append(node)
        else:
            # 非映射元素，递归处理子元素
            for child in element:
                child_nodes = self._parse_element(child)
                nodes.extend(child_nodes)
        
        return nodes

    def _extract_text_with_inline(self, element: etree._Element) -> tuple[str, List[dict]]:
        """提取元素文本，同时记录内联标签
        
        Args:
            element: lxml 元素
            
        Returns:
            tuple[str, List[dict]]: (纯文本, 内联标签列表)
        """
        inline_tags = []
        text_parts = []
        
        # 处理元素的直接文本
        if element.text:
            text_parts.append(element.text)
        
        # 处理子元素
        for child in element:
            child_tag = etree.QName(child).localname if isinstance(child.tag, str) else child.tag
            
            if child_tag in INLINE_ELEMENTS:
                # 记录内联标签
                start_pos = len("".join(text_parts))
                child_text = self._get_all_text(child)
                
                inline_tags.append({
                    "tag": child_tag,
                    "start": start_pos,
                    "end": start_pos + len(child_text),
                    "attrs": dict(child.attrib),
                })
                
                text_parts.append(child_text)
            elif child_tag not in DITA_ELEMENT_MAP:
                # 未知元素，提取文本
                text_parts.append(self._get_all_text(child))
            
            # 处理尾部文本
            if child.tail:
                text_parts.append(child.tail)
        
        return "".join(text_parts), inline_tags

    def _get_all_text(self, element: etree._Element) -> str:
        """获取元素及其所有子元素的文本
        
        Args:
            element: lxml 元素
            
        Returns:
            str: 所有文本内容
        """
        return "".join(element.itertext())
