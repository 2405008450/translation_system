from __future__ import annotations

import re
import csv
import sqlite3
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
from uuid import UUID

from openpyxl import load_workbook
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import MemoryEntry
from app.services.language_pairs import require_language_pair
from app.services.normalizer import build_source_hash, normalize_match_text, normalize_text
from app.services.tm_vector import sync_tm_embeddings


CSV_EXTENSIONS = {".csv"}
TMX_EXTENSIONS = {".tmx"}
XLS_EXTENSIONS = {".xls"}
XLSX_EXTENSIONS = {".xlsx"}
SDLTM_EXTENSIONS = {".sdltm"}
WORKBOOK_EXTENSIONS = XLSX_EXTENSIONS | XLS_EXTENSIONS
TM_IMPORT_EXTENSIONS = CSV_EXTENSIONS | TMX_EXTENSIONS | WORKBOOK_EXTENSIONS | SDLTM_EXTENSIONS
HEADER_ALIASES = {
    ("zh-cn", "en-us"),
    ("source", "target"),
    ("source_text", "target_text"),
    ("源文", "译文"),
    ("原文", "译文"),
    ("中文", "英文"),
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


@dataclass
class SDLTMMetadata:
    """Metadata extracted from SDLTM file."""
    name: str
    source_language: str
    target_language: str
    entry_count: int


def preview_sdltm_metadata(raw_bytes: bytes) -> SDLTMMetadata:
    """Extract metadata from SDLTM file without importing."""
    with tempfile.NamedTemporaryFile(suffix=".sdltm", delete=False) as tmp:
        tmp.write(raw_bytes)
        tmp_path = tmp.name

    try:
        conn = sqlite3.connect(tmp_path)
        cursor = conn.cursor()

        # Get TM metadata
        cursor.execute("""
            SELECT name, source_language, target_language
            FROM translation_memories
            LIMIT 1
        """)
        row = cursor.fetchone()

        if row:
            name, source_lang, target_lang = row
        else:
            name, source_lang, target_lang = "", "", ""

        # Get entry count
        cursor.execute("SELECT COUNT(*) FROM translation_units")
        entry_count = cursor.fetchone()[0]

        conn.close()

        return SDLTMMetadata(
            name=name or "",
            source_language=source_lang or "",
            target_language=target_lang or "",
            entry_count=entry_count,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def import_tm_from_upload(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    source_language: str,
    target_language: str,
    batch_size: int = 5000,
    collection_id: UUID | None = None,
    creator_id: UUID | None = None,
) -> TMImportSummary:
    extension = f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else ""
    if extension in CSV_EXTENSIONS:
        return import_tm_from_csv_upload(
            db=db,
            raw_bytes=raw_bytes,
            filename=filename,
            source_language=source_language,
            target_language=target_language,
            batch_size=batch_size,
            collection_id=collection_id,
            creator_id=creator_id,
        )
    if extension in TMX_EXTENSIONS:
        return import_tm_from_tmx_upload(
            db=db,
            raw_bytes=raw_bytes,
            filename=filename,
            source_language=source_language,
            target_language=target_language,
            batch_size=batch_size,
            collection_id=collection_id,
            creator_id=creator_id,
        )
    return import_tm_from_xlsx_upload(
        db=db,
        raw_bytes=raw_bytes,
        filename=filename,
        source_language=source_language,
        target_language=target_language,
        batch_size=batch_size,
        collection_id=collection_id,
        creator_id=creator_id,
    )


def import_tm_from_xlsx_upload(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    source_language: str,
    target_language: str,
    batch_size: int = 5000,
    collection_id: UUID | None = None,
    creator_id: UUID | None = None,
) -> TMImportSummary:
    extension = f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else ""
    if extension in XLS_EXTENSIONS:
        rows = _iter_xls_rows(raw_bytes)
        return _import_text_rows(
            db=db,
            rows=rows,
            filename=filename,
            batch_size=batch_size,
            collection_id=collection_id,
            creator_id=creator_id,
            source_language=source_language,
            target_language=target_language,
        )

    workbook = load_workbook(BytesIO(raw_bytes), read_only=True, data_only=True)
    return _import_workbook(
        db=db,
        workbook=workbook,
        filename=filename,
        batch_size=batch_size,
        collection_id=collection_id,
        creator_id=creator_id,
        source_language=source_language,
        target_language=target_language,
    )


def import_tm_from_xlsx_path(
    db: Session,
    xlsx_path: str | Path,
    source_language: str,
    target_language: str,
    batch_size: int = 5000,
    collection_id: UUID | None = None,
    creator_id: UUID | None = None,
) -> TMImportSummary:
    workbook = load_workbook(Path(xlsx_path), read_only=True, data_only=True)
    return _import_workbook(
        db=db,
        workbook=workbook,
        filename=Path(xlsx_path).name,
        batch_size=batch_size,
        collection_id=collection_id,
        creator_id=creator_id,
        source_language=source_language,
        target_language=target_language,
    )


def import_tm_from_csv_upload(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    source_language: str,
    target_language: str,
    batch_size: int = 5000,
    collection_id: UUID | None = None,
    creator_id: UUID | None = None,
) -> TMImportSummary:
    rows = _iter_csv_rows(raw_bytes)
    return _import_text_rows(
        db=db,
        rows=rows,
        filename=filename,
        batch_size=batch_size,
        collection_id=collection_id,
        creator_id=creator_id,
        source_language=source_language,
        target_language=target_language,
    )


def import_tm_from_tmx_upload(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    source_language: str,
    target_language: str,
    batch_size: int = 5000,
    collection_id: UUID | None = None,
    creator_id: UUID | None = None,
) -> TMImportSummary:
    normalized_source_language, normalized_target_language = require_language_pair(
        source_language,
        target_language,
    )
    rows = _iter_tmx_rows(raw_bytes, normalized_source_language, normalized_target_language)
    return _import_text_rows(
        db=db,
        rows=rows,
        filename=filename,
        batch_size=batch_size,
        collection_id=collection_id,
        creator_id=creator_id,
        source_language=normalized_source_language,
        target_language=normalized_target_language,
    )


def _iter_csv_rows(raw_bytes: bytes):
    text = _decode_csv_bytes(raw_bytes)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(text.splitlines(), dialect)
    for row in reader:
        yield tuple(row)


def _iter_xls_rows(raw_bytes: bytes):
    try:
        import xlrd
    except ModuleNotFoundError as exc:
        raise RuntimeError("导入 .xls 文件需要安装 xlrd 依赖。") from exc

    workbook = xlrd.open_workbook(file_contents=raw_bytes)
    sheet = workbook.sheet_by_index(0)
    for row_index in range(sheet.nrows):
        yield tuple(sheet.cell_value(row_index, column_index) for column_index in range(sheet.ncols))


def _iter_tmx_rows(raw_bytes: bytes, source_language: str, target_language: str):
    root = ET.fromstring(raw_bytes)
    for translation_unit in root.findall(".//{*}tu"):
        language_segments: list[tuple[str, str]] = []
        for tuv in translation_unit.findall("{*}tuv"):
            language = (
                tuv.attrib.get("{http://www.w3.org/XML/1998/namespace}lang")
                or tuv.attrib.get("lang")
                or tuv.attrib.get("xml:lang")
                or ""
            )
            segment = tuv.find("{*}seg")
            if segment is None:
                continue
            text = normalize_text("".join(segment.itertext()))
            if text:
                language_segments.append((language, text))

        source_text = _find_tmx_language_text(language_segments, source_language)
        target_text = _find_tmx_language_text(language_segments, target_language)
        if (not source_text or not target_text) and len(language_segments) >= 2:
            source_text = source_text or language_segments[0][1]
            target_text = target_text or language_segments[1][1]
        yield (source_text, target_text)


def _decode_csv_bytes(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _find_tmx_language_text(language_segments: list[tuple[str, str]], language: str) -> str:
    normalized_language = _normalize_language_tag(language)
    for candidate_language, text in language_segments:
        if _normalize_language_tag(candidate_language) == normalized_language:
            return text
    primary_language = normalized_language.split("-", 1)[0]
    for candidate_language, text in language_segments:
        if _normalize_language_tag(candidate_language).split("-", 1)[0] == primary_language:
            return text
    return ""


def _import_workbook(
    db: Session,
    workbook,
    filename: str,
    batch_size: int,
    source_language: str,
    target_language: str,
    collection_id: UUID | None = None,
    creator_id: UUID | None = None,
) -> TMImportSummary:
    worksheet = workbook.active
    try:
        return _import_text_rows(
            db=db,
            rows=worksheet.iter_rows(values_only=True),
            filename=filename,
            batch_size=batch_size,
            collection_id=collection_id,
            creator_id=creator_id,
            source_language=source_language,
            target_language=target_language,
        )
    finally:
        workbook.close()


def _import_text_rows(
    db: Session,
    rows,
    filename: str,
    batch_size: int,
    source_language: str,
    target_language: str,
    collection_id: UUID | None = None,
    creator_id: UUID | None = None,
) -> TMImportSummary:
    normalized_source_language, normalized_target_language = require_language_pair(
        source_language,
        target_language,
    )
    batch_rows: dict[str, dict] = {}
    created_rows = 0
    updated_rows = 0
    skipped_empty_rows = 0
    skipped_header_rows = 0

    for row_index, row in enumerate(rows, start=1):
        source_text = normalize_text(_cell_to_text(row, 0))
        target_text = normalize_text(_cell_to_text(row, 1))

        if row_index == 1 and _looks_like_header(source_text, target_text):
            skipped_header_rows += 1
            continue

        if not source_text or not target_text:
            skipped_empty_rows += 1
            continue

        tm_row = _build_tm_row(
            source_text=source_text,
            target_text=target_text,
            source_language=normalized_source_language,
            target_language=normalized_target_language,
            collection_id=collection_id,
            creator_id=creator_id,
        )
        batch_rows[tm_row["source_hash"]] = tm_row

        if len(batch_rows) >= batch_size:
            created_in_batch, updated_in_batch = _flush_tm_batch(
                db=db,
                batch_rows=list(batch_rows.values()),
                collection_id=collection_id,
                creator_id=creator_id,
                source_language=normalized_source_language,
                target_language=normalized_target_language,
            )
            created_rows += created_in_batch
            updated_rows += updated_in_batch
            batch_rows.clear()

    if batch_rows:
        created_in_batch, updated_in_batch = _flush_tm_batch(
            db=db,
            batch_rows=list(batch_rows.values()),
            collection_id=collection_id,
            creator_id=creator_id,
            source_language=normalized_source_language,
            target_language=normalized_target_language,
        )
        created_rows += created_in_batch
        updated_rows += updated_in_batch

    return TMImportSummary(
        filename=filename,
        created_rows=created_rows,
        updated_rows=updated_rows,
        skipped_empty_rows=skipped_empty_rows,
        skipped_header_rows=skipped_header_rows,
    )


def _build_tm_row(
    source_text: str,
    target_text: str,
    source_language: str,
    target_language: str,
    collection_id: UUID | None = None,
    creator_id: UUID | None = None,
) -> dict:
    return {
        "collection_id": collection_id,
        "source_text": source_text,
        "target_text": target_text,
        "source_hash": build_source_hash(source_text),
        "source_normalized": normalize_match_text(source_text) or normalize_text(source_text),
        "source_language": source_language,
        "target_language": target_language,
        "creator_id": creator_id,
    }


def _flush_tm_batch(
    db: Session,
    batch_rows: list[dict],
    source_language: str,
    target_language: str,
    collection_id: UUID | None = None,
    creator_id: UUID | None = None,
) -> tuple[int, int]:
    if not batch_rows:
        return 0, 0

    source_hashes = [row["source_hash"] for row in batch_rows]
    source_texts = [row["source_text"] for row in batch_rows]
    existing_query = (
        db.query(MemoryEntry)
        .filter(
            or_(
                MemoryEntry.source_hash.in_(source_hashes),
                MemoryEntry.source_text.in_(source_texts),
            ),
            MemoryEntry.source_language == source_language,
            MemoryEntry.target_language == target_language,
        )
    )
    if collection_id is None:
        existing_query = existing_query.filter(MemoryEntry.collection_id.is_(None))
    else:
        existing_query = existing_query.filter(MemoryEntry.collection_id == collection_id)
    existing_rows = existing_query.all()

    existing_by_hash: dict[str, MemoryEntry] = {}
    existing_by_source_text: dict[str, MemoryEntry] = {}
    for existing in existing_rows:
        if existing.source_hash:
            existing_by_hash.setdefault(existing.source_hash, existing)
        existing_by_source_text.setdefault(existing.source_text, existing)

    created_rows = 0
    updated_rows = 0
    sync_candidates: list[MemoryEntry] = []
    for row in batch_rows:
        existing = existing_by_hash.get(row["source_hash"]) or existing_by_source_text.get(
            row["source_text"]
        )
        if existing is None:
            tm_row = MemoryEntry(**row)
            db.add(tm_row)
            sync_candidates.append(tm_row)
            created_rows += 1
            continue

        existing.source_text = row["source_text"]
        existing.target_text = row["target_text"]
        existing.source_hash = row["source_hash"]
        existing.source_normalized = row["source_normalized"]
        existing.collection_id = row["collection_id"]
        existing.source_language = row["source_language"]
        existing.target_language = row["target_language"]
        existing_by_hash[row["source_hash"]] = existing
        existing_by_source_text[row["source_text"]] = existing
        sync_candidates.append(existing)
        updated_rows += 1

    db.flush()
    sync_rows = list(
        {
            row.id: row.source_text
            for row in sync_candidates
            if row.id is not None and row.source_text
        }.items()
    )
    db.commit()
    sync_tm_embeddings(db, sync_rows)
    return created_rows, updated_rows


def _cell_to_text(row: tuple, index: int) -> str:
    if index >= len(row):
        return ""
    value = row[index]
    return "" if value is None else str(value)


def _normalize_language_tag(language: str) -> str:
    return (language or "").strip().lower().replace("_", "-")


def _looks_like_header(source_text: str, target_text: str) -> bool:
    if not source_text or not target_text:
        return False

    header_key = (source_text.lower(), target_text.lower())
    return header_key in HEADER_ALIASES


# ============================================================================
# SDLTM Import Functions
# ============================================================================

def import_tm_from_sdltm_upload(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    source_language: str,
    target_language: str,
    batch_size: int = 5000,
    collection_id: UUID | None = None,
    creator_id: UUID | None = None,
) -> TMImportSummary:
    """Import translation memory from an uploaded SDLTM file."""
    with tempfile.NamedTemporaryFile(suffix=".sdltm", delete=False) as tmp:
        tmp.write(raw_bytes)
        tmp_path = tmp.name

    try:
        return _import_sdltm(
            db=db,
            sdltm_path=tmp_path,
            filename=filename,
            batch_size=batch_size,
            collection_id=collection_id,
            creator_id=creator_id,
            source_language=source_language,
            target_language=target_language,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def import_tm_from_sdltm_path(
    db: Session,
    sdltm_path: str | Path,
    source_language: str,
    target_language: str,
    batch_size: int = 5000,
    collection_id: UUID | None = None,
    creator_id: UUID | None = None,
) -> TMImportSummary:
    """Import translation memory from an SDLTM file path."""
    return _import_sdltm(
        db=db,
        sdltm_path=str(sdltm_path),
        filename=Path(sdltm_path).name,
        batch_size=batch_size,
        collection_id=collection_id,
        creator_id=creator_id,
        source_language=source_language,
        target_language=target_language,
    )


def _import_sdltm(
    db: Session,
    sdltm_path: str,
    filename: str,
    batch_size: int,
    source_language: str,
    target_language: str,
    collection_id: UUID | None = None,
    creator_id: UUID | None = None,
) -> TMImportSummary:
    """Core SDLTM import logic."""
    normalized_source_language, normalized_target_language = require_language_pair(
        source_language,
        target_language,
    )

    batch_rows: dict[str, dict] = {}
    created_rows = 0
    updated_rows = 0
    skipped_empty_rows = 0

    conn = sqlite3.connect(sdltm_path)
    try:
        cursor = conn.cursor()

        # SDLTM stores source/target as XML in translation_units table
        query = """
            SELECT
                id,
                source_segment,
                target_segment
            FROM translation_units
            WHERE source_segment IS NOT NULL AND target_segment IS NOT NULL
        """

        cursor.execute(query)

        for row in cursor.fetchall():
            _, raw_source, raw_target = row

            # Extract text from SDLTM XML format
            source_text = normalize_text(_extract_sdltm_text(raw_source))
            target_text = normalize_text(_extract_sdltm_text(raw_target))

            if not source_text or not target_text:
                skipped_empty_rows += 1
                continue

            tm_row = _build_tm_row(
                source_text=source_text,
                target_text=target_text,
                source_language=normalized_source_language,
                target_language=normalized_target_language,
                collection_id=collection_id,
                creator_id=creator_id,
            )
            batch_rows[tm_row["source_hash"]] = tm_row

            if len(batch_rows) >= batch_size:
                created_in_batch, updated_in_batch = _flush_tm_batch(
                    db=db,
                    batch_rows=list(batch_rows.values()),
                    collection_id=collection_id,
                    creator_id=creator_id,
                    source_language=normalized_source_language,
                    target_language=normalized_target_language,
                )
                created_rows += created_in_batch
                updated_rows += updated_in_batch
                batch_rows.clear()

        if batch_rows:
            created_in_batch, updated_in_batch = _flush_tm_batch(
                db=db,
                batch_rows=list(batch_rows.values()),
                collection_id=collection_id,
                creator_id=creator_id,
                source_language=normalized_source_language,
                target_language=normalized_target_language,
            )
            created_rows += created_in_batch
            updated_rows += updated_in_batch

    finally:
        conn.close()

    return TMImportSummary(
        filename=filename,
        created_rows=created_rows,
        updated_rows=updated_rows,
        skipped_empty_rows=skipped_empty_rows,
        skipped_header_rows=0,
    )


def _extract_sdltm_text(xml_content: str) -> str:
    """Extract plain text from SDLTM XML segment format.
    
    SDLTM stores segments as XML like:
    <Segment><Elements><Text><Value>actual text</Value></Text></Elements>...</Segment>
    """
    if not xml_content:
        return ""
    
    # Extract all text values from <Value> tags
    values = re.findall(r"<Value>(.*?)</Value>", xml_content, re.DOTALL)
    text = "".join(values)
    
    # Clean up any remaining XML entities
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    text = text.replace("&quot;", '"').replace("&apos;", "'")
    
    return text.strip()
