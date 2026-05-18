"""
TXML 适配器模块 - 解析 Wordfast TXML 文件

Wordfast Pro 的翻译交换格式。
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


class TxmlAdapter(FormatAdapter):
    """Wordfast TXML 文件适配器"""

    def supported_extensions(self) -> List[str]:
        return [".txml"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".txml"),
                segments=[],
                metadata={},
            )
        
        try:
            parser = etree.XMLParser(remove_blank_text=True, recover=True)
            root = etree.fromstring(raw_bytes, parser=parser)
        except etree.XMLSyntaxError as e:
            raise ParseError(filename="<unknown>", reason=f"TXML 解析错误: {str(e)}")
        
        nodes = self._extract_segments(root)
        
        ast = DocumentAST(nodes=nodes, source_format=".txml")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"segment_count": len(nodes)},
        )

    def _extract_segments(self, root: etree._Element) -> List[BlockNode]:
        """提取翻译段落"""
        nodes = []
        
        # TXML 格式: <segment><source>...</source><target>...</target></segment>
        for segment in root.iter('segment'):
            source_elem = segment.find('source')
            target_elem = segment.find('target')
            
            if source_elem is not None:
                source_text = self._get_text(source_elem)
                target_text = self._get_text(target_elem) if target_elem is not None else ""
                
                if source_text.strip():
                    # 获取段落属性
                    seg_id = segment.get('segmentId', segment.get('id', ''))
                    
                    nodes.append(BlockNode(
                        node_type=NodeType.PARAGRAPH,
                        text_content=source_text,
                        metadata={
                            "segment_id": seg_id,
                            "target": target_text,
                            "locked": segment.get('locked', 'false') == 'true',
                        },
                    ))
        
        # 也尝试其他可能的结构
        if not nodes:
            nodes = self._extract_tu_segments(root)
        
        return nodes

    def _extract_tu_segments(self, root: etree._Element) -> List[BlockNode]:
        """尝试提取 tu (translation unit) 格式的段落"""
        nodes = []
        
        for tu in root.iter('tu'):
            tuv_list = list(tu.iter('tuv'))
            if tuv_list:
                source_tuv = tuv_list[0]
                target_tuv = tuv_list[1] if len(tuv_list) > 1 else None
                
                source_seg = source_tuv.find('seg')
                if source_seg is not None:
                    source_text = self._get_text(source_seg)
                    target_text = ""
                    
                    if target_tuv is not None:
                        target_seg = target_tuv.find('seg')
                        if target_seg is not None:
                            target_text = self._get_text(target_seg)
                    
                    if source_text.strip():
                        nodes.append(BlockNode(
                            node_type=NodeType.PARAGRAPH,
                            text_content=source_text,
                            metadata={
                                "tu_id": tu.get('tuid', ''),
                                "target": target_text,
                            },
                        ))
        
        return nodes

    def _get_text(self, element) -> str:
        """获取元素的文本内容"""
        if element is None:
            return ""
        return ''.join(element.itertext())
