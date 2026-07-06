"""
SDLXLIFF 导出器 - 将翻译后的内容导出为 SDLXLIFF 格式

保留原始文件结构，仅更新 target 元素。
"""
from typing import Any, Dict

from lxml import etree


# SDL XLIFF 命名空间
NAMESPACES = {
    'xliff': 'urn:oasis:names:tc:xliff:document:1.2',
}


class SdlxliffExporter:
    """SDLXLIFF 导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """导出翻译后的 SDLXLIFF 文件
        
        Args:
            original_bytes: 原始文件字节
            translations: source_text -> target_text 或 tu_id -> target_text
            
        Returns:
            bytes: 翻译后的文件字节
        """
        parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
        root = etree.fromstring(original_bytes, parser=parser)
        
        # 检测命名空间
        nsmap = self._detect_namespaces(root)
        xliff_ns = nsmap.get('xliff', NAMESPACES['xliff'])
        
        # 更新翻译
        for tu in root.iter(f'{{{xliff_ns}}}trans-unit'):
            source = tu.find(f'{{{xliff_ns}}}source')
            target = tu.find(f'{{{xliff_ns}}}target')
            
            if source is None:
                continue
            
            source_text = ''.join(source.itertext())
            tu_id = tu.get('id', '')
            
            # 查找翻译
            translation = None
            if source_text in translations:
                translation = translations[source_text]
            elif tu_id in translations:
                translation = translations[tu_id]
            
            if translation:
                if target is None:
                    # 创建 target 元素
                    target = etree.SubElement(tu, f'{{{xliff_ns}}}target')
                    # 插入到 source 后面
                    source_idx = list(tu).index(source)
                    tu.remove(target)
                    tu.insert(source_idx + 1, target)
                
                # 清空并设置新文本
                target.text = translation
                for child in list(target):
                    target.remove(child)
        
        return etree.tostring(root, encoding='utf-8', xml_declaration=True)

    def export_by_segments(
        self,
        original_bytes: bytes,
        segments: list[Any],
    ) -> bytes:
        """按解析句段顺序/ID 写回 SDLXLIFF target。

        SDLXLIFF 中重复源文很常见，单纯 source_text -> target_text 会把不同位置的译文混在一起。
        这里复用 SdlxliffAdapter 的 sdl-000001 顺序 ID，保证导出与工作台句段一一对应。
        """
        parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
        root = etree.fromstring(original_bytes, parser=parser)
        nsmap = self._detect_namespaces(root)
        xliff_ns = nsmap.get('xliff', NAMESPACES['xliff'])

        ordered_segments = [self._normalize_segment(segment) for segment in segments]
        by_id = {
            str(segment.get("segment_id") or segment.get("sentence_id") or ""): segment
            for segment in ordered_segments
            if str(segment.get("segment_id") or segment.get("sentence_id") or "")
        }

        position = 0
        for tu in root.iter(f'{{{xliff_ns}}}trans-unit'):
            source = tu.find(f'{{{xliff_ns}}}source')
            seg_source = tu.find(f'{{{xliff_ns}}}seg-source')
            target = tu.find(f'{{{xliff_ns}}}target')
            source_marks = self._find_segment_marks(seg_source, xliff_ns)

            if source_marks:
                if target is None:
                    target = self._create_target_after_source(tu, source, seg_source, xliff_ns)
                target_marks = self._target_marks_by_mid(target, xliff_ns)
                for source_mark in source_marks:
                    segment = self._lookup_segment(position, by_id, ordered_segments)
                    position += 1
                    target_text = str(segment.get("target_text") or "") if segment else ""
                    if not target_text.strip():
                        continue

                    mid = source_mark.get("mid", "")
                    target_mark = target_marks.get(mid)
                    if target_mark is None:
                        target_mark = etree.SubElement(target, f'{{{xliff_ns}}}mrk')
                        target_mark.set("mtype", "seg")
                        if mid:
                            target_mark.set("mid", mid)
                        target_marks[mid] = target_mark
                    self._set_element_text(target_mark, target_text)
                continue

            if source is None:
                continue

            segment = self._lookup_segment(position, by_id, ordered_segments)
            position += 1
            target_text = str(segment.get("target_text") or "") if segment else ""
            if not target_text.strip():
                continue

            if target is None:
                target = self._create_target_after_source(tu, source, seg_source, xliff_ns)
            self._set_element_text(target, target_text)

        return etree.tostring(root, encoding='utf-8', xml_declaration=True)

    def _detect_namespaces(self, root: etree._Element) -> dict:
        """检测命名空间"""
        nsmap = {}
        for prefix, uri in root.nsmap.items():
            if prefix:
                nsmap[prefix] = uri
            elif 'xliff' in uri.lower():
                nsmap['xliff'] = uri
        if 'xliff' not in nsmap:
            nsmap['xliff'] = NAMESPACES['xliff']
        return nsmap

    def _normalize_segment(self, segment: Any) -> dict[str, Any]:
        if isinstance(segment, dict):
            item = dict(segment)
        else:
            item = {
                "segment_id": getattr(segment, "segment_id", None),
                "sentence_id": getattr(segment, "sentence_id", None),
                "target_text": getattr(segment, "target_text", ""),
            }
        segment_id = item.get("segment_id") or item.get("sentence_id") or ""
        item["segment_id"] = str(segment_id)
        item.setdefault("sentence_id", str(segment_id))
        item.setdefault("target_text", "")
        return item

    def _lookup_segment(
        self,
        position: int,
        by_id: dict[str, dict[str, Any]],
        ordered_segments: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        segment_id = f"sdl-{position + 1:06d}"
        if segment_id in by_id:
            return by_id[segment_id]
        if position < len(ordered_segments):
            return ordered_segments[position]
        return None

    def _find_segment_marks(
        self,
        element: etree._Element | None,
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
        target: etree._Element | None,
        xliff_ns: str,
    ) -> dict[str, etree._Element]:
        result: dict[str, etree._Element] = {}
        for mark in self._find_segment_marks(target, xliff_ns):
            mid = mark.get("mid")
            if mid:
                result[mid] = mark
        return result

    def _create_target_after_source(
        self,
        tu: etree._Element,
        source: etree._Element | None,
        seg_source: etree._Element | None,
        xliff_ns: str,
    ) -> etree._Element:
        target = etree.Element(f'{{{xliff_ns}}}target')
        anchor = seg_source if seg_source is not None else source
        if anchor is None:
            tu.append(target)
            return target
        anchor_index = list(tu).index(anchor)
        tu.insert(anchor_index + 1, target)
        return target

    def _set_element_text(self, element: etree._Element, text: str) -> None:
        element.text = text
        for child in list(element):
            element.remove(child)
