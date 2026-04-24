"""
PO 导出器 - 将翻译后的内容导出为 .po 格式

保留原始文件结构、注释和元数据。
"""
import re
from typing import Dict


class PoExporter:
    """PO 导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """导出翻译后的 PO 文件
        
        Args:
            original_bytes: 原始文件字节
            translations: msgid -> msgstr 的映射
            
        Returns:
            bytes: 翻译后的文件字节
        """
        content = self._decode_content(original_bytes)
        lines = content.replace('\r\n', '\n').split('\n')
        result_lines = []
        
        current_msgid = None
        in_msgstr = False
        msgstr_lines = []
        
        for line in lines:
            # 检测 msgid
            if line.startswith('msgid '):
                current_msgid = self._extract_string(line[6:])
                result_lines.append(line)
                in_msgstr = False
                continue
            
            # 检测 msgid 续行
            if line.startswith('"') and current_msgid is not None and not in_msgstr:
                current_msgid += self._extract_string(line)
                result_lines.append(line)
                continue
            
            # 检测 msgstr
            if line.startswith('msgstr '):
                in_msgstr = True
                # 查找翻译
                if current_msgid and current_msgid in translations:
                    new_msgstr = translations[current_msgid]
                    escaped = self._escape_string(new_msgstr)
                    result_lines.append(f'msgstr "{escaped}"')
                else:
                    result_lines.append(line)
                continue
            
            # msgstr 续行
            if line.startswith('"') and in_msgstr:
                # 如果已经替换了 msgstr，跳过原来的续行
                if current_msgid and current_msgid in translations:
                    continue
                result_lines.append(line)
                continue
            
            # 其他行（注释、空行等）
            if not line.strip():
                current_msgid = None
                in_msgstr = False
            
            result_lines.append(line)
        
        return '\n'.join(result_lines).encode('utf-8')

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "iso-8859-1", "gb18030"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode('utf-8', errors='replace')

    def _extract_string(self, text: str) -> str:
        """从引号中提取字符串"""
        text = text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        text = text.replace('\\n', '\n')
        text = text.replace('\\t', '\t')
        text = text.replace('\\"', '"')
        text = text.replace('\\\\', '\\')
        return text

    def _escape_string(self, text: str) -> str:
        """转义 PO 字符串"""
        text = text.replace('\\', '\\\\')
        text = text.replace('"', '\\"')
        text = text.replace('\n', '\\n')
        text = text.replace('\t', '\\t')
        return text
