"""
SRT 导出器 - 将翻译后的内容导出为 SRT 字幕格式

保留原始时间轴和序号。
"""
import re
from typing import Dict


# 时间轴正则
TIMECODE_PATTERN = re.compile(
    r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})'
)


class SrtExporter:
    """SRT 字幕导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """导出翻译后的 SRT 文件
        
        Args:
            original_bytes: 原始文件字节
            translations: 源文 -> 译文 的映射（或 index -> 译文）
            
        Returns:
            bytes: 翻译后的文件字节
        """
        content = self._decode_content(original_bytes)
        content = content.replace('\r\n', '\n').replace('\r', '\n')
        
        # 按空行分割字幕块
        blocks = re.split(r'\n\n+', content.strip())
        result_blocks = []
        
        for block in blocks:
            if not block.strip():
                continue
            
            lines = block.strip().split('\n')
            if len(lines) < 2:
                result_blocks.append(block)
                continue
            
            # 解析序号
            try:
                index = int(lines[0].strip())
            except ValueError:
                result_blocks.append(block)
                continue
            
            # 检查时间轴
            if not TIMECODE_PATTERN.match(lines[1]):
                result_blocks.append(block)
                continue
            
            # 提取原始字幕文本
            original_text = '\n'.join(lines[2:])
            clean_text = re.sub(r'<[^>]+>', '', original_text).strip()
            
            # 查找翻译
            translated = None
            if clean_text in translations:
                translated = translations[clean_text]
            elif str(index) in translations:
                translated = translations[str(index)]
            
            if translated:
                result_blocks.append(f"{lines[0]}\n{lines[1]}\n{translated}")
            else:
                result_blocks.append(block)
        
        return '\n\n'.join(result_blocks).encode('utf-8')

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "gb18030", "iso-8859-1", "cp1252"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode('utf-8', errors='replace')
