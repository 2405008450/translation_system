"""
SDLXLIFF 适配器模块 - 解析 SDL Trados XLIFF 文件

SDL Trados 的 XLIFF 变体，包含额外的命名空间和元数据。
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


# SDL XLIFF 命名空间
NAMESPACES = {
    'xliff': 'urn:oasis:names:tc:xliff:document:1.2',
    'sdl': 'http://sdl.com/FileTypes/SdlXliff/1.0',
}


class SdlxliffAdapter(FormatAdapter):
    """SDL XLIFF 文件适配器"""

    def supported_extensions(self) -> List[str]:
        return [".sdlxliff"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".sdlxliff"),
                segments=[],
                metadata={},
            )
        
        try:
            parser = etree.XMLParser(remove_blank_text=True, recover=True)
            root = etree.fromstring(raw_bytes, parser=parser)
        except etree.XMLSyntaxError as e:
            raise ParseError(filename="<unknown>", reason=f"SDLXLIFF 解析错误: {str(e)}")
        
        # 检测命名空间
        nsmap = self._detect_namespaces(root)
        nodes = self._extract_trans_units(root, nsmap)
        
        ast = DocumentAST(nodes=nodes, source_format=".sdlxliff")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"trans_unit_count": len(nodes)},
        )

    def _detect_namespaces(self, root: etree._Element) -> dict:
        """检测文档中使用的命名空间"""
        nsmap = {}
        
        # 从根元素获取命名空间
        for prefix, uri in root.nsmap.items():
            if prefix:
                nsmap[prefix] = uri
            elif 'xliff' in uri.lower():
                nsmap['xliff'] = uri
        
        # 如果没有找到 xliff 命名空间，使用默认值
        if 'xliff' not in nsmap:
            nsmap['xliff'] = NAMESPACES['xliff']
        
        return nsmap

    def _extract_trans_units(self, root: etree._Element, nsmap: dict) -> List[BlockNode]:
        """提取翻译单元"""
        nodes = []
        
        # 查找所有 trans-unit 元素
        xliff_ns = nsmap.get('xliff', NAMESPACES['xliff'])
        
        for tu in root.iter(f'{{{xliff_ns}}}trans-unit'):
            source = tu.find(f'{{{xliff_ns}}}source')
            target = tu.find(f'{{{xliff_ns}}}target')
            
            if source is not None:
                source_text = self._get_text_content(source)
                target_text = self._get_text_content(target) if target is not None else ""
                
                if source_text.strip():
                    tu_id = tu.get('id', '')
                    
                    nodes.append(BlockNode(
                        node_type=NodeType.PARAGRAPH,
                        text_content=source_text,
                        metadata={
                            "tu_id": tu_id,
                            "target": target_text,
                            "translate": tu.get('translate', 'yes'),
                            "locked": self._is_locked(tu, nsmap),
                        },
                    ))
        
        return nodes

    def _get_text_content(self, element: Optional[etree._Element]) -> str:
        """获取元素的文本内容，包括内联标签"""
        if element is None:
            return ""
        return ''.join(element.itertext())

    def _is_locked(self, tu: etree._Element, nsmap: dict) -> bool:
        """检查翻译单元是否被锁定"""
        # SDL 特定的锁定属性
        sdl_ns = nsmap.get('sdl', NAMESPACES.get('sdl', ''))
        if sdl_ns:
            locked = tu.get(f'{{{sdl_ns}}}locked')
            if locked:
                return locked.lower() == 'true'
        return False
