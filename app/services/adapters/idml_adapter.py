"""
IDML 适配器模块 - 解析 Adobe InDesign IDML 文件

IDML 是一个 ZIP 压缩包，包含多个 XML 文件。
主要从 Stories 目录中的 Story_*.xml 文件提取文本。
"""
import zipfile
from io import BytesIO
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


# InDesign 命名空间
IDML_NS = "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging"


def _local_name(element) -> str:
    """返回 XML 元素的本地标签名，兼容带命名空间的 IDML。"""
    if not isinstance(getattr(element, "tag", None), str):
        return ""
    return etree.QName(element).localname


def _nearest_paragraph(element):
    """返回元素最近的 ParagraphStyleRange 祖先。"""
    return next(
        (
            ancestor
            for ancestor in element.iterancestors()
            if _local_name(ancestor) == "ParagraphStyleRange"
        ),
        None,
    )


def _paragraph_text_parts(paragraph) -> List[str]:
    """提取当前段落直接拥有的文本，避免递归吃进嵌套表格。

    IDML 会把项目符号、说明项等多个视觉段落放在同一个
    ``ParagraphStyleRange`` 中，并用 ``Br`` 标记边界。无论是否位于
    表格单元格，都必须按该边界拆分，否则整页内容会被拼成一个句段。
    """
    parts: List[str] = []
    current: List[str] = []

    def flush() -> None:
        text = "".join(current).strip()
        current.clear()
        if text:
            parts.append(text)

    for element in paragraph.iter():
        if element is paragraph or _nearest_paragraph(element) is not paragraph:
            continue
        element_name = _local_name(element)
        if element_name == "Content" and element.text:
            current.append(element.text)
        elif element_name == "Br":
            flush()

    flush()
    return parts


class IdmlAdapter(FormatAdapter):
    """Adobe InDesign IDML 文件适配器"""

    def supported_extensions(self) -> List[str]:
        return [".idml"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".idml"),
                segments=[],
                metadata={},
            )
        
        try:
            zf = zipfile.ZipFile(BytesIO(raw_bytes), 'r')
        except zipfile.BadZipFile as e:
            raise ParseError(filename="<unknown>", reason=f"无效的 IDML 文件: {str(e)}")
        
        nodes = []
        story_count = 0
        
        # 遍历 Stories 目录
        for name in sorted(zf.namelist()):
            if name.startswith('Stories/') and name.endswith('.xml'):
                try:
                    content = zf.read(name)
                    story_nodes = self._parse_story(content, name)
                    nodes.extend(story_nodes)
                    story_count += 1
                except Exception:
                    continue
        
        zf.close()
        
        ast = DocumentAST(nodes=nodes, source_format=".idml")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"story_count": story_count},
        )

    def _parse_story(self, content: bytes, story_name: str) -> List[BlockNode]:
        """解析单个 Story XML 文件"""
        nodes = []
        
        try:
            parser = etree.XMLParser(remove_blank_text=True, recover=True)
            root = etree.fromstring(content, parser=parser)
        except etree.XMLSyntaxError:
            return nodes
        
        # paragraph_index 是 Story 内稳定的结构坐标，导出时据此把句段译文
        # 重新写回原 ParagraphStyleRange，避免依赖 Content 文本完全匹配。
        paragraph_index = -1

        # 查找所有 ParagraphStyleRange 或 Content 元素
        for para in root.iter():
            if _local_name(para) == 'ParagraphStyleRange':
                paragraph_index += 1
                paragraph_parts = _paragraph_text_parts(para)
                if paragraph_parts:
                    # 获取段落样式
                    style = para.get('AppliedParagraphStyle', '')
                    
                    # 判断是否是标题
                    node_type = NodeType.PARAGRAPH
                    if 'Heading' in style or 'Title' in style:
                        node_type = NodeType.HEADING
                    
                    for part_index, text in enumerate(paragraph_parts):
                        nodes.append(BlockNode(
                            node_type=node_type,
                            text_content=text,
                            metadata={
                                "story": story_name,
                                "style": style,
                                "paragraph_index": paragraph_index,
                                "paragraph_part_index": part_index,
                            },
                        ))
        
        return nodes
