"""
CSV 导出器 - 将翻译后的内容导出为 CSV 格式

保留原始 CSV 结构和格式。
"""
import csv
import io
from typing import Dict


class CsvExporter:
    """CSV 导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """导出翻译后的 CSV 文件
        
        Args:
            original_bytes: 原始文件字节
            translations: 源文 -> 译文 的映射（或 "row,col" -> 译文）
            
        Returns:
            bytes: 翻译后的文件字节
        """
        content = self._decode_content(original_bytes)
        
        # 检测分隔符
        try:
            dialect = csv.Sniffer().sniff(content[:4096], delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel
        
        # 读取原始数据
        reader = csv.reader(io.StringIO(content), dialect)
        rows = list(reader)
        
        # 替换翻译
        for row_idx, row in enumerate(rows):
            for col_idx, cell in enumerate(row):
                text = cell.strip()
                if not text:
                    continue
                
                # 尝试多种键格式
                translated = None
                if text in translations:
                    translated = translations[text]
                elif f"{row_idx},{col_idx}" in translations:
                    translated = translations[f"{row_idx},{col_idx}"]
                
                if translated:
                    rows[row_idx][col_idx] = translated
        
        # 写入结果
        output = io.StringIO()
        writer = csv.writer(output, dialect)
        writer.writerows(rows)
        
        return output.getvalue().encode('utf-8')

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "gb18030", "iso-8859-1"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode('utf-8', errors='replace')
