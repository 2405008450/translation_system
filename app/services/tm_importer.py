from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import TranslationMemory
from app.services.normalizer import build_source_hash, normalize_match_text, normalize_text


XLSX_EXTENSIONS = {".xlsx"}
HEADER_ALIASES = {
    ("zh-cn", "en-us"),
    ("source_text", "target_text"),
    ("中文", "英文"),
    ("中文原文", "英文译文"),
    ("原文", "译文"),
}


@dataclass
class TMImportSummary:
    filename: str
    created_rows: int
    updated_rows: int
    skipped_empty_rows: int
    skipped_header_rows: int

    @property
    def imported_rows(self) -> int:
        return self.created_rows + self.updated_rows


def import_tm_from_xlsx_upload(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    batch_size: int = 5000,
) -> TMImportSummary:
    workbook = load_workbook(BytesIO(raw_bytes), read_only=True, data_only=True)
    return _import_workbook(db=db, workbook=workbook, filename=filename, batch_size=batch_size)


def import_tm_from_xlsx_path(
    db: Session,
    xlsx_path: str | Path,
    batch_size: int = 5000,
) -> TMImportSummary:
    workbook = load_workbook(Path(xlsx_path), read_only=True, data_only=True)
    return _import_workbook(
        db=db,
        workbook=workbook,
        filename=Path(xlsx_path).name,
        batch_size=batch_size,
    )


def _import_workbook(db: Session, workbook, filename: str, batch_size: int) -> TMImportSummary:
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

        tm_row = _build_tm_row(source_text=source_text, target_text=target_text)
        batch_rows[tm_row["source_hash"]] = tm_row

        if len(batch_rows) >= batch_size:
            created_in_batch, updated_in_batch = _flush_tm_batch(
                db=db,
                batch_rows=list(batch_rows.values()),
            )
            created_rows += created_in_batch
            updated_rows += updated_in_batch
            batch_rows.clear()

    if batch_rows:
        created_in_batch, updated_in_batch = _flush_tm_batch(
            db=db,
            batch_rows=list(batch_rows.values()),
        )
        created_rows += created_in_batch
        updated_rows += updated_in_batch

    workbook.close()
    return TMImportSummary(
        filename=filename,
        created_rows=created_rows,
        updated_rows=updated_rows,
        skipped_empty_rows=skipped_empty_rows,
        skipped_header_rows=skipped_header_rows,
    )


def _build_tm_row(source_text: str, target_text: str) -> dict:
    return {
        "source_text": source_text,
        "target_text": target_text,
        "source_hash": build_source_hash(source_text),
        "source_normalized": normalize_match_text(source_text) or normalize_text(source_text),
    }


def _flush_tm_batch(db: Session, batch_rows: list[dict]) -> tuple[int, int]:
    if not batch_rows:
        return 0, 0

    source_hashes = [row["source_hash"] for row in batch_rows]
    source_texts = [row["source_text"] for row in batch_rows]
    existing_rows = (
        db.query(TranslationMemory)
        .filter(
            or_(
                TranslationMemory.source_hash.in_(source_hashes),
                TranslationMemory.source_text.in_(source_texts),
            )
        )
        .all()
    )

    existing_by_hash: dict[str, TranslationMemory] = {}
    existing_by_source_text: dict[str, TranslationMemory] = {}
    for existing in existing_rows:
        if existing.source_hash:
            existing_by_hash.setdefault(existing.source_hash, existing)
        existing_by_source_text.setdefault(existing.source_text, existing)

    created_rows = 0
    updated_rows = 0
    for row in batch_rows:
        existing = existing_by_hash.get(row["source_hash"]) or existing_by_source_text.get(
            row["source_text"]
        )
        if existing is None:
            db.add(TranslationMemory(**row))
            created_rows += 1
            continue

        existing.source_text = row["source_text"]
        existing.target_text = row["target_text"]
        existing.source_hash = row["source_hash"]
        existing.source_normalized = row["source_normalized"]
        existing_by_hash[row["source_hash"]] = existing
        existing_by_source_text[row["source_text"]] = existing
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
