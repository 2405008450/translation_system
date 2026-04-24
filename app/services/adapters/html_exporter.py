"""
HTML 导出器 - 将翻译后的内容导出为 HTML 格式

保留原始 HTML 结构，仅替换文本内容。
"""
import re
from typing import Dict
from html.parser import HTMLParser


class HtmlExporter:
    """HTML 导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """导出翻译后的 HTML
        
        Args:
            original_bytes: 原始 HTML 文件字节
            translations: segment_id -> 译文 的映射
            
        Returns:
            bytes: 翻译后的 HTML 文件字节
        """
        content = self._decode_content(original_bytes)
        
        # 构建源文到译文的映射
        source_to_target = {}
        for seg_id, target in translations.items():
            # segment_id 格式通常包含源文信息
            source_to_target[target] = target  # 直接映射
        
        # 也尝试从 translations 的值中提取
        for source, target in translations.items():
            if source and target:
                source_to_target[source] = target
        
        # 替换文本内容
        result = self._replace_text(content, source_to_target)
        
        return result.encode('utf-8')

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "iso-8859-1"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode('utf-8', errors='replace')

    def _replace_text(self, content: str, translations: Dict[str, str]) -> str:
        """替换 HTML 中的文本内容"""
        # 使用正则替换标签之间的文本
        def replace_text_node(match):
            text = match.group(1)
            stripped = text.strip()
            if stripped in translations:
                # 保留原始空白
                leading = text[:len(text) - len(text.lstrip())]
                trailing = text[len(text.rstrip()):]
                return f">{leading}{translations[stripped]}{trailing}<"
            return match.group(0)
        
        # 匹配标签之间的文本
        result = re.sub(r'>([^<]+)<', replace_text_node, content)
        return result
