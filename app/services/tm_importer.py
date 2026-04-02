from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import insert
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
    imported_rows: int
    skipped_empty_rows: int
    skipped_header_rows: int


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
    batch_rows: list[dict] = []
    imported_rows = 0
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

        batch_rows.append(
            {
                "source_text": source_text,
                "target_text": target_text,
                "source_hash": build_source_hash(source_text),
                "source_normalized": normalize_match_text(source_text) or normalize_text(source_text),
            }
        )

        if len(batch_rows) >= batch_size:
            db.execute(insert(TranslationMemory), batch_rows)
            db.commit()
            imported_rows += len(batch_rows)
            batch_rows.clear()

    if batch_rows:
        db.execute(insert(TranslationMemory), batch_rows)
        db.commit()
        imported_rows += len(batch_rows)

    workbook.close()
    return TMImportSummary(
        filename=filename,
        imported_rows=imported_rows,
        skipped_empty_rows=skipped_empty_rows,
        skipped_header_rows=skipped_header_rows,
    )


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
