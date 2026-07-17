from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import FileRecord, Segment, User
from app.services.english_variant_converter import (
    EnglishVariantConverter,
    convert_html_fragment,
    get_default_converter,
)
from app.services.file_record_service import (
    build_labeled_copy_filename,
    copy_file_record_source,
    create_file_record_copy_shell,
    get_segment_ordering_for_file_record,
)
from app.services.normalizer import build_source_hash


ENGLISH_VARIANT_SOURCE = "english_variant_conversion"
SUPPORTED_CHINESE_SOURCE_LANGUAGES = frozenset({"zh-CN", "zh-TW", "zh-HK", "zh-MO"})


EnglishVariantCopyErrorCode = Literal[
    "not_found",
    "unsupported_language_pair",
    "active_operation",
    "empty_translation",
]


@dataclass(frozen=True)
class EnglishVariantCopySpec:
    target_language: Literal["en-US", "en-GB"]
    target_style: Literal["american", "british"]
    filename_label: str
    source_variant_label: str


ENGLISH_VARIANT_COPY_SPECS: dict[str, EnglishVariantCopySpec] = {
    "en-US": EnglishVariantCopySpec(
        target_language="en-GB",
        target_style="british",
        filename_label="英式英语",
        source_variant_label="美式英语",
    ),
    "en-GB": EnglishVariantCopySpec(
        target_language="en-US",
        target_style="american",
        filename_label="美式英语",
        source_variant_label="英式英语",
    ),
}
_ENGLISH_VARIANT_FILENAME_SUFFIX = re.compile(r"\s-\s(?:英式英语|美式英语)(?:\s+\d+)?$")


class EnglishVariantCopyError(ValueError):
    def __init__(self, code: EnglishVariantCopyErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EnglishVariantCopySummary:
    processed_segments: int
    changed_segments: int
    replacement_count: int


@dataclass(frozen=True)
class EnglishVariantCopyResult:
    file_record: FileRecord
    summary: EnglishVariantCopySummary


def _normalize_variant_copy_source_filename(filename: str) -> str:
    path = Path(filename or "untitled.txt")
    suffix = path.suffix
    stem = path.name[: -len(suffix)] if suffix else path.name
    normalized_stem = _ENGLISH_VARIANT_FILENAME_SUFFIX.sub("", stem).strip() or "untitled"
    return f"{normalized_stem}{suffix}"


def _create_english_variant_copy(
    db: Session,
    *,
    project_id: UUID,
    file_record_id: UUID,
    current_user: User,
    converter: EnglishVariantConverter | None = None,
    required_source_target_language: str | None = None,
) -> EnglishVariantCopyResult:
    source_record = (
        db.query(FileRecord)
        .filter(
            FileRecord.id == file_record_id,
            FileRecord.project_id == project_id,
        )
        .with_for_update()
        .first()
    )
    if source_record is None:
        raise EnglishVariantCopyError("not_found", "项目文件不存在。")
    copy_spec = ENGLISH_VARIANT_COPY_SPECS.get(source_record.target_language or "")
    if source_record.source_language not in SUPPORTED_CHINESE_SOURCE_LANGUAGES or copy_spec is None:
        raise EnglishVariantCopyError(
            "unsupported_language_pair",
            "仅支持中文译英语（美国）与中文译英语（英国）文件之间的变体转换。",
        )
    if (
        required_source_target_language is not None
        and source_record.target_language != required_source_target_language
    ):
        raise EnglishVariantCopyError(
            "unsupported_language_pair",
            "此兼容接口仅支持将中文译英语（美国）的文件生成英式英语副本。",
        )
    if getattr(source_record, "active_operation", None):
        raise EnglishVariantCopyError(
            "active_operation",
            source_record.active_operation_message
            if hasattr(source_record, "active_operation_message")
            else "文件正在执行其他操作，请稍后重试。",
        )

    source_segments = (
        db.query(Segment)
        .filter(Segment.file_record_id == source_record.id)
        .order_by(*get_segment_ordering_for_file_record(source_record))
        .with_for_update()
        .all()
    )
    if not any((segment.target_text or "").strip() for segment in source_segments):
        raise EnglishVariantCopyError(
            "empty_translation",
            f"所选文件没有可转换的{copy_spec.source_variant_label}译文。",
        )

    next_filename = build_labeled_copy_filename(
        db,
        _normalize_variant_copy_source_filename(source_record.filename),
        project_id,
        copy_spec.filename_label,
    )
    duplicate, source_bytes = create_file_record_copy_shell(
        db,
        source_record,
        filename=next_filename,
        project_id=project_id,
        current_user=current_user,
        target_language=copy_spec.target_language,
        preserve_language_resources=False,
    )

    active_converter = converter or get_default_converter()
    processed_segments = 0
    changed_segments = 0
    replacement_count = 0
    for sequence_index, segment in enumerate(source_segments):
        original_target = segment.target_text or ""
        converted_target = ""
        converted_html: str | None = None
        segment_source = "none"
        if original_target.strip():
            processed_segments += 1
            converted_html, conversion = convert_html_fragment(
                segment.target_html,
                original_target,
                target_style=copy_spec.target_style,
                converter=active_converter,
            )
            converted_target = conversion.text
            replacement_count += conversion.replacement_count
            if converted_target != original_target:
                changed_segments += 1
            segment_source = ENGLISH_VARIANT_SOURCE

        raw_sequence_index = getattr(segment, "sequence_index", -1)
        stored_sequence_index = int(raw_sequence_index if raw_sequence_index is not None else -1)
        db.add(
            Segment(
                file_record_id=duplicate.id,
                sentence_id=segment.sentence_id,
                source_text=segment.source_text,
                source_hash=segment.source_hash or build_source_hash(segment.source_text),
                display_text=segment.display_text,
                source_html=segment.source_html,
                target_text=converted_target,
                target_html=converted_html,
                status="none",
                project_sync_disabled=False,
                project_sync_source_segment_id=None,
                project_sync_source_file_record_id=None,
                version=1,
                score=0.0,
                matched_source_text=None,
                matched_collection_name=None,
                matched_creator_name=None,
                matched_created_at=None,
                matched_updated_at=None,
                source=segment_source,
                source_word_count=segment.source_word_count,
                llm_provider=None,
                llm_model=None,
                last_modified_by_id=(current_user.id if original_target.strip() else None),
                block_type=segment.block_type,
                block_index=segment.block_index,
                row_index=segment.row_index,
                cell_index=segment.cell_index,
                sequence_index=(
                    stored_sequence_index if stored_sequence_index >= 0 else sequence_index
                ),
                segment_metadata=getattr(segment, "segment_metadata", "{}") or "{}",
            )
        )

    copy_file_record_source(db, source_record, duplicate, source_bytes)
    db.flush()
    return EnglishVariantCopyResult(
        file_record=duplicate,
        summary=EnglishVariantCopySummary(
            processed_segments=processed_segments,
            changed_segments=changed_segments,
            replacement_count=replacement_count,
        ),
    )


def create_english_variant_copy(
    db: Session,
    *,
    project_id: UUID,
    file_record_id: UUID,
    current_user: User,
    converter: EnglishVariantConverter | None = None,
) -> EnglishVariantCopyResult:
    """在英式英语与美式英语之间自动判断方向并创建项目副本。"""
    return _create_english_variant_copy(
        db,
        project_id=project_id,
        file_record_id=file_record_id,
        current_user=current_user,
        converter=converter,
    )


def create_british_english_copy(
    db: Session,
    *,
    project_id: UUID,
    file_record_id: UUID,
    current_user: User,
    converter: EnglishVariantConverter | None = None,
) -> EnglishVariantCopyResult:
    """兼容旧调用：仅允许从美式英语生成英式英语副本。"""
    return _create_english_variant_copy(
        db,
        project_id=project_id,
        file_record_id=file_record_id,
        current_user=current_user,
        converter=converter,
        required_source_target_language="en-US",
    )


__all__ = [
    "ENGLISH_VARIANT_SOURCE",
    "ENGLISH_VARIANT_COPY_SPECS",
    "EnglishVariantCopyError",
    "EnglishVariantCopyResult",
    "EnglishVariantCopySummary",
    "SUPPORTED_CHINESE_SOURCE_LANGUAGES",
    "create_british_english_copy",
    "create_english_variant_copy",
]
