from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.adapters import ensure_default_adapters_registered
from app.services.adapters.base import DEFAULT_MAX_FILE_SIZE, FORMAT_SIZE_LIMITS
from app.services.adapters.export_formats import get_supported_exports
from app.services.adapters.models import BlockNode, DocumentAST, NodeType, ParseResult
from app.services.adapters.multi_format_exporter import export_file as export_multi_format_file
from app.services.document_exporter import (
    BILINGUAL_LAYOUT_SOURCE_FIRST,
    BILINGUAL_LAYOUT_TARGET_FIRST,
    DOCX_MEDIA_TYPE,
    build_bilingual_docx_filename,
    build_translated_docx_filename,
    export_bilingual_docx_with_layout,
    export_translated_docx,
)
from app.services.document_workspace import (
    DOCUMENT_PARSE_MODE_FULL,
    build_docx_preview_html,
    build_docx_workspace,
    build_document_html_from_segments,
    normalize_document_parse_options,
    normalize_document_parse_mode,
)
from app.services.document_statistics import compute_word_document_statistics
from app.services.libreoffice_service import (
    LibreOfficeError,
    build_converted_docx_filename,
    convert_word_to_docx,
)
from app.services.matcher import MatchStats, match_sentences_with_stats

WORD_TASK_EXTENSIONS = {".doc", ".docx"}

TASK_ADAPTER_EXTENSIONS = {
    ".txt",
    ".dat",
    ".pptx",
    ".xlsx",
    ".csv",
    ".html",
    ".htm",
    ".md",
    ".markdown",
    ".json",
    ".yaml",
    ".yml",
    ".php",
    ".properties",
    ".po",
    ".pot",
    ".strings",
    ".srt",
    ".dita",
    ".ditamap",
    ".xml",
    ".svg",
    ".sdlxliff",
    ".txml",
    ".dxf",
    ".idml",
    ".mif",
    ".zip",
}

LOSSY_EXPORTABLE_TASK_EXTENSIONS = {
    ".html",
    ".htm",
}

DOCX_PARSE_MODE_CAPABILITIES = (
    {
        "id": "full",
        "label": "完整解析",
        "description": "解析正文、表格、页眉页脚、脚注尾注、文本框、编号、数学公式占位和 Word 批注。",
    },
    {
        "id": "body_only",
        "label": "仅正文解析",
        "description": "仅解析正文相关内容，页眉页脚、脚注尾注和 Word 批注保留原文。",
    },
)

AUTO_PARSE_MODE_CAPABILITY = {
    "id": "auto",
    "label": "按格式自动解析",
    "description": "后端根据文件格式提取真实可翻译文本。",
}

_UPLOAD_CAPABILITY_SPECS = (
    {
        "extensions": (".doc", ".docx"),
        "label": "Word 文档",
        "category": "office",
        "features": (
            "正文与表格",
            "页眉页脚、脚注尾注",
            "文本框、编号、数学公式占位",
            "Word 批注",
            "默认保留超链接",
        ),
        "settings": (
            {
                "id": "include_headers_footers",
                "label": "翻译页眉页脚",
                "default": True,
            },
            {
                "id": "include_footnotes_endnotes",
                "label": "翻译脚注尾注",
                "default": True,
            },
            {
                "id": "include_comments",
                "label": "翻译批注",
                "default": True,
            },
            {
                "id": "clean_format",
                "label": "清洗格式",
                "default": False,
            },
            {
                "id": "preserve_hyperlinks",
                "label": "保留超链接",
                "default": True,
                "description": "关闭后导出 Word 时会去除超链接，仅保留文本内容。",
            },
        ),
    },
    {
        "extensions": (".txt",),
        "label": "纯文本",
        "category": "text",
        "features": ("按空行识别段落", "自动断句", "支持 UTF-8 / UTF-8-BOM / GB18030"),
    },
    {
        "extensions": (".pptx",),
        "label": "PowerPoint 演示文稿",
        "category": "office",
        "features": (
            "提取幻灯片文本框和占位符",
            "提取表格单元格",
            "提取演讲者备注",
            "支持原格式写回",
        ),
        "settings_select_all": True,
        "settings": (
            {
                "id": "pptx_translate_comments",
                "label": "翻译批注",
                "default": True,
            },
            {
                "id": "pptx_translate_notes",
                "label": "翻译备注",
                "default": True,
            },
            {
                "id": "pptx_translate_document_properties",
                "label": "翻译文档属性",
                "default": False,
            },
        ),
    },
    {
        "extensions": (".xlsx",),
        "label": "Excel 工作簿",
        "category": "office",
        "features": (
            "默认提取非空单元格，包含数字、日期、布尔值和隐藏内容",
            "公式单元格可按设置纳入；纳入后会作为文本写回",
            "支持批注、图形文本、工作表名和文档属性选项",
            "支持按常见背景色跳过单元格",
        ),
        "settings_select_all": False,
        "settings": (
            {
                "id": "xlsx_translate_numeric_cells",
                "label": "纳入数字单元格",
                "default": True,
            },
            {
                "id": "xlsx_translate_date_cells",
                "label": "纳入日期/时间单元格",
                "default": True,
            },
            {
                "id": "xlsx_translate_boolean_cells",
                "label": "纳入布尔值单元格",
                "default": True,
            },
            {
                "id": "xlsx_translate_formula_cells",
                "label": "纳入公式单元格（会转为文本）",
                "default": False,
                "description": "开启后公式内容会进入翻译并在导出时写回为文本，原公式不再保留。",
            },
            {
                "id": "xlsx_translate_comments",
                "label": "翻译批注",
                "default": True,
            },
            {
                "id": "xlsx_translate_drawing_text",
                "label": "翻译图形文本",
                "default": True,
            },
            {
                "id": "xlsx_translate_sheet_names",
                "label": "翻译工作表名",
                "default": False,
            },
            {
                "id": "xlsx_translate_hidden_content",
                "label": "翻译隐藏内容",
                "default": True,
            },
            {
                "id": "xlsx_translate_document_properties",
                "label": "翻译文档属性",
                "default": False,
            },
            {
                "id": "xlsx_skip_fill_colors",
                "kind": "color_palette",
                "label": "跳过应用所选背景色的单元格",
                "default": [],
                "options": (
                    {"label": "深红", "value": "C00000"},
                    {"label": "红色", "value": "FF0000"},
                    {"label": "橙色", "value": "FFC000"},
                    {"label": "黄色", "value": "FFFF00"},
                    {"label": "浅绿", "value": "92D050"},
                    {"label": "绿色", "value": "00B050"},
                    {"label": "浅蓝", "value": "00B0F0"},
                    {"label": "蓝色", "value": "0070C0"},
                    {"label": "深蓝", "value": "002060"},
                    {"label": "紫色", "value": "7030A0"},
                ),
            },
        ),
    },
    {
        "extensions": (".dat",),
        "label": "DAT 文本",
        "category": "text",
        "features": ("按纯文本解析", "支持 UTF-8 / UTF-8-BOM / GB18030", "保留文本段落顺序"),
        "settings": (
            {
                "id": "translate_code_blocks",
                "label": "翻译代码块",
                "default": True,
            },
        ),
    },
    {
        "extensions": (".csv",),
        "label": "CSV 表格",
        "category": "table",
        "features": ("自动识别分隔符", "提取非数字单元格", "保留行列位置"),
    },
    {
        "extensions": (".html", ".htm"),
        "label": "HTML 网页",
        "category": "web",
        "features": ("提取可见文本", "跳过脚本和样式", "支持原格式导出"),
    },
    {
        "extensions": (".md", ".markdown"),
        "label": "Markdown",
        "category": "text",
        "features": ("标题、段落、列表和引用可翻译", "代码块可按设置提取", "保留 Markdown 结构定位"),
        "settings_select_all": True,
        "settings": (
            {
                "id": "translate_code_blocks",
                "label": "翻译代码块",
                "default": True,
            },
            {
                "id": "extract_links",
                "label": "提取链接",
                "default": False,
                "disabled": True,
                "description": "当前上传解析保留 Markdown 链接结构，暂不单独抽取 URL。",
            },
        ),
    },
    {
        "extensions": (".json",),
        "label": "JSON",
        "category": "localization",
        "features": ("递归提取字符串值", "保留键路径", "跳过非字符串值"),
    },
    {
        "extensions": (".yaml", ".yml"),
        "label": "YAML",
        "category": "localization",
        "features": ("提取字符串值", "保留配置路径", "跳过结构字段"),
        "settings": (
            {
                "id": "custom_parse_config",
                "label": "自定义解析配置",
                "default": False,
                "disabled": True,
                "description": "当前上传接口还没有接收自定义解析配置文件。",
            },
        ),
    },
    {
        "extensions": (".properties",),
        "label": "Properties",
        "category": "localization",
        "features": ("提取键值文本", "保留 key", "支持原格式导出"),
    },
    {
        "extensions": (".po", ".pot"),
        "label": "PO / POT",
        "category": "localization",
        "features": ("提取 msgid", "保留条目顺序", "支持原格式导出"),
    },
    {
        "extensions": (".strings",),
        "label": "Apple Strings",
        "category": "localization",
        "features": ("提取字符串值", "保留 key", "支持原格式导出"),
    },
    {
        "extensions": (".php",),
        "label": "PHP 本地化",
        "category": "localization",
        "features": ("提取数组字符串", "保留键路径", "跳过代码逻辑"),
    },
    {
        "extensions": (".srt",),
        "label": "SRT 字幕",
        "category": "subtitle",
        "features": ("提取字幕文本", "保留时间轴", "支持原格式导出"),
    },
    {
        "extensions": (".dita", ".ditamap", ".xml"),
        "label": "DITA / XML",
        "category": "technical",
        "features": ("提取可翻译元素文本", "保留结构路径", "支持原格式导出"),
        "settings": (
            {
                "id": "xml_inline_elements_no_split",
                "label": "将文本内所有元素视为内嵌元素(不断句)",
                "default": True,
            },
            {
                "id": "custom_parse_config",
                "label": "自定义解析配置",
                "default": False,
                "disabled": True,
                "description": "当前上传接口还没有接收自定义解析配置文件。",
            },
        ),
    },
    {
        "extensions": (".svg",),
        "label": "SVG",
        "category": "design",
        "features": ("提取 text / tspan 文本", "保留图形结构", "支持原格式导出"),
    },
    {
        "extensions": (".sdlxliff",),
        "label": "SDLXLIFF",
        "category": "bilingual",
        "features": ("提取双语交换句段", "保留单元 ID", "支持原格式导出"),
    },
    {
        "extensions": (".txml",),
        "label": "TXML",
        "category": "bilingual",
        "features": ("提取 Wordfast 句段", "保留单元 ID", "支持原格式导出"),
    },
    {
        "extensions": (".dxf",),
        "label": "DXF",
        "category": "engineering",
        "features": ("提取图纸文本", "保留文本实体定位", "支持原格式导出"),
        "settings": (
            {
                "id": "skip_non_translatable",
                "label": "非译元素(数字和符号)",
                "default": True,
            },
        ),
    },
    {
        "extensions": (".idml",),
        "label": "IDML",
        "category": "design",
        "features": ("提取 InDesign Story 文本", "保留 story 路径", "支持原格式导出"),
        "settings_select_all": True,
        "settings": (
            {
                "id": "translate_idml_comments",
                "label": "翻译附注",
                "default": False,
            },
            {
                "id": "translate_idml_hidden_layers",
                "label": "翻译隐藏图层",
                "default": False,
            },
        ),
    },
    {
        "extensions": (".mif",),
        "label": "MIF",
        "category": "technical",
        "features": ("提取 FrameMaker 文本", "保留结构定位", "支持原格式导出"),
    },
    {
        "extensions": (".zip",),
        "label": "ZIP 压缩包",
        "category": "archive",
        "features": ("递归解析包内支持格式", "跳过不支持文件", "按内部文件回写导出"),
    },
)


@dataclass(frozen=True)
class ExportedTaskFile:
    content: bytes
    media_type: str
    filename: str


BILINGUAL_DOCX_LAYOUT_EXPORT_ORDERS = {
    "bilingual_docx_layout_source_first": BILINGUAL_LAYOUT_SOURCE_FIRST,
    "bilingual_docx_layout_target_first": BILINGUAL_LAYOUT_TARGET_FIRST,
}


def get_task_file_extension(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def is_docx_task(filename: str) -> bool:
    return get_task_file_extension(filename) == ".docx"


def is_word_task(filename: str) -> bool:
    return get_task_file_extension(filename) in WORD_TASK_EXTENSIONS


def get_supported_task_extensions() -> tuple[str, ...]:
    registry = ensure_default_adapters_registered()
    extensions = {
        extension
        for extension in registry.list_supported_extensions()
        if extension in TASK_ADAPTER_EXTENSIONS
    }
    extensions.update(WORD_TASK_EXTENSIONS)
    return tuple(sorted(extensions))


def supports_task_file(filename: str) -> bool:
    extension = get_task_file_extension(filename)
    return extension in set(get_supported_task_extensions())


def can_export_task_file(filename: str, has_source_file: bool = True) -> bool:
    extension = get_task_file_extension(filename)
    if extension in WORD_TASK_EXTENSIONS:
        return has_source_file
    if not has_source_file:
        return extension in LOSSY_EXPORTABLE_TASK_EXTENSIONS
    return any(option.id == "original" for option in get_supported_exports(extension))


class UploadLimitError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def get_max_upload_size_bytes(filename: str) -> int:
    extension = get_task_file_extension(filename)
    registry = ensure_default_adapters_registered()
    try:
        adapter = registry.get_adapter(filename if extension else "file.txt")
        return adapter.get_max_file_size()
    except Exception:
        return FORMAT_SIZE_LIMITS.get(extension, DEFAULT_MAX_FILE_SIZE)


def validate_upload_batch(
    files: list[tuple[str, bytes]],
    *,
    max_files: int | None = None,
) -> None:
    settings = get_settings()
    limit_files = max_files if max_files is not None else settings.upload_max_files_per_batch
    max_total_bytes = settings.upload_max_total_size_mb * 1024 * 1024

    if len(files) > limit_files:
        raise UploadLimitError(
            f"文件数量超过限制，最多 {limit_files} 个。",
            status_code=400,
        )

    total = 0
    for filename, raw_bytes in files:
        max_size = get_max_upload_size_bytes(filename)
        size = len(raw_bytes)
        if size > max_size:
            max_mb = round(max_size / (1024 * 1024), 2)
            raise UploadLimitError(
                f"文件 {filename} 超过大小限制（{max_mb} MB）。",
                status_code=413,
            )
        total += size

    if total > max_total_bytes:
        raise UploadLimitError(
            f"上传总大小超过限制（{settings.upload_max_total_size_mb} MB）。",
            status_code=413,
        )


def validate_expanded_upload_batch(file_payloads: list[dict[str, Any]]) -> None:
    from app.services.import_task_storage import read_import_file_bytes

    files = [
        (payload.get("filename") or "source.txt", read_import_file_bytes(payload))
        for payload in file_payloads
    ]
    validate_upload_batch(files, max_files=get_settings().upload_max_expanded_files)


def get_upload_capabilities() -> dict[str, Any]:
    supported_extensions = get_supported_task_extensions()
    supported_set = set(supported_extensions)
    formats: list[dict[str, Any]] = []

    for spec in _UPLOAD_CAPABILITY_SPECS:
        extensions = [extension for extension in spec["extensions"] if extension in supported_set]
        if not extensions:
            continue

        is_word = set(extensions).issubset(WORD_TASK_EXTENSIONS)
        formats.append(
            {
                "extensions": extensions,
                "accept": ",".join(extensions),
                "label": spec["label"],
                "category": spec["category"],
                "max_size_mb": max(_get_upload_max_size_mb(extension) for extension in extensions),
                "can_export_original": any(can_export_task_file(f"file{extension}") for extension in extensions),
                "parse_modes": list(DOCX_PARSE_MODE_CAPABILITIES if is_word else (AUTO_PARSE_MODE_CAPABILITY,)),
                "features": list(spec["features"]),
                "settings": list(spec.get("settings", ())),
                "settings_select_all": bool(spec.get("settings_select_all", is_word)),
            }
        )

    settings = get_settings()
    return {
        "extensions": list(supported_extensions),
        "accept": ",".join(supported_extensions),
        "formats": formats,
        "limits": {
            "max_files_per_batch": settings.upload_max_files_per_batch,
            "max_total_size_mb": settings.upload_max_total_size_mb,
            "max_expanded_files": settings.upload_max_expanded_files,
        },
    }


def _get_upload_max_size_mb(extension: str) -> float:
    return round(get_max_upload_size_bytes(f"file{extension}") / (1024 * 1024), 2)


def build_task_workspace(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    similarity_threshold: float,
    collection_ids: list[UUID] | None = None,
    document_parse_mode: str = DOCUMENT_PARSE_MODE_FULL,
    document_parse_options: dict[str, object] | str | None = None,
) -> dict[str, Any]:
    document_parse_mode = normalize_document_parse_mode(document_parse_mode)
    document_parse_options = normalize_document_parse_options(document_parse_options, document_parse_mode)
    if is_word_task(filename):
        parse_bytes = raw_bytes
        parse_filename = filename
        original_filename = filename
        if get_task_file_extension(filename) == ".doc":
            try:
                parse_bytes = convert_word_to_docx(raw_bytes, filename)
            except LibreOfficeError as exc:
                raise ValueError(f"DOC 转 DOCX 失败：{exc}") from exc
            parse_filename = build_converted_docx_filename(filename)

        workspace = build_docx_workspace(
            db=db,
            raw_bytes=parse_bytes,
            similarity_threshold=similarity_threshold,
            collection_ids=collection_ids,
            document_parse_mode=document_parse_mode,
            document_parse_options=document_parse_options,
        )
        if get_task_file_extension(original_filename) == ".doc":
            workspace["_source_bytes"] = parse_bytes
            workspace["_source_filename"] = parse_filename
            workspace["document_statistics"] = compute_word_document_statistics(raw_bytes, original_filename)
        return workspace

    if not supports_task_file(filename):
        raise ValueError(f"暂不支持 {get_task_file_extension(filename) or '该'} 文件格式。")

    registry = ensure_default_adapters_registered()
    adapter = registry.get_adapter(filename)
    parse_result = adapter.parse_with_options(raw_bytes, filename=filename, options=document_parse_options)
    if not parse_result.segments:
        raise ValueError("文件中没有可翻译的内容。")

    source_sentences = [segment.source_text for segment in parse_result.segments]
    auxiliary_sentences = [segment.display_text for segment in parse_result.segments]
    match_results, match_stats = match_sentences_with_stats(
        db=db,
        sentences=source_sentences,
        similarity_threshold=similarity_threshold,
        auxiliary_sentences=auxiliary_sentences,
        collection_ids=collection_ids,
    )

    segments: list[dict[str, Any]] = []
    for index, (segment, match_result) in enumerate(zip(parse_result.segments, match_results, strict=False)):
        context = _build_segment_context(parse_result.ast, segment.block_path, fallback_index=index)
        segments.append(
            {
                "sentence_id": segment.segment_id,
                "source_text": segment.source_text,
                "display_text": segment.display_text,
                "target_text": match_result.target_text or "",
                "status": match_result.status,
                "score": match_result.score,
                "matched_source_text": match_result.matched_source_text or "",
                "matched_collection_name": match_result.matched_collection_name,
                "matched_creator_name": match_result.matched_creator_name,
                "matched_created_at": match_result.matched_created_at,
                "matched_updated_at": match_result.matched_updated_at,
                **context,
            }
        )

    return {
        "segments": segments,
        "document_html": build_document_html_from_segments(segments),
        "match_stats": asdict(match_stats),
    }


def build_task_preview_html(
    filename: str,
    segments: list[Any],
    source_bytes: bytes | None = None,
    document_parse_mode: str = DOCUMENT_PARSE_MODE_FULL,
    document_parse_options: dict[str, object] | str | None = None,
) -> str:
    document_parse_mode = normalize_document_parse_mode(document_parse_mode)
    document_parse_options = normalize_document_parse_options(document_parse_options, document_parse_mode)
    if source_bytes and is_word_task(filename):
        preview_bytes = source_bytes
        if get_task_file_extension(filename) == ".doc":
            try:
                preview_bytes = convert_word_to_docx(source_bytes, filename)
            except LibreOfficeError as exc:
                raise ValueError(f"DOC 转 DOCX 失败：{exc}") from exc
        return build_docx_preview_html(
            preview_bytes,
            document_parse_mode=document_parse_mode,
            document_parse_options=document_parse_options,
        )
    return build_document_html_from_segments(segments) if segments else ""


def export_translated_task_file(
    raw_bytes: bytes | None,
    filename: str,
    segments: list[Any],
    document_parse_mode: str = DOCUMENT_PARSE_MODE_FULL,
    document_parse_options: dict[str, object] | str | None = None,
    target_language: str | None = None,
) -> ExportedTaskFile:
    document_parse_mode = normalize_document_parse_mode(document_parse_mode)
    document_parse_options = normalize_document_parse_options(document_parse_options, document_parse_mode)
    if is_word_task(filename):
        if raw_bytes is None:
            raise ValueError("Word 源文件缺失，暂时无法导出。")
        export_bytes = raw_bytes
        export_filename = filename
        if get_task_file_extension(filename) == ".doc":
            try:
                export_bytes = convert_word_to_docx(raw_bytes, filename)
            except LibreOfficeError as exc:
                raise ValueError(f"DOC 转 DOCX 失败：{exc}") from exc
            export_filename = build_converted_docx_filename(filename)
        return ExportedTaskFile(
            content=export_translated_docx(
                raw_bytes=export_bytes,
                segments=segments,
                document_parse_mode=document_parse_mode,
                document_parse_options=document_parse_options,
                target_language=target_language,
            ),
            media_type=DOCX_MEDIA_TYPE,
            filename=build_translated_docx_filename(export_filename),
        )

    if raw_bytes is None:
        return _export_translated_task_file_without_source(filename, segments)

    extension = get_task_file_extension(filename)
    if extension == ".pptx":
        from app.services.adapters.pptx_exporter import PPTX_MEDIA_TYPE, PptxExporter

        export_segments = build_export_segments_from_source(
            raw_bytes,
            filename,
            segments,
            document_parse_options=document_parse_options,
        )
        return ExportedTaskFile(
            content=PptxExporter().export(
                raw_bytes,
                export_segments,
                document_parse_options=document_parse_options,
            ),
            media_type=PPTX_MEDIA_TYPE,
            filename=_build_translated_filename(filename),
        )

    if extension == ".xlsx":
        from app.services.adapters.xlsx_exporter import XLSX_MEDIA_TYPE, XlsxExporter

        export_segments = build_export_segments_from_source(
            raw_bytes,
            filename,
            segments,
            document_parse_options=document_parse_options,
        )
        return ExportedTaskFile(
            content=XlsxExporter().export(
                raw_bytes,
                export_segments,
                document_parse_options=document_parse_options,
            ),
            media_type=XLSX_MEDIA_TYPE,
            filename=_build_translated_filename(filename),
        )

    if not can_export_task_file(filename, has_source_file=True):
        raise ValueError(f"{get_task_file_extension(filename) or '该'} 文件暂不支持原格式导出。")

    export_segments = build_export_segments_from_source(
        raw_bytes,
        filename,
        segments,
        document_parse_options=document_parse_options,
    )
    content, media_type, export_filename = export_multi_format_file(
        export_type="original",
        segments=export_segments,
        filename=filename,
        original_bytes=raw_bytes,
    )
    return ExportedTaskFile(content=content, media_type=media_type, filename=export_filename)


def export_bilingual_task_docx_with_layout(
    raw_bytes: bytes | None,
    filename: str,
    segments: list[Any],
    order: str = BILINGUAL_LAYOUT_SOURCE_FIRST,
    document_parse_mode: str = DOCUMENT_PARSE_MODE_FULL,
    document_parse_options: dict[str, object] | str | None = None,
    target_language: str | None = None,
) -> ExportedTaskFile:
    document_parse_mode = normalize_document_parse_mode(document_parse_mode)
    document_parse_options = normalize_document_parse_options(document_parse_options, document_parse_mode)
    if not is_word_task(filename):
        raise ValueError("仅 Word 源文件支持保留排版的双语 Word 导出。")
    if raw_bytes is None:
        raise ValueError("Word 源文件缺失，暂时无法导出保留排版的双语 Word。")

    export_bytes = raw_bytes
    export_filename = filename
    if get_task_file_extension(filename) == ".doc":
        try:
            export_bytes = convert_word_to_docx(raw_bytes, filename)
        except LibreOfficeError as exc:
            raise ValueError(f"DOC 转 DOCX 失败：{exc}") from exc
        export_filename = build_converted_docx_filename(filename)

    return ExportedTaskFile(
        content=export_bilingual_docx_with_layout(
            raw_bytes=export_bytes,
            segments=segments,
            order=order,
            document_parse_mode=document_parse_mode,
            document_parse_options=document_parse_options,
            target_language=target_language,
        ),
        media_type=DOCX_MEDIA_TYPE,
        filename=build_bilingual_docx_filename(export_filename, order),
    )


def _export_translated_task_file_without_source(
    filename: str,
    segments: list[Any],
) -> ExportedTaskFile:
    extension = get_task_file_extension(filename)
    if extension not in LOSSY_EXPORTABLE_TASK_EXTENSIONS:
        raise ValueError("源文件缺失，当前格式暂时无法导出。")

    translated_segments = _build_translated_render_segments(segments)
    body_html = build_document_html_from_segments(translated_segments)
    content = (
        "<!DOCTYPE html>"
        "<html>"
        "<head><meta charset=\"utf-8\"></head>"
        f"<body>{body_html}</body>"
        "</html>"
    ).encode("utf-8")
    return ExportedTaskFile(
        content=content,
        media_type="text/html; charset=utf-8",
        filename=_build_translated_filename(filename),
    )


def build_export_segments_from_source(
    raw_bytes: bytes,
    filename: str,
    segments: list[Any],
    document_parse_options: dict[str, object] | str | None = None,
) -> list[dict[str, Any]]:
    if is_word_task(filename):
        return [_normalize_existing_segment(segment) for segment in segments]

    registry = ensure_default_adapters_registered()
    adapter = registry.get_adapter(filename)
    document_parse_options = normalize_document_parse_options(document_parse_options)
    parse_result = adapter.parse_with_options(raw_bytes, filename=filename, options=document_parse_options)
    translated_segments = {
        str(_get_segment_value(segment, "sentence_id", _get_segment_value(segment, "segment_id", ""))): segment
        for segment in segments
    }

    export_segments: list[dict[str, Any]] = []
    for index, parsed_segment in enumerate(parse_result.segments):
        translated_segment = translated_segments.get(parsed_segment.segment_id)
        context = _build_segment_context(parse_result.ast, parsed_segment.block_path, fallback_index=index)
        export_segments.append(
            {
                "segment_id": parsed_segment.segment_id,
                "sentence_id": parsed_segment.segment_id,
                "source_text": parsed_segment.source_text,
                "display_text": parsed_segment.display_text,
                "target_text": _get_segment_value(translated_segment, "target_text", ""),
                "status": _get_segment_value(translated_segment, "status", "none"),
                "matched_source_text": _get_segment_value(translated_segment, "matched_source_text", ""),
                **context,
            }
        )

    return export_segments


def _normalize_existing_segment(segment: Any) -> dict[str, Any]:
    return {
        "segment_id": _get_segment_value(segment, "segment_id", _get_segment_value(segment, "sentence_id", "")),
        "sentence_id": _get_segment_value(segment, "sentence_id", _get_segment_value(segment, "segment_id", "")),
        "source_text": _get_segment_value(segment, "source_text", ""),
        "display_text": _get_segment_value(segment, "display_text", ""),
        "target_text": _get_segment_value(segment, "target_text", ""),
        "status": _get_segment_value(segment, "status", "none"),
        "matched_source_text": _get_segment_value(segment, "matched_source_text", ""),
        "block_type": _get_segment_value(segment, "block_type", "paragraph"),
        "block_index": _get_segment_value(segment, "block_index", 0),
        "row_index": _get_segment_value(segment, "row_index"),
        "cell_index": _get_segment_value(segment, "cell_index"),
    }


def _build_translated_render_segments(segments: list[Any]) -> list[dict[str, Any]]:
    render_segments: list[dict[str, Any]] = []
    for segment in segments:
        normalized = _normalize_existing_segment(segment)
        translated_text = str(
            _get_segment_value(segment, "target_text")
            or _get_segment_value(segment, "display_text")
            or _get_segment_value(segment, "source_text")
            or ""
        )
        normalized["display_text"] = translated_text
        render_segments.append(normalized)
    return render_segments


def _build_segment_context(
    ast: DocumentAST,
    block_path: str,
    fallback_index: int,
) -> dict[str, Any]:
    node = _resolve_node_by_path(ast, block_path)
    root_index = _resolve_root_index(block_path, fallback_index)
    metadata = dict(node.metadata or {}) if node else {}
    block_type = "table_cell" if node and node.node_type == NodeType.TABLE_CELL else "paragraph"

    block_index = root_index
    if block_type == "table_cell":
        if ".children." not in block_path and ("row" in metadata or "col" in metadata):
            block_index = _to_int(metadata.get("table_index"), default=0)
        else:
            block_index = _to_int(metadata.get("table_index"), default=root_index)

    context: dict[str, Any] = {
        "block_type": block_type,
        "block_index": block_index,
        "row_index": _to_optional_int(metadata.get("row_index", metadata.get("row"))),
        "cell_index": _to_optional_int(metadata.get("cell_index", metadata.get("col"))),
    }

    key = metadata.get("key")
    if key is not None:
        context["key"] = str(key)
        context["metadata_path"] = str(key)

    subtitle_index = metadata.get("index")
    if subtitle_index is not None:
        context["index"] = subtitle_index
        context["subtitle_index"] = subtitle_index

    for field in ("id", "tu_id", "start", "end", "zip_path", "rar_path", "file_type"):
        value = metadata.get(field)
        if value is not None and not isinstance(value, (dict, list, set, tuple)):
            context[field] = value

    return context


def _resolve_node_by_path(ast: DocumentAST, block_path: str) -> BlockNode | None:
    if not block_path:
        return None

    parts = block_path.split(".")
    try:
        node = ast.nodes[int(parts[0])]
    except (IndexError, TypeError, ValueError):
        return None

    index = 1
    while index < len(parts):
        if parts[index] != "children" or index + 1 >= len(parts):
            return None
        try:
            node = node.children[int(parts[index + 1])]
        except (IndexError, TypeError, ValueError):
            return None
        index += 2

    return node


def _resolve_root_index(block_path: str, fallback_index: int) -> int:
    if not block_path:
        return fallback_index
    try:
        return int(block_path.split(".", 1)[0])
    except (TypeError, ValueError):
        return fallback_index


def _get_segment_value(segment: Any, field_name: str, default: Any = None) -> Any:
    if segment is None:
        return default
    if isinstance(segment, dict):
        return segment.get(field_name, default)
    return getattr(segment, field_name, default)


def _to_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any, default: int) -> int:
    optional_value = _to_optional_int(value)
    return default if optional_value is None else optional_value


def _build_translated_filename(filename: str) -> str:
    path = Path(filename or "translated.html")
    extension = path.suffix or ".html"
    stem = path.stem or "translated"
    return f"{stem}_translated{extension}"
