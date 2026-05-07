"""
通用导出服务 - 根据文件格式选择合适的导出器

支持所有已实现的格式，自动选择正确的导出器。
"""
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import quote

from app.services.adapters import (
    ExportService,
    HtmlExporter,
    PropertiesExporter,
    PoExporter,
    StringsExporter,
    MarkdownExporter,
    SrtExporter,
    CsvExporter,
    SdlxliffExporter,
    TxmlExporter,
    DxfExporter,
    ZipExporter,
    IdmlExporter,
    MifExporter,
    RarExporter,
    DitaExporter,
    SvgExporter,
)


# 格式到 MIME 类型的映射
MIME_TYPES = {
    ".txt": "text/plain; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".properties": "text/plain; charset=utf-8",
    ".po": "text/plain; charset=utf-8",
    ".pot": "text/plain; charset=utf-8",
    ".strings": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".markdown": "text/markdown; charset=utf-8",
    ".srt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".yaml": "text/yaml; charset=utf-8",
    ".yml": "text/yaml; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".dita": "application/xml; charset=utf-8",
    ".ditamap": "application/xml; charset=utf-8",
    ".svg": "image/svg+xml",
    ".sdlxliff": "application/xml; charset=utf-8",
    ".txml": "application/xml; charset=utf-8",
    ".dxf": "application/dxf",
    ".zip": "application/zip",
    ".idml": "application/vnd.adobe.indesign-idml-package",
    ".mif": "application/x-mif",
    ".rar": "application/zip",  # RAR 导出为 ZIP
    ".pdf": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # PDF 导出为 DOCX
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".php": "text/plain; charset=utf-8",
}

# 需要转换格式的映射
FORMAT_CONVERSION = {
    ".rar": ".zip",
    ".pdf": ".docx",
}


def get_export_info(filename: str) -> Tuple[str, str, str]:
    """获取导出信息

    Args:
        filename: 原始文件名

    Returns:
        Tuple[export_ext, mime_type, export_filename]
    """
    ext = Path(filename).suffix.lower()
    base_name = Path(filename).stem

    # 检查是否需要格式转换
    export_ext = FORMAT_CONVERSION.get(ext, ext)
    mime_type = MIME_TYPES.get(export_ext, "application/octet-stream")

    export_filename = f"{base_name}-translated{export_ext}"

    return export_ext, mime_type, export_filename


def export_translated_file(
    original_bytes: bytes,
    filename: str,
    segments: List,
) -> Tuple[bytes, str, str]:
    """导出翻译后的文件

    Args:
        original_bytes: 原始文件字节
        filename: 原始文件名
        segments: 句段列表（包含 source_text 和 target_text）

    Returns:
        Tuple[exported_bytes, mime_type, export_filename]
    """
    ext = Path(filename).suffix.lower()
    export_ext, mime_type, export_filename = get_export_info(filename)

    # 构建翻译映射
    translations = {}
    for seg in segments:
        source = getattr(seg, 'source_text', None) or seg.get('source_text', '') if isinstance(seg, dict) else seg.source_text
        target = getattr(seg, 'target_text', None) or seg.get('target_text', '') if isinstance(seg, dict) else seg.target_text
        if source and target:
            translations[source] = target

    # 根据格式选择导出器
    exported_bytes = _export_by_format(ext, original_bytes, translations, segments)

    return exported_bytes, mime_type, export_filename


def _export_by_format(
    ext: str,
    original_bytes: bytes,
    translations: Dict[str, str],
    segments: List,
) -> bytes:
    """根据格式导出"""

    # 文本格式 - 直接替换
    if ext == ".txt":
        return _export_txt(original_bytes, translations)

    # HTML
    if ext in (".html", ".htm"):
        return HtmlExporter().export(original_bytes, translations)

    # Properties
    if ext == ".properties":
        # 构建 key -> target 映射
        key_translations = {}
        for seg in segments:
            key = _get_segment_key(seg)
            target = _get_segment_target(seg)
            if key and target:
                key_translations[key] = target
        return PropertiesExporter().export(original_bytes, key_translations)

    # PO
    if ext in (".po", ".pot"):
        return PoExporter().export(original_bytes, translations)

    # Strings
    if ext == ".strings":
        key_translations = {}
        for seg in segments:
            key = _get_segment_key(seg)
            target = _get_segment_target(seg)
            if key and target:
                key_translations[key] = target
        return StringsExporter().export(original_bytes, key_translations)

    # Markdown
    if ext in (".md", ".markdown"):
        return MarkdownExporter().export(original_bytes, translations)

    # SRT
    if ext == ".srt":
        return SrtExporter().export(original_bytes, translations)

    # CSV
    if ext == ".csv":
        return CsvExporter().export(original_bytes, translations)

    # YAML/JSON - 简单文本替换
    if ext in (".yaml", ".yml", ".json"):
        return _export_txt(original_bytes, translations)

    # PHP
    if ext == ".php":
        return _export_txt(original_bytes, translations)

    # DITA
    if ext in (".dita", ".ditamap", ".xml"):
        return DitaExporter().export_with_translations(original_bytes, translations)

    # SVG
    if ext == ".svg":
        exporter = SvgExporter()
        result, _ = exporter.export(original_bytes, translations)
        return result

    # SDL XLIFF
    if ext == ".sdlxliff":
        return SdlxliffExporter().export(original_bytes, translations)

    # TXML
    if ext == ".txml":
        return TxmlExporter().export(original_bytes, translations)

    # DXF
    if ext == ".dxf":
        return DxfExporter().export(original_bytes, translations)

    # ZIP
    if ext == ".zip":
        return ZipExporter().export(original_bytes, translations)

    # IDML
    if ext == ".idml":
        return IdmlExporter().export(original_bytes, translations)

    # MIF
    if ext == ".mif":
        return MifExporter().export(original_bytes, translations)

    # RAR -> ZIP
    if ext == ".rar":
        return RarExporter().export(original_bytes, translations)

    # DOCX - 使用专用导出器
    if ext == ".docx":
        from app.services.document_exporter import export_translated_docx
        return export_translated_docx(original_bytes, segments)

    # PDF -> DOCX
    if ext == ".pdf":
        # PDF 无法直接导出，转换为 DOCX
        return _export_as_docx(segments)

    # PPTX - 暂时导出为文本
    if ext == ".pptx":
        return _export_as_docx(segments)

    # 默认：文本替换
    return _export_txt(original_bytes, translations)


def _export_txt(original_bytes: bytes, translations: Dict[str, str]) -> bytes:
    """简单文本替换导出"""
    try:
        content = original_bytes.decode('utf-8')
    except UnicodeDecodeError:
        content = original_bytes.decode('utf-8', errors='replace')

    for source, target in translations.items():
        content = content.replace(source, target)

    return content.encode('utf-8')


def _export_as_docx(segments: List) -> bytes:
    """将句段导出为 DOCX"""
    from docx import Document
    from io import BytesIO

    doc = Document()
    for seg in segments:
        target = _get_segment_target(seg) or _get_segment_source(seg)
        if target:
            doc.add_paragraph(target)

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _get_segment_key(seg) -> str:
    """获取句段的 key"""
    if isinstance(seg, dict):
        return seg.get('metadata', {}).get('key', '')
    return getattr(seg, 'metadata', {}).get('key', '') if hasattr(seg, 'metadata') else ''


def _get_segment_source(seg) -> str:
    """获取句段的源文"""
    if isinstance(seg, dict):
        return seg.get('source_text', '')
    return getattr(seg, 'source_text', '')


def _get_segment_target(seg) -> str:
    """获取句段的译文"""
    if isinstance(seg, dict):
        return seg.get('target_text', '')
    return getattr(seg, 'target_text', '')
