"""SVG 导出器：按稳定文本槽位将译文写回 SVG。"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

from lxml import etree

from app.services.adapters.exceptions import ExportError


SVG_NS = "http://www.w3.org/2000/svg"
AI_NS = "http://ns.adobe.com/AdobeIllustrator/10.0/"
NSMAP = {"svg": SVG_NS, "i": AI_NS}


class SvgExporter:
    """将翻译结果写回 SVG，并清理会覆盖译文的 Illustrator 私有数据。"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        *,
        strip_illustrator_private_data: bool = True,
    ) -> Tuple[bytes, List[dict]]:
        """导出译后 SVG。

        ``translations`` 同时兼容三种键：稳定句段 ID（如 ``seg-000001``）、
        旧接口顺序 ID（如 ``seg_0``）以及原文。稳定句段 ID 优先。
        """
        return self._export(
            original_bytes,
            translations,
            bilingual=False,
            separator=" / ",
            strip_illustrator_private_data=strip_illustrator_private_data,
        )

    def export_bilingual(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        separator: str = " / ",
        *,
        strip_illustrator_private_data: bool = True,
    ) -> Tuple[bytes, List[dict]]:
        """导出原文和译文并列的 SVG。"""
        return self._export(
            original_bytes,
            translations,
            bilingual=True,
            separator=separator,
            strip_illustrator_private_data=strip_illustrator_private_data,
        )

    def _export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        *,
        bilingual: bool,
        separator: str,
        strip_illustrator_private_data: bool,
    ) -> Tuple[bytes, List[dict]]:
        root = self._parse(original_bytes)
        warnings: List[dict] = []

        for unit_index, (owner, slot_kind, original_text) in enumerate(
            self._iter_text_slots(root)
        ):
            match = self._resolve_translation(translations, unit_index, original_text)
            if match is None:
                continue

            segment_id, translated_text = match
            replacement = (
                f"{original_text.strip()}{separator}{translated_text}"
                if bilingual
                else translated_text
            )
            replacement = self._preserve_edge_whitespace(original_text, replacement)

            if slot_kind == "text":
                owner.text = replacement
            else:
                owner.tail = replacement

            warning = self._check_length_change(
                segment_id,
                original_text,
                replacement,
            )
            if warning:
                warnings.append(warning)

        if strip_illustrator_private_data:
            removed = self._strip_illustrator_private_data(root)
            if removed:
                warnings.append(
                    {
                        "code": "illustrator_private_data_removed",
                        "message": (
                            "已从译后副本中移除 Illustrator 私有 PGF 数据，"
                            "避免重新打开时旧文字覆盖译文。"
                        ),
                        "removed_elements": removed,
                    }
                )

        exported_bytes = etree.tostring(
            root,
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=False,
        )
        return exported_bytes, warnings

    def _parse(self, original_bytes: bytes) -> etree._Element:
        try:
            parser = etree.XMLParser(
                remove_blank_text=False,
                recover=False,
                resolve_entities="internal",
                no_network=True,
            )
            root = etree.fromstring(original_bytes, parser=parser)
        except (etree.XMLSyntaxError, ValueError) as exc:
            raise ExportError(
                filename="<unknown>",
                reason=f"无法解析 SVG 文件: {exc}",
            ) from exc

        if etree.QName(root).localname.lower() != "svg":
            raise ExportError(filename="<unknown>", reason="根元素不是 SVG")
        return root

    def _iter_text_slots(
        self,
        root: etree._Element,
    ) -> Iterable[tuple[etree._Element, str, str]]:
        text_elements = root.xpath("//svg:text | //text", namespaces=NSMAP)
        for text_element in text_elements:
            yield from self._iter_element_text_slots(text_element)

    def _iter_element_text_slots(
        self,
        element: etree._Element,
    ) -> Iterable[tuple[etree._Element, str, str]]:
        if element.text and element.text.strip():
            yield element, "text", element.text
        for child in element:
            yield from self._iter_element_text_slots(child)
            if child.tail and child.tail.strip():
                yield child, "tail", child.tail

    def _resolve_translation(
        self,
        translations: Dict[str, str],
        unit_index: int,
        original_text: str,
    ) -> Optional[tuple[str, str]]:
        stable_id = f"seg-{unit_index + 1:06d}"
        legacy_id = f"seg_{unit_index}"
        source_text = original_text.strip()
        for key in (stable_id, legacy_id, source_text):
            if key not in translations:
                continue
            value = translations[key]
            if value is None:
                continue
            translated_text = str(value)
            if not translated_text:
                continue
            return key, translated_text
        return None

    def _strip_illustrator_private_data(self, root: etree._Element) -> int:
        """移除 Illustrator 的旧 PGF 和指向它的占位节点。"""
        candidates = list(root.xpath("//i:pgf", namespaces=NSMAP))
        candidates.extend(
            root.xpath(
                "//*[local-name()='foreignObject' and "
                "contains(@requiredExtensions, $ai_ns)]",
                ai_ns=AI_NS,
            )
        )

        removed = 0
        seen: set[int] = set()
        for element in candidates:
            marker = id(element)
            if marker in seen:
                continue
            seen.add(marker)
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
                removed += 1
        return removed

    @staticmethod
    def _preserve_edge_whitespace(original: str, replacement: str) -> str:
        leading = re.match(r"^\s*", original).group(0)
        trailing = re.search(r"\s*$", original).group(0)
        return f"{leading}{replacement}{trailing}"

    def _check_length_change(
        self,
        segment_id: str,
        original: str,
        translated: str,
        threshold: float = 0.3,
    ) -> Optional[dict]:
        """译文长度变化较大时返回版式风险提示。"""
        original_len = len(original.strip())
        translated_len = len(translated.strip())
        if original_len == 0:
            return None

        change_ratio = abs(translated_len - original_len) / original_len
        if change_ratio <= threshold:
            return None

        return {
            "segment_id": segment_id,
            "original_length": original_len,
            "translated_length": translated_len,
            "change_ratio": round(change_ratio * 100, 1),
            "message": f"文本长度变化 {round(change_ratio * 100, 1)}%，可能影响布局",
        }
