from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.adapters import ensure_default_adapters_registered
from app.services.adapters.export_formats import get_supported_exports
from app.services.adapters.models import BlockNode, DocumentAST, NodeType, ParseResult
from app.services.adapters.multi_format_exporter import export_file as export_multi_format_file
from app.services.document_exporter import (
    DOCX_MEDIA_TYPE,
    build_translated_docx_filename,
    export_translated_docx,
)
from app.services.document_workspace import (
    DOCUMENT_PARSE_MODE_FULL,
    build_docx_preview_html,
    build_docx_workspace,
    build_document_html_from_segments,
    normalize_document_parse_mode,
)
from app.services.matcher import MatchStats, match_sentences_with_stats

TASK_ADAPTER_EXTENSIONS = {
    ".txt",
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


@dataclass(frozen=True)
class ExportedTaskFile:
    content: bytes
    media_type: str
    filename: str


def get_task_file_extension(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def is_docx_task(filename: str) -> bool:
    return get_task_file_extension(filename) == ".docx"


def get_supported_task_extensions() -> tuple[str, ...]:
    registry = ensure_default_adapters_registered()
    extensions = {
        extension
        for extension in registry.list_supported_extensions()
        if extension in TASK_ADAPTER_EXTENSIONS
    }
    extensions.add(".docx")
    return tuple(sorted(extensions))


def supports_task_file(filename: str) -> bool:
    extension = get_task_file_extension(filename)
    return extension in set(get_supported_task_extensions())


def can_export_task_file(filename: str, has_source_file: bool = True) -> bool:
    extension = get_task_file_extension(filename)
    if extension == ".docx":
        return has_source_file
    if not has_source_file:
        return extension in LOSSY_EXPORTABLE_TASK_EXTENSIONS
    return any(option.id == "original" for option in get_supported_exports(extension))


def build_task_workspace(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    similarity_threshold: float,
    collection_ids: list[UUID] | None = None,
    document_parse_mode: str = DOCUMENT_PARSE_MODE_FULL,
) -> dict[str, Any]:
    document_parse_mode = normalize_document_parse_mode(document_parse_mode)
    if is_docx_task(filename):
        return build_docx_workspace(
            db=db,
            raw_bytes=raw_bytes,
            similarity_threshold=similarity_threshold,
            collection_ids=collection_ids,
            document_parse_mode=document_parse_mode,
        )

    if not supports_task_file(filename):
        raise ValueError(f"暂不支持 {get_task_file_extension(filename) or '该'} 文件格式。")

    registry = ensure_default_adapters_registered()
    adapter = registry.get_adapter(filename)
    parse_result = adapter.parse_with_validation(raw_bytes, filename=filename)
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
) -> str:
    document_parse_mode = normalize_document_parse_mode(document_parse_mode)
    if source_bytes and is_docx_task(filename):
        return build_docx_preview_html(source_bytes, document_parse_mode=document_parse_mode)
    return build_document_html_from_segments(segments) if segments else ""


def export_translated_task_file(
    raw_bytes: bytes | None,
    filename: str,
    segments: list[Any],
    document_parse_mode: str = DOCUMENT_PARSE_MODE_FULL,
) -> ExportedTaskFile:
    document_parse_mode = normalize_document_parse_mode(document_parse_mode)
    if is_docx_task(filename):
        if raw_bytes is None:
            raise ValueError("DOCX 源文件缺失，暂时无法导出。")
        return ExportedTaskFile(
            content=export_translated_docx(
                raw_bytes=raw_bytes,
                segments=segments,
                document_parse_mode=document_parse_mode,
            ),
            media_type=DOCX_MEDIA_TYPE,
            filename=build_translated_docx_filename(filename),
        )

    if raw_bytes is None:
        return _export_translated_task_file_without_source(filename, segments)

    if not can_export_task_file(filename, has_source_file=True):
        raise ValueError(f"{get_task_file_extension(filename) or '该'} 文件暂不支持原格式导出。")

    export_segments = build_export_segments_from_source(raw_bytes, filename, segments)
    content, media_type, export_filename = export_multi_format_file(
        export_type="original",
        segments=export_segments,
        filename=filename,
        original_bytes=raw_bytes,
    )
    return ExportedTaskFile(content=content, media_type=media_type, filename=export_filename)


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
) -> list[dict[str, Any]]:
    if is_docx_task(filename):
        return [_normalize_existing_segment(segment) for segment in segments]

    registry = ensure_default_adapters_registered()
    adapter = registry.get_adapter(filename)
    parse_result = adapter.parse_with_validation(raw_bytes, filename=filename)
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
