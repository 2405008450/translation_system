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
            if para.tag == 'ParagraphStyleRange':
                paragraph_index += 1
                text_parts = []
                
                # 提取段落中的所有文本
                for content_elem in para.iter('Content'):
                    if content_elem.text:
                        text_parts.append(content_elem.text)
                
                text = ''.join(text_parts).strip()
                if text:
                    # 获取段落样式
                    style = para.get('AppliedParagraphStyle', '')
                    
                    # 判断是否是标题
                    node_type = NodeType.PARAGRAPH
                    if 'Heading' in style or 'Title' in style:
                        node_type = NodeType.HEADING
                    
                    nodes.append(BlockNode(
                        node_type=node_type,
                        text_content=text,
                        metadata={
                            "story": story_name,
                            "style": style,
                            "paragraph_index": paragraph_index,
                        },
                    ))
        
        return nodes
