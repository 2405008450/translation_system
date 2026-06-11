from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import UUID

from openpyxl import load_workbook
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import GlossaryEntry
from app.services.language_pairs import require_language_pair
from app.services.normalizer import normalize_match_text, normalize_text


XLSX_EXTENSIONS = {".xlsx"}
SOURCE_HEADER_ALIASES = {"source", "source_text", "source_term", "term", "原文", "词汇", "词条", "术语"}
TARGET_HEADER_ALIASES = {"target", "target_text", "target_term", "translation", "译文", "翻译"}
NOTE_HEADER_ALIASES = {"note", "notes", "comment", "comments", "context", "remark", "remarks", "备注", "说明", "补充解释"}


@dataclass
class GlossaryImportSummary:
    filename: str
    created_rows: int
    updated_rows: int
    skipped_empty_rows: int
    skipped_header_rows: int

    @property
    def imported_rows(self) -> int:
        return self.created_rows + self.updated_rows


def import_glossary_from_xlsx_upload(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    source_language: str,
    target_language: str,
    *,
    glossary_base_id: UUID,
    creator_id: UUID | None = None,
    batch_size: int = 5000,
    skip_header: bool = False,
) -> GlossaryImportSummary:
    workbook = load_workbook(BytesIO(raw_bytes), read_only=True, data_only=True)
    return _import_workbook(
        db=db,
        workbook=workbook,
        filename=filename,
        batch_size=batch_size,
        glossary_base_id=glossary_base_id,
        source_language=source_language,
        target_language=target_language,
        creator_id=creator_id,
        skip_header=skip_header,
    )


def import_glossary_from_xlsx_path(
    db: Session,
    xlsx_path: str | Path,
    source_language: str,
    target_language: str,
    *,
    glossary_base_id: UUID,
    creator_id: UUID | None = None,
    batch_size: int = 5000,
    skip_header: bool = False,
) -> GlossaryImportSummary:
    workbook = load_workbook(Path(xlsx_path), read_only=True, data_only=True)
    return _import_workbook(
        db=db,
        workbook=workbook,
        filename=Path(xlsx_path).name,
        batch_size=batch_size,
        glossary_base_id=glossary_base_id,
        source_language=source_language,
        target_language=target_language,
        creator_id=creator_id,
        skip_header=skip_header,
    )


def _import_workbook(
    db: Session,
    workbook,
    filename: str,
    batch_size: int,
    glossary_base_id: UUID,
    source_language: str,
    target_language: str,
    creator_id: UUID | None = None,
    skip_header: bool = False,
) -> GlossaryImportSummary:
    normalized_source_language, normalized_target_language = require_language_pair(
        source_language,
        target_language,
    )
    worksheet = workbook.active
    batch_rows: dict[str, dict] = {}
    created_rows = 0
    updated_rows = 0
    skipped_empty_rows = 0
    skipped_header_rows = 0
    column_indexes = (0, 1, 2)

    for row_index, row in enumerate(worksheet.iter_rows(values_only=True), start=1):
        if row_index == 1:
            detected = _detect_header_indexes(row)
            if detected is not None:
                column_indexes = detected
                skipped_header_rows += 1
                continue
            if skip_header:
                skipped_header_rows += 1
                continue

        source_index, target_index, note_index = column_indexes
        source_text = normalize_text(_cell_to_text(row, source_index))
        target_text = normalize_text(_cell_to_text(row, target_index))
        note = normalize_text(_cell_to_text(row, note_index)) if note_index >= 0 else ""

        if not source_text or not target_text:
            skipped_empty_rows += 1
            continue

        glossary_row = _build_glossary_row(
            source_text=source_text,
            target_text=target_text,
            note=note,
            glossary_base_id=glossary_base_id,
            source_language=normalized_source_language,
            target_language=normalized_target_language,
            creator_id=creator_id,
        )
        batch_rows[glossary_row["source_normalized"]] = glossary_row

        if len(batch_rows) >= batch_size:
            created_in_batch, updated_in_batch = _flush_glossary_batch(
                db=db,
                batch_rows=list(batch_rows.values()),
                glossary_base_id=glossary_base_id,
                source_language=normalized_source_language,
                target_language=normalized_target_language,
            )
            created_rows += created_in_batch
            updated_rows += updated_in_batch
            batch_rows.clear()

    if batch_rows:
        created_in_batch, updated_in_batch = _flush_glossary_batch(
            db=db,
            batch_rows=list(batch_rows.values()),
            glossary_base_id=glossary_base_id,
            source_language=normalized_source_language,
            target_language=normalized_target_language,
        )
        created_rows += created_in_batch
        updated_rows += updated_in_batch

    workbook.close()
    return GlossaryImportSummary(
        filename=filename,
        created_rows=created_rows,
        updated_rows=updated_rows,
        skipped_empty_rows=skipped_empty_rows,
        skipped_header_rows=skipped_header_rows,
    )


def _detect_header_indexes(row: tuple) -> tuple[int, int, int] | None:
    normalized_cells = [_normalize_header(_cell_to_text(row, index)) for index in range(len(row or ()))]
    source_index = _find_header_index(normalized_cells, SOURCE_HEADER_ALIASES)
    target_index = _find_header_index(normalized_cells, TARGET_HEADER_ALIASES)
    note_index = _find_header_index(normalized_cells, NOTE_HEADER_ALIASES)
    if source_index >= 0 and target_index >= 0:
        return source_index, target_index, note_index
    return None


def _find_header_index(cells: list[str], aliases: set[str]) -> int:
    for index, value in enumerate(cells):
        if value in aliases:
            return index
    return -1


def _normalize_header(value: str) -> str:
    return normalize_text(value).strip().lower().replace(" ", "_")


def _build_glossary_row(
    source_text: str,
    target_text: str,
    note: str,
    source_language: str,
    target_language: str,
    glossary_base_id: UUID,
    creator_id: UUID | None = None,
) -> dict:
    return {
        "glossary_base_id": glossary_base_id,
        "source_text": source_text,
        "target_text": target_text,
        "note": note or None,
        "source_normalized": normalize_match_text(source_text) or normalize_text(source_text),
        "source_language": source_language,
        "target_language": target_language,
        "creator_id": creator_id,
    }


def _flush_glossary_batch(
    db: Session,
    batch_rows: list[dict],
    source_language: str,
    target_language: str,
    glossary_base_id: UUID,
) -> tuple[int, int]:
    if not batch_rows:
        return 0, 0

    source_normalized_values = [row["source_normalized"] for row in batch_rows]
    source_texts = [row["source_text"] for row in batch_rows]
    existing_rows = (
        db.query(GlossaryEntry)
        .filter(
            GlossaryEntry.glossary_base_id == glossary_base_id,
            GlossaryEntry.source_language == source_language,
            GlossaryEntry.target_language == target_language,
            or_(
                GlossaryEntry.source_normalized.in_(source_normalized_values),
                GlossaryEntry.source_text.in_(source_texts),
            ),
        )
        .all()
    )

    existing_by_normalized: dict[str, GlossaryEntry] = {}
    existing_by_source_text: dict[str, GlossaryEntry] = {}
    for existing in existing_rows:
        if existing.source_normalized:
            existing_by_normalized.setdefault(existing.source_normalized, existing)
        existing_by_source_text.setdefault(existing.source_text, existing)

    created_rows = 0
    updated_rows = 0
    for row in batch_rows:
        existing = existing_by_normalized.get(row["source_normalized"]) or existing_by_source_text.get(
            row["source_text"]
        )
        if existing is None:
            db.add(GlossaryEntry(**row))
            created_rows += 1
            continue

        existing.source_text = row["source_text"]
        existing.target_text = row["target_text"]
        existing.note = row["note"]
        existing.source_normalized = row["source_normalized"]
        existing.source_language = row["source_language"]
        existing.target_language = row["target_language"]
        if row.get("creator_id"):
            existing.creator_id = row["creator_id"]
        updated_rows += 1

    db.commit()
    return created_rows, updated_rows


def _cell_to_text(row: tuple, index: int) -> str:
    if index < 0 or index >= len(row or ()):
        return ""
    value = row[index]
    return "" if value is None else str(value)
