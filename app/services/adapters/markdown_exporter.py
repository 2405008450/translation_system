"""
Markdown 导出器 - 将翻译后的内容导出为 Markdown 格式

保留原始 Markdown 结构和格式。
"""
import re
from typing import Dict


class MarkdownExporter:
    """Markdown 导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """导出翻译后的 Markdown 文件
        
        Args:
            original_bytes: 原始文件字节
            translations: 源文 -> 译文 的映射
            
        Returns:
            bytes: 翻译后的文件字节
        """
        content = self._decode_content(original_bytes)
        lines = content.replace('\r\n', '\n').split('\n')
        result_lines = []
        
        in_code_block = False
        current_para = []
        current_code_block = []
        code_fence = ""
        
        for line in lines:
            # 代码块
            if line.startswith('```'):
                if current_para:
                    result_lines.extend(self._process_paragraph(current_para, translations))
                    current_para = []
                if in_code_block:
                    result_lines.extend(self._process_code_block(code_fence, current_code_block, line, translations))
                    current_code_block = []
                    code_fence = ""
                    in_code_block = False
                else:
                    in_code_block = True
                    code_fence = line
                continue
            
            if in_code_block:
                current_code_block.append(line)
                continue
            
            # 空行
            if not line.strip():
                if current_para:
                    result_lines.extend(self._process_paragraph(current_para, translations))
                    current_para = []
                result_lines.append(line)
                continue
            
            # 标题
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if heading_match:
                if current_para:
                    result_lines.extend(self._process_paragraph(current_para, translations))
                    current_para = []
                prefix = heading_match.group(1)
                text = heading_match.group(2).strip()
                translated = translations.get(text, text)
                result_lines.append(f"{prefix} {translated}")
                continue
            
            # 列表项
            list_match = re.match(r'^(\s*)([-*+]|\d+\.)\s+(.+)$', line)
            if list_match:
                if current_para:
                    result_lines.extend(self._process_paragraph(current_para, translations))
                    current_para = []
                indent = list_match.group(1)
                marker = list_match.group(2)
                text = list_match.group(3).strip()
                translated = translations.get(text, text)
                result_lines.append(f"{indent}{marker} {translated}")
                continue
            
            # 引用
            if line.startswith('>'):
                if current_para:
                    result_lines.extend(self._process_paragraph(current_para, translations))
                    current_para = []
                text = line.lstrip('>').strip()
                if text:
                    translated = translations.get(text, text)
                    result_lines.append(f"> {translated}")
                else:
                    result_lines.append(line)
                continue
            
            # 普通段落
            current_para.append(line)
        
        if current_para:
            result_lines.extend(self._process_paragraph(current_para, translations))
        if in_code_block:
            result_lines.append(code_fence)
            result_lines.extend(current_code_block)
        
        return '\n'.join(result_lines).encode('utf-8')

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode('utf-8', errors='replace')

    def _process_paragraph(self, lines: list, translations: Dict[str, str]) -> list:
        """处理段落"""
        text = ' '.join(line.strip() for line in lines if line.strip())
        if text in translations:
            return [translations[text]]
        return lines

    def _process_code_block(
        self,
        opening_fence: str,
        lines: list,
        closing_fence: str,
        translations: Dict[str, str],
    ) -> list:
        """处理代码块。只有上传时勾选并产生译文时才会替换。"""
        text = '\n'.join(lines).strip()
        normalized_text = re.sub(r'\s+', ' ', text)
        translated = translations.get(text) or translations.get(normalized_text)
        if translated:
            return [opening_fence, translated, closing_fence]
        return [opening_fence, *lines, closing_fence]
