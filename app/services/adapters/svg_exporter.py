"""
SVG 导出器模块 - 将翻译结果导出为 SVG

Requirements: 14.1, 14.2, 14.3, 14.4
"""
from typing import Dict, List, Optional, Tuple

from lxml import etree

from app.services.adapters.exceptions import ExportError


# SVG 命名空间
SVG_NS = "http://www.w3.org/2000/svg"
NSMAP = {"svg": SVG_NS}


class SvgExporter:
    """SVG 导出器
    
    将翻译结果替换到 SVG 文件中的文本元素。
    """

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> Tuple[bytes, List[dict]]:
        """导出 SVG 文件
        
        Args:
            original_bytes: 原始 SVG 文件字节
            translations: 翻译映射 {segment_id: translated_text}
            
        Returns:
            Tuple[bytes, List[dict]]: (导出的 SVG 字节, 警告列表)
            
        Raises:
            ExportError: 当导出失败时
        """
        warnings = []
        
        try:
            parser = etree.XMLParser(remove_blank_text=False, recover=True)
            root = etree.fromstring(original_bytes, parser=parser)
        except etree.XMLSyntaxError as e:
            raise ExportError(
                filename="<unknown>",
                reason=f"无法解析 SVG 文件: {str(e)}"
            )
        
        # 查找所有 text 元素
        text_elements = root.xpath(
            "//svg:text | //text",
            namespaces=NSMAP
        )
        
        segment_index = 0
        
        for text_elem in text_elements:
            # 检查是否有 tspan 子元素
            tspans = text_elem.xpath("svg:tspan | tspan", namespaces=NSMAP)
            
            if tspans:
                # 处理 tspan 子元素
                for tspan in tspans:
                    if tspan.text and tspan.text.strip():
                        segment_id = f"seg_{segment_index}"
                        if segment_id in translations:
                            original_text = tspan.text
                            translated_text = translations[segment_id]
                            
                            # 检查文本长度变化
                            warning = self._check_length_change(
                                segment_id, original_text, translated_text
                            )
                            if warning:
                                warnings.append(warning)
                            
                            tspan.text = translated_text
                        segment_index += 1
            else:
                # 直接处理 text 元素
                if text_elem.text and text_elem.text.strip():
                    segment_id = f"seg_{segment_index}"
                    if segment_id in translations:
                        original_text = text_elem.text
                        translated_text = translations[segment_id]
                        
                        # 检查文本长度变化
                        warning = self._check_length_change(
                            segment_id, original_text, translated_text
                        )
                        if warning:
                            warnings.append(warning)
                        
                        text_elem.text = translated_text
                    segment_index += 1
        
        # 序列化为 XML
        exported_bytes = etree.tostring(
            root,
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=True,
        )
        
        return exported_bytes, warnings

    def _check_length_change(
        self,
        segment_id: str,
        original: str,
        translated: str,
        threshold: float = 0.3,
    ) -> Optional[dict]:
        """检查文本长度变化
        
        Args:
            segment_id: 段落 ID
            original: 原始文本
            translated: 翻译文本
            threshold: 长度变化阈值（默认 30%）
            
        Returns:
            Optional[dict]: 警告信息，如果长度变化超过阈值
        """
        original_len = len(original.strip())
        translated_len = len(translated.strip())
        
        if original_len == 0:
            return None
        
        change_ratio = abs(translated_len - original_len) / original_len
        
        if change_ratio > threshold:
            return {
                "segment_id": segment_id,
                "original_length": original_len,
                "translated_length": translated_len,
                "change_ratio": round(change_ratio * 100, 1),
                "message": f"文本长度变化 {round(change_ratio * 100, 1)}%，可能影响布局",
            }
        
        return None

    def export_bilingual(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        separator: str = " / ",
    ) -> Tuple[bytes, List[dict]]:
        """导出双语 SVG 文件
        
        Args:
            original_bytes: 原始 SVG 文件字节
            translations: 翻译映射 {segment_id: translated_text}
            separator: 原文和译文之间的分隔符
            
        Returns:
            Tuple[bytes, List[dict]]: (导出的 SVG 字节, 警告列表)
        """
        warnings = []
        
        try:
            parser = etree.XMLParser(remove_blank_text=False, recover=True)
            root = etree.fromstring(original_bytes, parser=parser)
        except etree.XMLSyntaxError as e:
            raise ExportError(
                filename="<unknown>",
                reason=f"无法解析 SVG 文件: {str(e)}"
            )
        
        # 查找所有 text 元素
        text_elements = root.xpath(
            "//svg:text | //text",
            namespaces=NSMAP
        )
        
        segment_index = 0
        
        for text_elem in text_elements:
            # 检查是否有 tspan 子元素
            tspans = text_elem.xpath("svg:tspan | tspan", namespaces=NSMAP)
            
            if tspans:
                for tspan in tspans:
                    if tspan.text and tspan.text.strip():
                        segment_id = f"seg_{segment_index}"
                        if segment_id in translations:
                            original_text = tspan.text.strip()
                            translated_text = translations[segment_id]
                            tspan.text = f"{original_text}{separator}{translated_text}"
                            
                            warnings.append({
                                "segment_id": segment_id,
                                "message": "双语文本可能超出原有空间",
                            })
                        segment_index += 1
            else:
                if text_elem.text and text_elem.text.strip():
                    segment_id = f"seg_{segment_index}"
                    if segment_id in translations:
                        original_text = text_elem.text.strip()
                        translated_text = translations[segment_id]
                        text_elem.text = f"{original_text}{separator}{translated_text}"
                        
                        warnings.append({
                            "segment_id": segment_id,
                            "message": "双语文本可能超出原有空间",
                        })
                    segment_index += 1
        
        exported_bytes = etree.tostring(
            root,
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=True,
        )
        
        return exported_bytes, warnings
