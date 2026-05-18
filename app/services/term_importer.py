from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import UUID

from openpyxl import load_workbook
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import TermEntry
from app.services.language_pairs import require_language_pair
from app.services.normalizer import normalize_match_text, normalize_text


XLSX_EXTENSIONS = {".xlsx"}
HEADER_ALIASES = {
    ("source", "target"),
    ("source_term", "target_term"),
    ("source_text", "target_text"),
    ("term", "translation"),
    ("术语", "译文"),
    ("原文", "译文"),
    ("中文", "英文"),
}


@dataclass
class TermImportSummary:
    filename: str
    created_rows: int
    updated_rows: int
    skipped_empty_rows: int
    skipped_header_rows: int

    @property
    def imported_rows(self) -> int:
        return self.created_rows + self.updated_rows


def import_terms_from_xlsx_upload(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    source_language: str,
    target_language: str,
    batch_size: int = 5000,
    term_base_id: UUID | None = None,
) -> TermImportSummary:
    workbook = load_workbook(BytesIO(raw_bytes), read_only=True, data_only=True)
    return _import_workbook(
        db=db,
        workbook=workbook,
        filename=filename,
        batch_size=batch_size,
        term_base_id=term_base_id,
        source_language=source_language,
        target_language=target_language,
    )


def import_terms_from_xlsx_path(
    db: Session,
    xlsx_path: str | Path,
    source_language: str,
    target_language: str,
    batch_size: int = 5000,
    term_base_id: UUID | None = None,
) -> TermImportSummary:
    workbook = load_workbook(Path(xlsx_path), read_only=True, data_only=True)
    return _import_workbook(
        db=db,
        workbook=workbook,
        filename=Path(xlsx_path).name,
        batch_size=batch_size,
        term_base_id=term_base_id,
        source_language=source_language,
        target_language=target_language,
    )


def _import_workbook(
    db: Session,
    workbook,
    filename: str,
    batch_size: int,
    source_language: str,
    target_language: str,
    term_base_id: UUID | None = None,
) -> TermImportSummary:
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

    for row_index, row in enumerate(worksheet.iter_rows(values_only=True), start=1):
        source_text = normalize_text(_cell_to_text(row, 0))
        target_text = normalize_text(_cell_to_text(row, 1))

        if row_index == 1 and _looks_like_header(source_text, target_text):
            skipped_header_rows += 1
            continue

        if not source_text or not target_text:
            skipped_empty_rows += 1
            continue

        term_row = _build_term_row(
            source_text=source_text,
            target_text=target_text,
            term_base_id=term_base_id,
            source_language=normalized_source_language,
            target_language=normalized_target_language,
        )
        batch_rows[term_row["source_normalized"]] = term_row

        if len(batch_rows) >= batch_size:
            created_in_batch, updated_in_batch = _flush_term_batch(
                db=db,
                batch_rows=list(batch_rows.values()),
                term_base_id=term_base_id,
                source_language=normalized_source_language,
                target_language=normalized_target_language,
            )
            created_rows += created_in_batch
            updated_rows += updated_in_batch
            batch_rows.clear()

    if batch_rows:
        created_in_batch, updated_in_batch = _flush_term_batch(
            db=db,
            batch_rows=list(batch_rows.values()),
            term_base_id=term_base_id,
            source_language=normalized_source_language,
            target_language=normalized_target_language,
        )
        created_rows += created_in_batch
        updated_rows += updated_in_batch

    workbook.close()
    return TermImportSummary(
        filename=filename,
        created_rows=created_rows,
        updated_rows=updated_rows,
        skipped_empty_rows=skipped_empty_rows,
        skipped_header_rows=skipped_header_rows,
    )


def _build_term_row(
    source_text: str,
    target_text: str,
    source_language: str,
    target_language: str,
    term_base_id: UUID | None = None,
) -> dict:
    return {
        "term_base_id": term_base_id,
        "source_text": source_text,
        "target_text": target_text,
        "source_normalized": normalize_match_text(source_text) or normalize_text(source_text),
        "source_language": source_language,
        "target_language": target_language,
    }


def _flush_term_batch(
    db: Session,
    batch_rows: list[dict],
    source_language: str,
    target_language: str,
    term_base_id: UUID | None = None,
) -> tuple[int, int]:
    if not batch_rows:
        return 0, 0

    source_normalized_values = [row["source_normalized"] for row in batch_rows]
    source_texts = [row["source_text"] for row in batch_rows]
    existing_query = (
        db.query(TermEntry)
        .filter(
            or_(
                TermEntry.source_normalized.in_(source_normalized_values),
                TermEntry.source_text.in_(source_texts),
            ),
            TermEntry.source_language == source_language,
            TermEntry.target_language == target_language,
        )
    )
    if term_base_id is not None:
        existing_query = existing_query.filter(TermEntry.term_base_id == term_base_id)
    existing_rows = existing_query.all()

    existing_by_normalized: dict[str, TermEntry] = {}
    existing_by_source_text: dict[str, TermEntry] = {}
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
            db.add(TermEntry(**row))
            created_rows += 1
            continue

        existing.source_text = row["source_text"]
        existing.target_text = row["target_text"]
        existing.source_normalized = row["source_normalized"]
        existing.source_language = row["source_language"]
        existing.target_language = row["target_language"]
        updated_rows += 1

    db.commit()
    return created_rows, updated_rows


def _cell_to_text(row: tuple, index: int) -> str:
    if index >= len(row):
        return ""
    value = row[index]
    return "" if value is None else str(value)


def _looks_like_header(source_text: str, target_text: str) -> bool:
    if not source_text or not target_text:
        return False

    header_key = (source_text.lower(), target_text.lower())
    return header_key in HEADER_ALIASES
