"""
Properties 导出器 - 将翻译后的内容导出为 .properties 格式

保留原始文件结构、注释和格式。
"""
import re
from typing import Dict


class PropertiesExporter:
    """Properties 导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """导出翻译后的 properties 文件
        
        Args:
            original_bytes: 原始文件字节
            translations: key -> 译文 的映射（key 是属性键名）
            
        Returns:
            bytes: 翻译后的文件字节
        """
        content = self._decode_content(original_bytes)
        lines = content.replace('\r\n', '\n').split('\n')
        result_lines = []
        
        i = 0
        while i < len(lines):
            line = lines[i]
            
            # 空行或注释行，保持原样
            if not line.strip() or line.lstrip().startswith(('#', '!')):
                result_lines.append(line)
                i += 1
                continue
            
            # 处理续行
            full_line = line
            continuation_count = 0
            while full_line.rstrip().endswith('\\') and i + 1 < len(lines):
                i += 1
                full_line = full_line.rstrip()[:-1] + lines[i].lstrip()
                continuation_count += 1
            
            # 解析键值对
            key, value, separator = self._parse_line(full_line)
            
            if key:
                # 查找翻译
                clean_value = value.strip()
                new_value = translations.get(key)
                if new_value is None and clean_value:
                    new_value = translations.get(clean_value)
                if new_value is None:
                    new_value = value
                # 转义特殊字符
                new_value = self._escape_value(new_value)
                result_lines.append(f"{key}{separator}{new_value}")
            else:
                result_lines.append(line)
            
            i += 1
        
        return '\n'.join(result_lines).encode('utf-8')

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "iso-8859-1", "gb18030"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode('utf-8', errors='replace')

    def _parse_line(self, line: str) -> tuple:
        """解析键值对，返回 (key, value, separator)"""
        line = line.lstrip()
        key = ""
        escape = False
        
        for i, char in enumerate(line):
            if escape:
                key += char
                escape = False
                continue
            if char == '\\':
                escape = True
                continue
            if char in ('=', ':'):
                return key.strip(), line[i + 1:].lstrip(), char
            if char in (' ', '\t'):
                rest = line[i:].lstrip()
                if rest and rest[0] in ('=', ':'):
                    return key.strip(), rest[1:].lstrip(), rest[0]
                return key.strip(), rest, ' '
            key += char
        
        return key.strip(), "", "="

    def _escape_value(self, value: str) -> str:
        """转义属性值中的特殊字符"""
        # 转义反斜杠
        value = value.replace('\\', '\\\\')
        # 转义换行
        value = value.replace('\n', '\\n')
        value = value.replace('\r', '\\r')
        value = value.replace('\t', '\\t')
        return value
