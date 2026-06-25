"""
SDLXLIFF 适配器模块 - 解析 SDL Trados XLIFF 文件

SDL Trados 的 XLIFF 变体，包含额外的命名空间和元数据。
"""
import re
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
        nodes, segments = self._extract_sdl_segments(root, nsmap)
        source_language, target_language = self._extract_language_pair(root, nsmap)
        
        ast = DocumentAST(
            nodes=nodes,
            source_format=".sdlxliff",
            metadata={
                "source_language": source_language,
                "target_language": target_language,
            },
        )
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={
                "segment_count": len(segments),
                "source_language": source_language,
                "target_language": target_language,
            },
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

    def _extract_sdl_segments(
        self,
        root: etree._Element,
        nsmap: dict,
    ) -> tuple[List[BlockNode], List[Segment]]:
        """按 SDL 的真实 mrk 句段顺序提取可翻译内容。"""
        nodes: list[BlockNode] = []
        segments: list[Segment] = []
        xliff_ns = nsmap.get('xliff', NAMESPACES['xliff'])
        
        for unit_index, tu in enumerate(root.iter(f'{{{xliff_ns}}}trans-unit')):
            source = tu.find(f'{{{xliff_ns}}}source')
            seg_source = tu.find(f'{{{xliff_ns}}}seg-source')
            target = tu.find(f'{{{xliff_ns}}}target')

            source_marks = self._find_segment_marks(seg_source, xliff_ns)
            target_marks = self._target_marks_by_mid(target, xliff_ns)
            tu_id = tu.get('id', '')

            if source_marks:
                for mark in source_marks:
                    mid = mark.get("mid", "")
                    source_text = self._get_text_content(mark)
                    target_text = self._get_text_content(target_marks.get(mid)) if mid in target_marks else ""
                    self._append_segment(
                        nodes=nodes,
                        segments=segments,
                        source_text=source_text,
                        target_text=target_text,
                        metadata={
                            "tu_id": tu_id,
                            "mid": mid,
                            "unit_index": unit_index,
                            "translate": tu.get('translate', 'yes'),
                            "locked": self._is_locked(tu, nsmap),
                        },
                    )
                continue

            if source is None:
                continue

            source_text = self._get_text_content(source)
            target_text = self._get_text_content(target) if target is not None else ""
            self._append_segment(
                nodes=nodes,
                segments=segments,
                source_text=source_text,
                target_text=target_text,
                metadata={
                    "tu_id": tu_id,
                    "mid": "",
                    "unit_index": unit_index,
                    "translate": tu.get('translate', 'yes'),
                    "locked": self._is_locked(tu, nsmap),
                },
            )
        
        return nodes, segments

    def _append_segment(
        self,
        *,
        nodes: list[BlockNode],
        segments: list[Segment],
        source_text: str,
        target_text: str,
        metadata: dict,
    ) -> None:
        display_text = (source_text or "").strip()
        normalized_source = self._normalize_text(display_text)
        if not normalized_source:
            return

        index = len(segments)
        segment_id = f"sdl-{index + 1:06d}"
        node_metadata = {
            "id": segment_id,
            "target": (target_text or "").strip(),
            **metadata,
        }
        nodes.append(
            BlockNode(
                node_type=NodeType.PARAGRAPH,
                text_content=display_text,
                metadata=node_metadata,
            )
        )
        segments.append(
            Segment(
                segment_id=segment_id,
                source_text=normalized_source,
                display_text=display_text,
                block_path=str(index),
                position=index,
            )
        )

    def _find_segment_marks(
        self,
        element: Optional[etree._Element],
        xliff_ns: str,
    ) -> list[etree._Element]:
        if element is None:
            return []
        return [
            mark
            for mark in element.iter(f'{{{xliff_ns}}}mrk')
            if (mark.get("mtype") or "").lower() == "seg"
        ]

    def _target_marks_by_mid(
        self,
        target: Optional[etree._Element],
        xliff_ns: str,
    ) -> dict[str, etree._Element]:
        result: dict[str, etree._Element] = {}
        for mark in self._find_segment_marks(target, xliff_ns):
            mid = mark.get("mid")
            if mid:
                result[mid] = mark
        return result

    def _get_text_content(self, element: Optional[etree._Element]) -> str:
        """获取元素的文本内容，包括内联标签"""
        if element is None:
            return ""
        return ''.join(element.itertext())

    def _normalize_text(self, text: str) -> str:
        """与通用抽取器保持一致，合并连续空白。"""
        return re.sub(r'\s+', ' ', (text or "").strip())

    def _is_locked(self, tu: etree._Element, nsmap: dict) -> bool:
        """检查翻译单元是否被锁定"""
        # SDL 特定的锁定属性
        sdl_ns = nsmap.get('sdl', NAMESPACES.get('sdl', ''))
        if sdl_ns:
            locked = tu.get(f'{{{sdl_ns}}}locked')
            if locked:
                return locked.lower() == 'true'
        return False

    def _extract_language_pair(
        self,
        root: etree._Element,
        nsmap: dict,
    ) -> tuple[str | None, str | None]:
        """从 file 节点读取 SDLXLIFF 声明的语言对。"""
        xliff_ns = nsmap.get('xliff', NAMESPACES['xliff'])
        for file_node in root.iter(f'{{{xliff_ns}}}file'):
            source_language = (file_node.get("source-language") or "").strip()
            target_language = (file_node.get("target-language") or "").strip()
            if source_language or target_language:
                return source_language or None, target_language or None
        return None, None
