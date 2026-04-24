"""
Strings 导出器 - 将翻译后的内容导出为 iOS .strings 格式

保留原始文件结构和注释。
"""
import re
from typing import Dict


class StringsExporter:
    """iOS Strings 导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """导出翻译后的 strings 文件
        
        Args:
            original_bytes: 原始文件字节
            translations: key -> 译文 的映射
            
        Returns:
            bytes: 翻译后的文件字节
        """
        content = self._decode_content(original_bytes)
        
        # 替换每个键值对的值
        def replace_value(match):
            key = self._unescape(match.group(1))
            original_value = self._unescape(match.group(2))

            translated = translations.get(key)
            if translated is None and original_value:
                translated = translations.get(original_value)

            if translated is not None:
                new_value = self._escape(translated)
                return f'"{match.group(1)}" = "{new_value}";'
            return match.group(0)
        
        # 匹配 "key" = "value"; 模式
        pattern = r'"([^"\\]*(?:\\.[^"\\]*)*)"\s*=\s*"([^"\\]*(?:\\.[^"\\]*)*)"\s*;'
        result = re.sub(pattern, replace_value, content)
        
        return result.encode('utf-8')

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-16", "utf-8", "utf-8-sig", "utf-16-le", "utf-16-be"):
            try:
                return raw_bytes.decode(encoding)
            except (UnicodeDecodeError, UnicodeError):
                continue
        return raw_bytes.decode('utf-8', errors='replace')

    def _unescape(self, text: str) -> str:
        """处理转义字符"""
        text = text.replace(r'\"', '"')
        text = text.replace(r"\'", "'")
        text = text.replace(r'\\n', '\n')
        text = text.replace(r'\\t', '\t')
        text = text.replace(r'\\\\', '\\')
        return text

    def _escape(self, text: str) -> str:
        """转义特殊字符"""
        text = text.replace('\\', '\\\\')
        text = text.replace('"', '\\"')
        text = text.replace('\n', '\\n')
        text = text.replace('\t', '\\t')
        return text
