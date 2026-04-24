"""
MIF 导出器 - 将翻译后的内容导出为 MIF 格式

保留原始文件结构，仅替换 String 内容。
"""
import re
from typing import Dict


class MifExporter:
    """MIF 导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """导出翻译后的 MIF 文件
        
        Args:
            original_bytes: 原始文件字节
            translations: source_text -> target_text
            
        Returns:
            bytes: 翻译后的文件字节
        """
        content = self._decode_content(original_bytes)
        
        # 替换 String 内容
        def replace_string(match):
            original = match.group(1)
            # 反转义以获取原始文本
            text = self._unescape_mif(original)
            
            if text in translations:
                # 转义新文本
                new_text = self._escape_mif(translations[text])
                return f"<String `{new_text}'>"
            return match.group(0)
        
        result = re.sub(r"<String\s+`([^']*)'?>", replace_string, content)
        
        return result.encode('utf-8')

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "iso-8859-1", "cp1252"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode('utf-8', errors='replace')

    def _unescape_mif(self, text: str) -> str:
        """处理 MIF 转义字符"""
        replacements = [
            ('\\q', "'"),
            ('\\Q', '"'),
            ('\\>', '>'),
            ('\\t', '\t'),
            ('\\\\', '\\'),
        ]
        for old, new in replacements:
            text = text.replace(old, new)
        
        def hex_replace(match):
            try:
                return chr(int(match.group(1), 16))
            except ValueError:
                return match.group(0)
        
        text = re.sub(r'\\x([0-9a-fA-F]{2})', hex_replace, text)
        return text

    def _escape_mif(self, text: str) -> str:
        """转义 MIF 特殊字符"""
        text = text.replace('\\', '\\\\')
        text = text.replace("'", '\\q')
        text = text.replace('"', '\\Q')
        text = text.replace('>', '\\>')
        text = text.replace('\t', '\\t')
        return text
