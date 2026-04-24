"""
多格式导出服务 - 统一处理各种导出格式

支持的导出类型：
- original: 原格式导出（仅译文）
- bilingual: 原格式双语对照文件（源文+译文）
- bilingual_docx: 双语 Word 文档
- bilingual_txt: 双语对照文本
- tmx: TMX 翻译记忆库
- xliff: XLIFF 工作流文件
"""
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from urllib.parse import quote

from app.services.adapters.export_formats import (
    EXPORT_OPTIONS,
    FORMAT_EXPORT_SUPPORT,
    get_supported_exports,
    get_export_option,
)
from app.services.adapters.tmx_exporter import TmxExporter
from app.services.adapters.xliff_exporter import XliffExporter


class MultiFormatExporter:
    """多格式导出器
    
    根据原始文件格式和用户选择的导出类型，生成对应的导出文件。
    """

    def __init__(
        self,
        source_lang: str = "zh-CN",
        target_lang: str = "en-US",
    ):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.tmx_exporter = TmxExporter(source_lang, target_lang)
        self.xliff_exporter = XliffExporter(source_lang, target_lang)

    def get_available_exports(self, filename: str) -> List[dict]:
        """获取文件支持的导出选项
        
        Args:
            filename: 原始文件名
            
        Returns:
            List[dict]: 导出选项列表
        """
        ext = Path(filename).suffix.lower()
        options = get_supported_exports(ext)
        
        return [
            {
                "id": opt.id,
                "name": opt.name,
                "description": opt.description,
                "extension": opt.extension or ext,
            }
            for opt in options
        ]

    def export(
        self,
        export_type: str,
        segments: List,
        filename: str,
        original_bytes: Optional[bytes] = None,
    ) -> Tuple[bytes, str, str]:
        """执行导出
        
        Args:
            export_type: 导出类型 ID
            segments: 句段列表
            filename: 原始文件名
            original_bytes: 原始文件字节（用于原格式导出）
            
        Returns:
            Tuple[bytes, mime_type, export_filename]
        """
        ext = Path(filename).suffix.lower()
        base_name = Path(filename).stem
        
        # 转换句段为标准格式
        std_segments = self._normalize_segments(segments)
        
        if export_type == "original":
            return self._export_original(ext, std_segments, filename, original_bytes)
        elif export_type == "bilingual":
            return self._export_bilingual_original(ext, std_segments, filename, original_bytes)
        elif export_type == "bilingual_docx":
            return self._export_bilingual_docx(std_segments, base_name)
        elif export_type == "bilingual_txt":
            return self._export_bilingual_txt(std_segments, base_name)
        elif export_type == "tmx":
            return self._export_tmx(std_segments, base_name)
        elif export_type in ("xliff", "xliff2"):
            version = "2.0" if export_type == "xliff2" else "1.2"
            return self._export_xliff(std_segments, filename, version)
        else:
            raise ValueError(f"不支持的导出类型: {export_type}")

    def _normalize_segments(self, segments: List) -> List[dict]:
        """将句段转换为标准字典格式"""
        result = []
        for i, seg in enumerate(segments):
            if isinstance(seg, dict):
                result.append({
                    "segment_id": seg.get("segment_id", seg.get("sentence_id", f"seg_{i}")),
                    "source_text": seg.get("source_text", ""),
                    "target_text": seg.get("target_text", ""),
                    "status": seg.get("status", "none"),
                    "matched_source_text": seg.get("matched_source_text", ""),
                })
            else:
                result.append({
                    "segment_id": getattr(seg, "sentence_id", f"seg_{i}"),
                    "source_text": getattr(seg, "source_text", ""),
                    "target_text": getattr(seg, "target_text", ""),
                    "status": getattr(seg, "status", "none"),
                    "matched_source_text": getattr(seg, "matched_source_text", ""),
                })
        return result

    def _export_original(
        self,
        ext: str,
        segments: List[dict],
        filename: str,
        original_bytes: Optional[bytes],
    ) -> Tuple[bytes, str, str]:
        """导出原格式"""
        from app.services.universal_exporter import export_translated_file
        
        if original_bytes is None:
            raise ValueError("原格式导出需要原始文件")
        
        return export_translated_file(original_bytes, filename, segments)

    def _export_bilingual_original(
        self,
        ext: str,
        segments: List[dict],
        filename: str,
        original_bytes: Optional[bytes],
    ) -> Tuple[bytes, str, str]:
        """导出原格式双语文件（源文+译文并排）"""
        if original_bytes is None:
            raise ValueError("原格式双语导出需要原始文件")
        
        base_name = Path(filename).stem
        
        # 根据格式选择对应的双语导出器
        bilingual_exporters = {
            ".properties": self._export_bilingual_properties,
            ".po": self._export_bilingual_po,
            ".pot": self._export_bilingual_po,
            ".strings": self._export_bilingual_strings,
            ".html": self._export_bilingual_html,
            ".htm": self._export_bilingual_html,
            ".srt": self._export_bilingual_srt,
        }
        
        exporter_func = bilingual_exporters.get(ext)
        if exporter_func:
            content = exporter_func(original_bytes, segments)
            mime_type = self._get_mime_type(ext)
            export_filename = f"{base_name}-bilingual{ext}"
            return content, mime_type, export_filename
        
        # 不支持原格式双语的，回退到双语文本
        return self._export_bilingual_txt(segments, base_name)

    def _get_mime_type(self, ext: str) -> str:
        """获取 MIME 类型"""
        mime_map = {
            ".properties": "text/plain; charset=utf-8",
            ".po": "text/x-gettext-translation",
            ".pot": "text/x-gettext-translation",
            ".strings": "text/plain; charset=utf-8",
            ".html": "text/html; charset=utf-8",
            ".htm": "text/html; charset=utf-8",
            ".srt": "text/plain; charset=utf-8",
        }
        return mime_map.get(ext, "application/octet-stream")

    def _export_bilingual_properties(
        self,
        original_bytes: bytes,
        segments: List[dict],
    ) -> bytes:
        """导出双语 Properties 文件"""
        from app.services.adapters.properties_exporter import PropertiesExporter
        
        exporter = PropertiesExporter()
        content = exporter._decode_content(original_bytes)
        lines = content.replace('\r\n', '\n').split('\n')
        result_lines = []
        
        # 构建源文到译文的映射
        source_to_target = {seg["source_text"]: seg["target_text"] for seg in segments if seg.get("source_text")}
        
        for line in lines:
            # 空行或注释行
            if not line.strip() or line.lstrip().startswith(('#', '!')):
                result_lines.append(line)
                continue
            
            # 解析键值对
            key, value, separator = exporter._parse_line(line)
            
            if key and value.strip():
                source = value.strip()
                target = source_to_target.get(source, "")
                # 双语格式：源文 | 译文
                bilingual_value = f"{source} | {target}" if target else source
                bilingual_value = exporter._escape_value(bilingual_value)
                result_lines.append(f"{key}{separator}{bilingual_value}")
            else:
                result_lines.append(line)
        
        return '\n'.join(result_lines).encode('utf-8')

    def _export_bilingual_po(
        self,
        original_bytes: bytes,
        segments: List[dict],
    ) -> bytes:
        """导出双语 PO 文件 - PO 本身就是双语格式，直接填充 msgstr"""
        from app.services.adapters.po_exporter import PoExporter
        
        # 构建 msgid -> msgstr 映射
        translations = {seg["source_text"]: seg["target_text"] for seg in segments if seg.get("source_text")}
        
        exporter = PoExporter()
        return exporter.export(original_bytes, translations)

    def _export_bilingual_strings(
        self,
        original_bytes: bytes,
        segments: List[dict],
    ) -> bytes:
        """导出双语 Strings 文件"""
        from app.services.adapters.strings_exporter import StringsExporter
        
        exporter = StringsExporter()
        content = exporter._decode_content(original_bytes)
        
        # 构建源文到译文的映射
        source_to_target = {seg["source_text"]: seg["target_text"] for seg in segments if seg.get("source_text")}
        
        import re
        def replace_value(match):
            key = exporter._unescape(match.group(1))
            original_value = exporter._unescape(match.group(2))
            
            target = source_to_target.get(original_value, "")
            # 双语格式：源文 | 译文
            bilingual = f"{original_value} | {target}" if target else original_value
            escaped = exporter._escape(bilingual)
            return f'"{match.group(1)}" = "{escaped}";'
        
        pattern = r'"([^"\\]*(?:\\.[^"\\]*)*)"\s*=\s*"([^"\\]*(?:\\.[^"\\]*)*)"\s*;'
        result = re.sub(pattern, replace_value, content)
        
        return result.encode('utf-8')

    def _export_bilingual_html(
        self,
        original_bytes: bytes,
        segments: List[dict],
    ) -> bytes:
        """导出双语 HTML 文件 - 在每个文本后添加译文"""
        from app.services.adapters.html_exporter import HtmlExporter
        
        exporter = HtmlExporter()
        content = exporter._decode_content(original_bytes)
        
        # 构建源文到译文的映射
        source_to_target = {seg["source_text"]: seg["target_text"] for seg in segments if seg.get("source_text")}
        
        import re
        def replace_text_node(match):
            text = match.group(1)
            stripped = text.strip()
            if stripped in source_to_target and source_to_target[stripped]:
                target = source_to_target[stripped]
                # 保留原始空白
                leading = text[:len(text) - len(text.lstrip())]
                trailing = text[len(text.rstrip()):]
                # 双语格式：源文 <br/> 译文
                bilingual = f"{leading}{stripped}<br/><span style=\"color:#666;\">{target}</span>{trailing}"
                return f">{bilingual}<"
            return match.group(0)
        
        result = re.sub(r'>([^<]+)<', replace_text_node, content)
        return result.encode('utf-8')

    def _export_bilingual_srt(
        self,
        original_bytes: bytes,
        segments: List[dict],
    ) -> bytes:
        """导出双语 SRT 字幕 - 每条字幕下方添加译文"""
        from app.services.adapters.srt_exporter import SrtExporter, TIMECODE_PATTERN
        
        exporter = SrtExporter()
        content = exporter._decode_content(original_bytes)
        content = content.replace('\r\n', '\n').replace('\r', '\n')
        
        # 构建源文到译文的映射
        source_to_target = {seg["source_text"]: seg["target_text"] for seg in segments if seg.get("source_text")}
        
        import re
        blocks = re.split(r'\n\n+', content.strip())
        result_blocks = []
        
        for block in blocks:
            if not block.strip():
                continue
            
            lines = block.strip().split('\n')
            if len(lines) < 2:
                result_blocks.append(block)
                continue
            
            try:
                index = int(lines[0].strip())
            except ValueError:
                result_blocks.append(block)
                continue
            
            if not TIMECODE_PATTERN.match(lines[1]):
                result_blocks.append(block)
                continue
            
            original_text = '\n'.join(lines[2:])
            clean_text = re.sub(r'<[^>]+>', '', original_text).strip()
            
            target = source_to_target.get(clean_text, "")
            if target:
                # 双语格式：源文换行后加译文
                result_blocks.append(f"{lines[0]}\n{lines[1]}\n{original_text}\n{target}")
            else:
                result_blocks.append(block)
        
        return '\n\n'.join(result_blocks).encode('utf-8')

    def _export_bilingual_docx(
        self,
        segments: List[dict],
        base_name: str,
    ) -> Tuple[bytes, str, str]:
        """导出双语 DOCX"""
        from docx import Document
        from docx.shared import Pt, RGBColor
        from docx.enum.table import WD_TABLE_ALIGNMENT
        
        doc = Document()
        
        # 添加标题
        title = doc.add_heading("双语对照文档", level=1)
        
        # 创建表格
        table = doc.add_table(rows=1, cols=2)
        table.style = 'Table Grid'
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        
        # 表头
        header_cells = table.rows[0].cells
        header_cells[0].text = "源文"
        header_cells[1].text = "译文"
        
        # 添加内容
        for seg in segments:
            source = seg.get("source_text", "")
            target = seg.get("target_text", "")
            
            if not source:
                continue
            
            row = table.add_row()
            row.cells[0].text = source
            row.cells[1].text = target or ""
        
        buffer = BytesIO()
        doc.save(buffer)
        
        mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        export_filename = f"{base_name}-bilingual.docx"
        
        return buffer.getvalue(), mime_type, export_filename

    def _export_bilingual_txt(
        self,
        segments: List[dict],
        base_name: str,
    ) -> Tuple[bytes, str, str]:
        """导出双语文本"""
        lines = []
        
        for i, seg in enumerate(segments, 1):
            source = seg.get("source_text", "")
            target = seg.get("target_text", "")
            
            if not source:
                continue
            
            lines.append(f"[{i}] 源文: {source}")
            lines.append(f"[{i}] 译文: {target or '(未翻译)'}")
            lines.append("")
        
        content = "\n".join(lines)
        mime_type = "text/plain; charset=utf-8"
        export_filename = f"{base_name}-bilingual.txt"
        
        return content.encode("utf-8"), mime_type, export_filename

    def _export_tmx(
        self,
        segments: List[dict],
        base_name: str,
    ) -> Tuple[bytes, str, str]:
        """导出 TMX"""
        content = self.tmx_exporter.export(segments, base_name)
        mime_type = "application/x-tmx+xml"
        export_filename = f"{base_name}.tmx"
        
        return content, mime_type, export_filename

    def _export_xliff(
        self,
        segments: List[dict],
        filename: str,
        version: str = "1.2",
    ) -> Tuple[bytes, str, str]:
        """导出 XLIFF"""
        base_name = Path(filename).stem
        ext = Path(filename).suffix.lower()
        
        # 确定原始格式
        format_map = {
            ".docx": "winword",
            ".pdf": "pdf",
            ".pptx": "powerpoint",
            ".txt": "plaintext",
            ".html": "html",
            ".htm": "html",
            ".xml": "xml",
            ".dita": "xml",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
        }
        original_format = format_map.get(ext, "plaintext")
        
        self.xliff_exporter.version = version
        content = self.xliff_exporter.export(segments, filename, original_format)
        
        mime_type = "application/xliff+xml"
        export_filename = f"{base_name}.xlf"
        
        return content, mime_type, export_filename


# 便捷函数
def get_export_options_for_file(filename: str) -> List[dict]:
    """获取文件支持的导出选项"""
    exporter = MultiFormatExporter()
    return exporter.get_available_exports(filename)


def export_file(
    export_type: str,
    segments: List,
    filename: str,
    original_bytes: Optional[bytes] = None,
    source_lang: str = "zh-CN",
    target_lang: str = "en-US",
) -> Tuple[bytes, str, str]:
    """导出文件"""
    exporter = MultiFormatExporter(source_lang, target_lang)
    return exporter.export(export_type, segments, filename, original_bytes)
