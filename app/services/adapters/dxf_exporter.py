"""
DXF 导出器 - 将翻译后的内容导出为 DXF 格式

保留原始文件结构，仅替换文本内容。
"""
import re
from typing import Dict


class DxfExporter:
    """DXF 导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """导出翻译后的 DXF 文件
        
        Args:
            original_bytes: 原始文件字节
            translations: source_text -> target_text
            
        Returns:
            bytes: 翻译后的文件字节
        """
        content = self._decode_content(original_bytes)
        lines = content.replace('\r\n', '\n').split('\n')
        result_lines = []
        
        i = 0
        current_entity = None
        
        while i < len(lines):
            line = lines[i]
            
            if i + 1 < len(lines):
                try:
                    group_code = int(line.strip())
                    value = lines[i + 1]
                    
                    # 实体开始
                    if group_code == 0:
                        current_entity = value.strip()
                        result_lines.append(line)
                        result_lines.append(value)
                        i += 2
                        continue
                    
                    # 文本内容 (组码 1 或 3)
                    if group_code in (1, 3) and current_entity in ('TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF'):
                        text = value.strip()
                        
                        # 对于 MTEXT，先清理格式代码再查找翻译
                        clean_text = self._clean_mtext(text) if current_entity == 'MTEXT' else text
                        
                        if clean_text in translations:
                            new_text = translations[clean_text]
                            # 保留原始的前导空格
                            leading_space = value[:len(value) - len(value.lstrip())]
                            result_lines.append(line)
                            result_lines.append(leading_space + new_text)
                            i += 2
                            continue
                    
                    result_lines.append(line)
                    result_lines.append(value)
                    i += 2
                    continue
                    
                except ValueError:
                    pass
            
            result_lines.append(line)
            i += 1
        
        return '\n'.join(result_lines).encode('utf-8')

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "cp1252", "iso-8859-1", "gb18030"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode('utf-8', errors='replace')

    def _clean_mtext(self, text: str) -> str:
        """清理 MTEXT 格式代码"""
        # 移除格式代码
        text = text.replace('\\P', '\n')
        text = re.sub(r'\\[LlOoKk]', '', text)
        text = re.sub(r'\\F[^;]*;', '', text)
        text = re.sub(r'\\H[^;]*;', '', text)
        text = re.sub(r'\\W[^;]*;', '', text)
        text = re.sub(r'\\Q[^;]*;', '', text)
        text = re.sub(r'\\T[^;]*;', '', text)
        text = re.sub(r'\\C\d+;', '', text)
        text = re.sub(r'\\S[^;]*;', '', text)
        text = re.sub(r'\\A\d;', '', text)
        text = text.replace('{', '').replace('}', '')
        text = text.replace('\\\\', '\\')
        return text.strip()
