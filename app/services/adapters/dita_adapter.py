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
    Segment,
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
    "dl": NodeType.LIST,
    "dlentry": NodeType.LIST_ITEM,
    "dt": NodeType.HEADING,
    "dd": NodeType.PARAGRAPH,
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

# 某些 Arbortext DITA 会把 term 直接放在结构容器下，作为后续列表或段落的
# 小标题。此时 term 虽然通常是内联元素，但在当前上下文中必须独立成句。
STANDALONE_TERM_PARENT_ELEMENTS = {
    "topic", "concept", "task", "reference",
    "body", "conbody", "taskbody", "refbody", "section",
}

# 这些元素会把同一父元素中的 XML 文本拆成不同文本槽。xref/link 自身的
# 显示文本需要独立翻译；image 虽无文本，也必须阻止前后文本被错误拼接。
SEGMENT_BOUNDARY_ELEMENTS = {"xref", "link", "image"}
TRANSLATABLE_BOUNDARY_ELEMENTS = {"xref", "link"}
FORMATTING_INLINE_ELEMENTS = {
    "ph", "b", "i", "u", "tt", "sup", "sub", "codeph",
    "filepath", "varname", "cmdname", "uicontrol", "menucascade",
    "wintitle", "cite", "q", "keyword", "apiname", "option",
    "parmname",
}


class DitaAdapter(FormatAdapter):
    """DITA XML 文件适配器
    
    使用 lxml 解析 DITA 文档，映射 DITA 元素到 BlockNode。
    """

    def supported_extensions(self) -> List[str]:
        return [".dita", ".ditamap", ".xml"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        return self._parse_with_options(raw_bytes, no_split=False)

    def parse_with_options(self, raw_bytes: bytes, filename: str = "<unknown>", options: dict | None = None) -> ParseResult:
        self.validate_file_size(raw_bytes, filename)
        return self._parse_with_options(
            raw_bytes,
            no_split=bool((options or {}).get("xml_inline_elements_no_split", True)),
        )

    def _parse_with_options(self, raw_bytes: bytes, no_split: bool) -> ParseResult:
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
        segments = self._extract_unsplit_segments(ast) if no_split else extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"doc_type": doc_type, "xml_inline_elements_no_split": no_split},
        )

    def _parse_element(self, element: etree._Element) -> List[BlockNode]:
        """递归解析 DITA 元素
        
        Args:
            element: lxml 元素
            
        Returns:
            List[BlockNode]: 块级节点列表
        """
        # 跳过非元素节点（如处理指令、注释等）
        if not isinstance(element.tag, str):
            return []
        
        nodes = []
        tag = etree.QName(element).localname
        
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
        if node_type is None and self._is_standalone_term(element):
            node_type = NodeType.PARAGRAPH
        if node_type is None and self._is_translatable_boundary(element):
            node_type = NodeType.PARAGRAPH
        if node_type is None and self._contains_segment_boundary(element):
            # xref/link 有时会被 b/ph 等格式标签包裹。包装元素也必须建成
            # 节点，才能保留其自身的直接文本，并继续向下拆出引用文本。
            node_type = NodeType.PARAGRAPH
        
        if node_type:
            # 提取文本内容和内联标签
            text_content, inline_tags = self._extract_text_with_inline(element)
            
            # 递归处理子元素
            children = []
            structural_children = {
                child
                for child in element
                if isinstance(child.tag, str) and self._is_structural_child(child)
            }
            has_structural_children = bool(structural_children)
            for child in element:
                # 跳过非元素节点
                if not isinstance(child.tag, str):
                    continue
                # DITA 表格等结构中可能存在未显式映射的容器元素，
                # 例如 table/tgroup/tbody/row。只要其后代包含块级元素，
                # 就必须按结构递归，不能把整个容器误当作内联文本聚合。
                is_structural = child in structural_children
                is_detached_inline = (
                    has_structural_children
                    and not is_structural
                    and self._is_inline_element(child)
                )
                if is_structural or is_detached_inline:
                    child_nodes = (
                        self._parse_detached_inline(child)
                        if is_detached_inline
                        else self._parse_element(child)
                    )
                    children.extend(child_nodes)
                    if (
                        child.tail
                        and self._has_translatable_text(child.tail)
                    ):
                        children.append(BlockNode(
                            node_type=NodeType.PARAGRAPH,
                            text_content=child.tail.strip(),
                            metadata={
                                "dita_tag": tag,
                                "xml_text_slot": "tail",
                                "after_dita_tag": etree.QName(child).localname,
                            },
                        ))
            
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
                # 跳过非元素节点
                if not isinstance(child.tag, str):
                    continue
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
        
        has_structural_children = any(
            isinstance(child.tag, str) and self._is_structural_child(child)
            for child in element
        )

        # 处理子元素
        for child in element:
            # 跳过非元素节点（处理指令、注释等）
            if not isinstance(child.tag, str):
                if child.tail:
                    text_parts.append(child.tail)
                continue
            
            child_tag = etree.QName(child).localname
            
            is_boundary = self._is_structural_child(child)
            is_detached_inline = (
                has_structural_children
                and not is_boundary
                and self._is_inline_element(child)
            )

            if is_boundary or is_detached_inline:
                # 边界元素及其 tail 由独立节点处理，不能聚合进父句段。
                pass
            elif self._is_nonlinguistic_inline(child):
                # 纯编号/符号等内容无需翻译，并且不能让父文本因拼接后无法
                # 与原始 XML 文本槽精确匹配。
                pass
            elif child_tag in INLINE_ELEMENTS and not self._is_standalone_term(child):
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
            elif not self._is_structural_child(child):
                # 未知元素，提取文本
                text_parts.append(self._get_all_text(child))
            
            # 处理尾部文本
            if child.tail and not is_boundary and not is_detached_inline:
                text_parts.append(child.tail)
        
        return "".join(text_parts), inline_tags

    def _is_structural_child(self, element: etree._Element) -> bool:
        """判断子元素是否承载块级结构，而不是普通内联文本。

        DITA 允许通过专用化或中间容器包装标准块级元素。例如标准表格的
        ``<table>`` 与 ``<row>`` 之间通常还有 ``<tgroup>/<tbody>``。
        这些容器即使没有出现在映射表中，也不能整体聚合成父节点文本。
        """
        if not isinstance(element.tag, str):
            return False

        tag = etree.QName(element).localname
        if self._is_segment_boundary(element):
            return True
        if tag in INLINE_ELEMENTS:
            if self._is_standalone_term(element):
                return True
            # 格式包装中若还有 xref/link，包装本身也要成为结构边界；
            # 否则外层会先把所有 itertext 再次合并。
            return self._contains_segment_boundary(element)
        if tag in DITA_ELEMENT_MAP:
            return True

        for descendant in element.iterdescendants():
            if not isinstance(descendant.tag, str):
                continue
            descendant_tag = etree.QName(descendant).localname
            if descendant_tag in DITA_ELEMENT_MAP:
                return True
        return False

    def _is_standalone_term(self, element: etree._Element) -> bool:
        """判断 term 是否在当前上下文中充当独立的小标题。"""
        if not isinstance(element.tag, str):
            return False
        if etree.QName(element).localname != "term":
            return False

        parent = element.getparent()
        if parent is None or not isinstance(parent.tag, str):
            return False

        parent_tag = etree.QName(parent).localname
        if parent_tag not in STANDALONE_TERM_PARENT_ELEMENTS:
            return False

        # ``<term>术语</term>后续正文`` 仍属于一个连续的内联语句；只有
        # term 后面没有非空尾文本时，才按结构性小标题处理。
        return not bool(element.tail and element.tail.strip())

    def _is_segment_boundary(self, element: etree._Element) -> bool:
        """判断元素是否需要切断父级文本聚合。"""
        if not isinstance(element.tag, str):
            return False
        tag = etree.QName(element).localname
        return (
            tag in SEGMENT_BOUNDARY_ELEMENTS
            or self._is_term_label(element)
            or self._is_redundant_formatting_boundary(element)
            or self._is_leading_format_label(element)
        )

    def _is_translatable_boundary(self, element: etree._Element) -> bool:
        """判断边界元素自身是否包含需要翻译的可见文本。"""
        if not isinstance(element.tag, str):
            return False
        tag = etree.QName(element).localname
        return (
            tag in TRANSLATABLE_BOUNDARY_ELEMENTS
            or self._is_term_label(element)
            or self._is_redundant_formatting_boundary(element)
            or (
                self._is_leading_format_label(element)
                and not self._is_fixed_code_label(element)
            )
        )

    def _is_term_label(self, element: etree._Element) -> bool:
        """识别 ``<p><term>标签</term>正文`` 形式的段首标签。"""
        if not isinstance(element.tag, str):
            return False
        if etree.QName(element).localname != "term":
            return False
        parent = element.getparent()
        if parent is None or not isinstance(parent.tag, str):
            return False
        parent_tag = etree.QName(parent).localname
        return (
            parent_tag not in {"title"}
            and not bool(parent.text and parent.text.strip())
            and bool(element.tail and self._has_translatable_text(element.tail))
        )

    def _is_redundant_formatting_boundary(self, element: etree._Element) -> bool:
        """识别 ``<b><b>文本</b>...</b>`` 等重复格式嵌套。"""
        if not isinstance(element.tag, str):
            return False
        tag = etree.QName(element).localname
        if tag not in FORMATTING_INLINE_ELEMENTS:
            return False
        parent = element.getparent()
        return (
            parent is not None
            and isinstance(parent.tag, str)
            and etree.QName(parent).localname == tag
        )

    def _is_leading_format_label(self, element: etree._Element) -> bool:
        """识别 ``<p><b>ACC：</b>正文</p>`` 形式的段首标签。

        标签文本与其后的 tail 属于不同 XML 文本槽。若继续聚合成一个
        句段，导出器无法把整句译文精确写回任一文本槽。
        """
        if not isinstance(element.tag, str):
            return False
        if etree.QName(element).localname != "b":
            return False

        parent = element.getparent()
        if parent is None or not isinstance(parent.tag, str):
            return False
        if etree.QName(parent).localname != "p":
            return False
        if parent.text and parent.text.strip():
            return False

        # 必须是段落中的第一个元素子节点，避免把普通句中间的加粗内容
        # 错判为段首标签。
        previous = element.getprevious()
        while previous is not None:
            if isinstance(previous.tag, str):
                return False
            previous = previous.getprevious()

        label = self._get_all_text(element).strip()
        return (
            bool(label)
            and label.endswith((":", "："))
            and bool(element.tail and self._has_translatable_text(element.tail))
        )

    def _is_fixed_code_label(self, element: etree._Element) -> bool:
        """判断段首标签是否为无需翻译的大写代码，如 OFF/ACC/ON。"""
        if not self._is_leading_format_label(element):
            return False

        label = self._get_all_text(element).strip().rstrip(":：").strip()
        return (
            bool(label)
            and any(char.isalnum() for char in label)
            and all(
                not char.isalpha() or (char.isascii() and char.isupper())
                for char in label
            )
        )

    def _contains_segment_boundary(self, element: etree._Element) -> bool:
        """判断格式包装元素内部是否含真正的句段边界。"""
        if not isinstance(element.tag, str):
            return False
        return any(
            self._is_segment_boundary(descendant)
            for descendant in element.iterdescendants()
            if isinstance(descendant.tag, str)
        )

    def _is_inline_element(self, element: etree._Element) -> bool:
        return (
            isinstance(element.tag, str)
            and etree.QName(element).localname in INLINE_ELEMENTS
        )

    def _is_nonlinguistic_inline(self, element: etree._Element) -> bool:
        """识别不应并入待译源文的纯数字/符号内联内容。"""
        if not self._is_inline_element(element):
            return False
        text = self._get_all_text(element).strip()
        return bool(text) and not any(char.isalpha() for char in text)

    def _parse_detached_inline(self, element: etree._Element) -> List[BlockNode]:
        """解析混合块结构之后出现的内联元素。"""
        if self._is_nonlinguistic_inline(element):
            return []

        text_content, inline_tags = self._extract_text_with_inline(element)
        children: List[BlockNode] = []
        for child in element:
            if not isinstance(child.tag, str):
                continue
            if self._is_structural_child(child):
                children.extend(self._parse_element(child))
                if child.tail and self._has_translatable_text(child.tail):
                    children.append(BlockNode(
                        node_type=NodeType.PARAGRAPH,
                        text_content=child.tail.strip(),
                        metadata={
                            "dita_tag": etree.QName(element).localname,
                            "xml_text_slot": "tail",
                            "after_dita_tag": etree.QName(child).localname,
                        },
                    ))

        metadata = {"dita_tag": etree.QName(element).localname}
        if inline_tags:
            metadata["inline_tags"] = inline_tags
        if not text_content.strip() and not children:
            return []
        return [BlockNode(
            node_type=NodeType.PARAGRAPH,
            text_content=text_content.strip() or None,
            children=children or None,
            metadata=metadata,
        )]

    def _has_translatable_text(self, text: str) -> bool:
        """过滤只含空白或标点的 XML tail，避免产生无意义句段。"""
        return any(char.isalnum() for char in text)

    def _get_all_text(self, element: etree._Element) -> str:
        """获取元素及其所有子元素的文本
        
        Args:
            element: lxml 元素
            
        Returns:
            str: 所有文本内容
        """
        return "".join(element.itertext())

    def _extract_unsplit_segments(self, ast: DocumentAST) -> List[Segment]:
        """按块提取 XML 文本，不再对块内文本做二次断句。"""
        segments: List[Segment] = []
        position = 0

        def visit(node: BlockNode, path: str) -> None:
            nonlocal position
            if node.text_content and node.text_content.strip():
                display_text = node.text_content.strip()
                source_text = " ".join(display_text.split())
                segments.append(Segment(
                    segment_id=f"seg-{position + 1:06d}",
                    source_text=source_text,
                    display_text=display_text,
                    block_path=path,
                    position=position,
                    metadata=node.metadata,
                ))
                position += 1

            for child_index, child in enumerate(node.children or []):
                visit(child, f"{path}.children.{child_index}")

        for index, node in enumerate(ast.nodes):
            visit(node, str(index))

        return segments
