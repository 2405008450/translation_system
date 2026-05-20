from __future__ import annotations

from typing import Literal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import TermBase, TermEntry, User
from app.services.normalizer import normalize_match_text, normalize_text

TermSaveAction = Literal["add", "replace", "skip"]


def normalize_term_entry_payload(source_text: str, target_text: str) -> dict:
    source_text = normalize_text(source_text)
    target_text = normalize_text(target_text)
    return {
        "source_text": source_text,
        "target_text": target_text,
        "source_normalized": normalize_match_text(source_text) or source_text,
    }


def serialize_term_entry_conflict(entry: TermEntry | None) -> dict | None:
    if entry is None:
        return None
    return {
        "id": str(entry.id),
        "term_base_id": str(entry.term_base_id),
        "source_text": entry.source_text,
        "target_text": entry.target_text,
        "source_language": entry.source_language,
        "target_language": entry.target_language,
    }


def _load_existing_terms(
    db: Session,
    term_base: TermBase,
    normalized_entries: list[dict],
) -> tuple[dict[str, TermEntry], dict[str, TermEntry]]:
    source_normalized_values = [
        item["source_normalized"]
        for item in normalized_entries
        if item.get("source_normalized")
    ]
    source_texts = [
        item["source_text"]
        for item in normalized_entries
        if item.get("source_text")
    ]
    if not source_normalized_values and not source_texts:
        return {}, {}

    existing_rows = (
        db.query(TermEntry)
        .filter(
            TermEntry.term_base_id == term_base.id,
            TermEntry.source_language == term_base.source_language,
            TermEntry.target_language == term_base.target_language,
            or_(
                TermEntry.source_normalized.in_(source_normalized_values or [""]),
                TermEntry.source_text.in_(source_texts or [""]),
            ),
        )
        .all()
    )

    existing_by_normalized: dict[str, TermEntry] = {}
    existing_by_source_text: dict[str, TermEntry] = {}
    for existing in existing_rows:
        if existing.source_normalized:
            existing_by_normalized.setdefault(existing.source_normalized, existing)
        existing_by_source_text.setdefault(existing.source_text, existing)

    return existing_by_normalized, existing_by_source_text


def build_term_entry_conflict_items(
    db: Session,
    term_base: TermBase,
    entries: list[dict],
) -> list[dict]:
    normalized_entries = [
        normalize_term_entry_payload(
            source_text=str(item.get("source_text") or ""),
            target_text=str(item.get("target_text") or ""),
        )
        for item in entries
    ]
    existing_by_normalized, existing_by_source_text = _load_existing_terms(
        db,
        term_base,
        normalized_entries,
    )

    items: list[dict] = []
    for index, item in enumerate(normalized_entries):
        existing = (
            existing_by_normalized.get(item["source_normalized"])
            or existing_by_source_text.get(item["source_text"])
        )
        items.append({
            "index": index,
            "source_text": item["source_text"],
            "target_text": item["target_text"],
            "source_normalized": item["source_normalized"],
            "has_conflict": existing is not None,
            "conflict": serialize_term_entry_conflict(existing),
        })

    return items


def save_term_entries_batch(
    db: Session,
    term_base: TermBase,
    entries: list[dict],
    current_user: User | None = None,
) -> dict:
    normalized_entries: list[dict] = []
    for index, item in enumerate(entries):
        normalized = normalize_term_entry_payload(
            source_text=str(item.get("source_text") or ""),
            target_text=str(item.get("target_text") or ""),
        )
        action = str(item.get("action") or "add")
        if action not in {"add", "replace", "skip"}:
            action = "add"
        normalized["index"] = index
        normalized["action"] = action
        normalized_entries.append(normalized)

    existing_by_normalized, existing_by_source_text = _load_existing_terms(
        db,
        term_base,
        normalized_entries,
    )

    created_count = 0
    updated_count = 0
    skipped_count = 0
    conflict_count = 0
    items: list[dict] = []
    seen_in_payload: set[str] = set()

    for item in normalized_entries:
        action = item["action"]
        source_text = item["source_text"]
        target_text = item["target_text"]
        source_normalized = item["source_normalized"]
        existing = (
            existing_by_normalized.get(source_normalized)
            or existing_by_source_text.get(source_text)
        )

        result_item = {
            "index": item["index"],
            "source_text": source_text,
            "target_text": target_text,
            "source_normalized": source_normalized,
            "action": action,
            "status": "skipped",
            "message": "",
            "conflict": serialize_term_entry_conflict(existing),
        }

        if action == "skip":
            skipped_count += 1
            result_item["message"] = "已按用户选择跳过。"
            items.append(result_item)
            continue

        if not source_text or not target_text:
            skipped_count += 1
            result_item["message"] = "原文术语或译文为空。"
            items.append(result_item)
            continue

        if source_normalized in seen_in_payload:
            skipped_count += 1
            result_item["message"] = "本次保存中存在重复原文术语。"
            items.append(result_item)
            continue

        if existing is not None and action == "add":
            conflict_count += 1
            result_item["status"] = "conflict"
            result_item["message"] = "术语库中已存在相同原文术语。"
            items.append(result_item)
            seen_in_payload.add(source_normalized)
            continue

        if existing is not None:
            existing.source_text = source_text
            existing.target_text = target_text
            existing.source_normalized = source_normalized
            existing.source_language = term_base.source_language
            existing.target_language = term_base.target_language
            updated_count += 1
            result_item["status"] = "updated"
            result_item["conflict"] = serialize_term_entry_conflict(existing)
            items.append(result_item)
            seen_in_payload.add(source_normalized)
            continue

        new_entry = TermEntry(
            term_base_id=term_base.id,
            source_text=source_text,
            target_text=target_text,
            source_normalized=source_normalized,
            source_language=term_base.source_language,
            target_language=term_base.target_language,
            creator_id=current_user.id if current_user else None,
        )
        db.add(new_entry)
        db.flush()
        existing_by_normalized[source_normalized] = new_entry
        existing_by_source_text[source_text] = new_entry
        created_count += 1
        result_item["status"] = "created"
        result_item["conflict"] = None
        items.append(result_item)
        seen_in_payload.add(source_normalized)

    db.commit()
    return {
        "created_count": created_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "conflict_count": conflict_count,
        "items": items,
    }
