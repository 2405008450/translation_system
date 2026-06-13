from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import MemoryEntry
from app.services.normalizer import build_source_hash, normalize_match_text, normalize_text


@dataclass(frozen=True)
class TMUpsertEntry:
    collection_id: UUID
    source_text: str
    target_text: str
    source_language: str
    target_language: str
    creator_id: UUID | None = None


@dataclass
class TMUpsertSummary:
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    sync_rows: list[tuple[UUID, str]] | None = None

    @property
    def total_written(self) -> int:
        return self.created_count + self.updated_count


def batch_upsert_tm_entries(db: Session, entries: list[TMUpsertEntry]) -> TMUpsertSummary:
    normalized_rows = _normalize_upsert_entries(entries)
    skipped_count = len(entries) - len(normalized_rows)
    if not normalized_rows:
        return TMUpsertSummary(skipped_count=skipped_count, sync_rows=[])

    existing_keys = _find_existing_keys(db, normalized_rows)
    created_count = sum(1 for row in normalized_rows if row["key"] not in existing_keys)
    updated_count = len(normalized_rows) - created_count

    if db.get_bind().dialect.name == "postgresql":
        sync_rows = _batch_upsert_tm_entries_postgres(db, normalized_rows)
    else:
        sync_rows = _batch_upsert_tm_entries_orm(db, normalized_rows)

    return TMUpsertSummary(
        created_count=created_count,
        updated_count=updated_count,
        skipped_count=skipped_count,
        sync_rows=sync_rows,
    )


def _normalize_upsert_entries(entries: list[TMUpsertEntry]) -> list[dict]:
    rows_by_key: dict[tuple[UUID, str, str, str], dict] = {}
    for entry in entries:
        source_text = normalize_text(entry.source_text)
        target_text = normalize_text(entry.target_text)
        source_language = (entry.source_language or "").strip()
        target_language = (entry.target_language or "").strip()
        if not source_text or not target_text or not source_language or not target_language:
            continue

        source_hash = build_source_hash(source_text)
        key = (entry.collection_id, source_hash, source_language, target_language)
        rows_by_key[key] = {
            "id": uuid4(),
            "collection_id": entry.collection_id,
            "source_text": source_text,
            "target_text": target_text,
            "source_hash": source_hash,
            "source_normalized": normalize_match_text(source_text) or source_text,
            "source_language": source_language,
            "target_language": target_language,
            "creator_id": entry.creator_id,
            "last_modified_by_id": entry.creator_id,
            "key": key,
        }
    return list(rows_by_key.values())


def _find_existing_keys(db: Session, rows: list[dict]) -> set[tuple[UUID, str, str, str]]:
    collection_ids = {row["collection_id"] for row in rows}
    source_hashes = {row["source_hash"] for row in rows}
    if not collection_ids or not source_hashes:
        return set()

    existing_rows = (
        db.query(
            MemoryEntry.collection_id,
            MemoryEntry.source_hash,
            MemoryEntry.source_language,
            MemoryEntry.target_language,
        )
        .filter(
            MemoryEntry.collection_id.in_(collection_ids),
            MemoryEntry.source_hash.in_(source_hashes),
        )
        .all()
    )
    return {
        (
            row.collection_id,
            row.source_hash,
            row.source_language,
            row.target_language,
        )
        for row in existing_rows
        if row.collection_id and row.source_hash and row.source_language and row.target_language
    }


def _batch_upsert_tm_entries_postgres(db: Session, rows: list[dict]) -> list[tuple[UUID, str]]:
    payload = [
        {
            "id": str(row["id"]),
            "collection_id": str(row["collection_id"]),
            "source_text": row["source_text"],
            "target_text": row["target_text"],
            "source_hash": row["source_hash"],
            "source_normalized": row["source_normalized"],
            "source_language": row["source_language"],
            "target_language": row["target_language"],
            "creator_id": str(row["creator_id"]) if row["creator_id"] else None,
            "last_modified_by_id": str(row["last_modified_by_id"]) if row["last_modified_by_id"] else None,
        }
        for row in rows
    ]
    db.execute(
        text(
            """
            INSERT INTO memory_entries (
                id,
                collection_id,
                source_text,
                target_text,
                source_hash,
                source_normalized,
                source_language,
                target_language,
                creator_id,
                last_modified_by_id
            )
            VALUES (
                CAST(:id AS uuid),
                CAST(:collection_id AS uuid),
                :source_text,
                :target_text,
                :source_hash,
                :source_normalized,
                :source_language,
                :target_language,
                CAST(:creator_id AS uuid),
                CAST(:last_modified_by_id AS uuid)
            )
            ON CONFLICT (collection_id, source_hash, source_language, target_language)
            DO UPDATE SET
                source_text = EXCLUDED.source_text,
                target_text = EXCLUDED.target_text,
                source_hash = EXCLUDED.source_hash,
                source_normalized = EXCLUDED.source_normalized,
                source_language = EXCLUDED.source_language,
                target_language = EXCLUDED.target_language,
                creator_id = COALESCE(memory_entries.creator_id, EXCLUDED.creator_id),
                last_modified_by_id = COALESCE(EXCLUDED.last_modified_by_id, memory_entries.last_modified_by_id),
                updated_at = NOW()
            """
        ),
        payload,
    )
    return _fetch_touched_tm_entries(db, rows)


def _fetch_touched_tm_entries(db: Session, rows: list[dict]) -> list[tuple[UUID, str]]:
    collection_ids = {row["collection_id"] for row in rows}
    source_hashes = {row["source_hash"] for row in rows}
    source_languages = {row["source_language"] for row in rows}
    target_languages = {row["target_language"] for row in rows}
    touched_entries = (
        db.query(MemoryEntry)
        .filter(
            MemoryEntry.collection_id.in_(collection_ids),
            MemoryEntry.source_hash.in_(source_hashes),
            MemoryEntry.source_language.in_(source_languages),
            MemoryEntry.target_language.in_(target_languages),
        )
        .all()
    )
    entries_by_key = {
        (
            entry.collection_id,
            entry.source_hash,
            entry.source_language,
            entry.target_language,
        ): entry
        for entry in touched_entries
    }
    return [
        (entry.id, entry.source_text)
        for key in (row["key"] for row in rows)
        if (entry := entries_by_key.get(key)) is not None and entry.id is not None and entry.source_text
    ]


def _batch_upsert_tm_entries_orm(db: Session, rows: list[dict]) -> list[tuple[UUID, str]]:
    existing_entries = (
        db.query(MemoryEntry)
        .filter(
            MemoryEntry.collection_id.in_({row["collection_id"] for row in rows}),
            MemoryEntry.source_hash.in_({row["source_hash"] for row in rows}),
        )
        .all()
    )
    existing_by_key = {
        (
            entry.collection_id,
            entry.source_hash,
            entry.source_language,
            entry.target_language,
        ): entry
        for entry in existing_entries
    }

    touched_entries: list[MemoryEntry] = []
    for row in rows:
        existing = existing_by_key.get(row["key"])
        if existing is not None:
            existing.source_text = row["source_text"]
            existing.target_text = row["target_text"]
            existing.source_hash = row["source_hash"]
            existing.source_normalized = row["source_normalized"]
            existing.collection_id = row["collection_id"]
            existing.source_language = row["source_language"]
            existing.target_language = row["target_language"]
            if existing.creator_id is None and row["creator_id"] is not None:
                existing.creator_id = row["creator_id"]
            if row["last_modified_by_id"] is not None:
                existing.last_modified_by_id = row["last_modified_by_id"]
            touched_entries.append(existing)
            continue

        memory_entry = MemoryEntry(
            collection_id=row["collection_id"],
            source_text=row["source_text"],
            target_text=row["target_text"],
            source_hash=row["source_hash"],
            source_normalized=row["source_normalized"],
            source_language=row["source_language"],
            target_language=row["target_language"],
            creator_id=row["creator_id"],
            last_modified_by_id=row["last_modified_by_id"],
        )
        db.add(memory_entry)
        touched_entries.append(memory_entry)

    db.flush()
    return [
        (entry.id, entry.source_text)
        for entry in touched_entries
        if entry.id is not None and entry.source_text
    ]
