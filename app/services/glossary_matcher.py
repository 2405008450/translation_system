from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import GlossaryEntry
from app.services.normalizer import normalize_match_text, normalize_text


MAX_GLOSSARY_MATCHES_PER_SEGMENT = 8
MAX_GLOSSARY_NOTE_CHARS = 240
CJK_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
WORDISH_RE = re.compile(r"[a-z0-9_]", re.IGNORECASE)


@dataclass(frozen=True)
class GlossaryMatch:
    glossary_base_id: UUID
    glossary_base_name: str
    source_text: str
    target_text: str
    note: str = ""

    def as_prompt_payload(self) -> dict[str, str]:
        payload = {
            "source_text": self.source_text,
            "target_text": self.target_text,
        }
        if self.note:
            payload["note"] = self.note
        return payload


@dataclass(frozen=True)
class _PreparedGlossaryEntry:
    entry: GlossaryEntry
    base_name: str
    base_priority: int
    source_key: str
    source_key_lower: str


def build_glossary_matches_by_text(
    db: Session,
    source_texts: list[str],
    glossary_base_ids: list[UUID],
    *,
    source_language: str | None = None,
    target_language: str | None = None,
    max_matches_per_segment: int = MAX_GLOSSARY_MATCHES_PER_SEGMENT,
) -> dict[str, list[GlossaryMatch]]:
    unique_source_texts = [text for text in dict.fromkeys(source_texts) if normalize_text(text)]
    normalized_base_ids = list(dict.fromkeys(glossary_base_ids))
    if not unique_source_texts or not normalized_base_ids:
        return {}

    entries = _load_prepared_entries(
        db,
        normalized_base_ids,
        source_language=source_language,
        target_language=target_language,
    )
    if not entries:
        return {}

    result: dict[str, list[GlossaryMatch]] = {}
    safe_limit = max(1, min(int(max_matches_per_segment or MAX_GLOSSARY_MATCHES_PER_SEGMENT), 20))
    for source_text in unique_source_texts:
        matches = _match_text(source_text, entries, safe_limit)
        if matches:
            result[source_text] = matches
    return result


def _load_prepared_entries(
    db: Session,
    glossary_base_ids: list[UUID],
    *,
    source_language: str | None,
    target_language: str | None,
) -> list[_PreparedGlossaryEntry]:
    query = (
        db.query(GlossaryEntry)
        .filter(GlossaryEntry.glossary_base_id.in_(glossary_base_ids))
    )
    if source_language:
        query = query.filter(GlossaryEntry.source_language == source_language)
    if target_language:
        query = query.filter(GlossaryEntry.target_language == target_language)

    priority_by_base_id = {base_id: index for index, base_id in enumerate(glossary_base_ids)}
    entries = query.all()
    prepared: list[_PreparedGlossaryEntry] = []
    seen_keys: set[tuple[UUID, str]] = set()
    for entry in entries:
        source_text = normalize_text(entry.source_text)
        target_text = normalize_text(entry.target_text)
        if not source_text or not target_text:
            continue
        source_key = entry.source_normalized or normalize_match_text(source_text) or source_text
        source_key = normalize_text(source_key)
        if not source_key:
            continue
        dedupe_key = (entry.glossary_base_id, source_key.casefold())
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        prepared.append(_PreparedGlossaryEntry(
            entry=entry,
            base_name=entry.glossary_base.name if entry.glossary_base else "",
            base_priority=priority_by_base_id.get(entry.glossary_base_id, len(priority_by_base_id)),
            source_key=source_key,
            source_key_lower=source_key.casefold(),
        ))

    return sorted(
        prepared,
        key=lambda item: (
            -len(item.source_key),
            item.base_priority,
            item.entry.updated_at,
        ),
    )


def _match_text(
    source_text: str,
    entries: list[_PreparedGlossaryEntry],
    max_matches: int,
) -> list[GlossaryMatch]:
    normalized_text = normalize_match_text(source_text) or normalize_text(source_text)
    normalized_text_lower = normalized_text.casefold()
    if not normalized_text_lower:
        return []

    occupied_ranges: list[tuple[int, int]] = []
    matches: list[GlossaryMatch] = []
    matched_source_keys: set[str] = set()

    for item in entries:
        if len(matches) >= max_matches:
            break
        if item.source_key_lower in matched_source_keys:
            continue
        found_range = _find_match_range(normalized_text_lower, item.source_key_lower)
        if found_range is None:
            continue
        if _overlaps(found_range, occupied_ranges):
            continue
        occupied_ranges.append(found_range)
        matched_source_keys.add(item.source_key_lower)
        matches.append(GlossaryMatch(
            glossary_base_id=item.entry.glossary_base_id,
            glossary_base_name=item.base_name,
            source_text=item.entry.source_text,
            target_text=item.entry.target_text,
            note=_truncate_note(item.entry.note or ""),
        ))

    return matches


def _find_match_range(text_lower: str, term_lower: str) -> tuple[int, int] | None:
    if not term_lower:
        return None
    if _needs_word_boundary(term_lower):
        pattern = re.compile(rf"(?<![a-z0-9_]){re.escape(term_lower)}(?![a-z0-9_])", re.IGNORECASE)
        match = pattern.search(text_lower)
        return match.span() if match else None

    position = text_lower.find(term_lower)
    if position < 0:
        return None
    return position, position + len(term_lower)


def _needs_word_boundary(term_lower: str) -> bool:
    return bool(WORDISH_RE.search(term_lower)) and not CJK_RE.search(term_lower)


def _overlaps(candidate: tuple[int, int], occupied_ranges: list[tuple[int, int]]) -> bool:
    start, end = candidate
    return any(not (end <= occupied_start or start >= occupied_end) for occupied_start, occupied_end in occupied_ranges)


def _truncate_note(note: str) -> str:
    normalized = normalize_text(note)
    if len(normalized) <= MAX_GLOSSARY_NOTE_CHARS:
        return normalized
    return normalized[:MAX_GLOSSARY_NOTE_CHARS].rstrip() + "..."
