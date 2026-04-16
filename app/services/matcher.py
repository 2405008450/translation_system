from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from time import perf_counter
from typing import TypeVar

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import TranslationMemory
from app.schemas import MatchResult
from app.services.normalizer import build_source_hash, normalize_match_text, normalize_text


EXACT_MATCH_BATCH_SIZE = 1000
FUZZY_MATCH_BATCH_SIZE = 200
FUZZY_CANDIDATE_LIMIT = 3
T = TypeVar("T")


@dataclass(frozen=True)
class PreparedSentence:
    source_sentence: str
    normalized_sentence: str
    match_text: str
    source_hash: str
    auxiliary_sentence: str = ""
    auxiliary_normalized: str = ""
    auxiliary_match_text: str = ""
    auxiliary_hash: str = ""


@dataclass(frozen=True)
class ResolvedMatch:
    status: str
    score: float
    matched_source_text: str | None
    target_text: str | None


@dataclass(frozen=True)
class MatchStats:
    total_input_sentences: int
    prepared_sentences: int
    unique_sentences: int
    exact_hits: int
    fuzzy_hits: int
    none_hits: int
    exact_phase_ms: float
    fuzzy_phase_ms: float
    total_match_ms: float
    fuzzy_candidates_evaluated: int


def match_sentences(
    db: Session,
    sentences: list[str],
    similarity_threshold: float,
    auxiliary_sentences: list[str] | None = None,
) -> list[MatchResult]:
    results, _ = match_sentences_with_stats(
        db,
        sentences,
        similarity_threshold,
        auxiliary_sentences=auxiliary_sentences,
    )
    return results


def match_sentences_with_stats(
    db: Session,
    sentences: list[str],
    similarity_threshold: float,
    auxiliary_sentences: list[str] | None = None,
) -> tuple[list[MatchResult], MatchStats]:
    total_started_at = perf_counter()
    prepared_sentences = _prepare_sentences(sentences, auxiliary_sentences=auxiliary_sentences)
    if not prepared_sentences:
        return [], MatchStats(
            total_input_sentences=len(sentences),
            prepared_sentences=0,
            unique_sentences=0,
            exact_hits=0,
            fuzzy_hits=0,
            none_hits=0,
            exact_phase_ms=0.0,
            fuzzy_phase_ms=0.0,
            total_match_ms=0.0,
            fuzzy_candidates_evaluated=0,
        )

    resolved_matches, stats = _resolve_matches(
        db=db,
        prepared_sentences=prepared_sentences,
        similarity_threshold=similarity_threshold,
        total_input_sentences=len(sentences),
    )

    results = [
        MatchResult(
            source_sentence=sentence.source_sentence,
            status=match.status,
            score=match.score,
            matched_source_text=match.matched_source_text,
            target_text=match.target_text,
        )
        for sentence, match in zip(prepared_sentences, resolved_matches, strict=False)
    ]

    total_match_ms = (perf_counter() - total_started_at) * 1000
    return results, MatchStats(
        total_input_sentences=stats.total_input_sentences,
        prepared_sentences=stats.prepared_sentences,
        unique_sentences=stats.unique_sentences,
        exact_hits=stats.exact_hits,
        fuzzy_hits=stats.fuzzy_hits,
        none_hits=stats.none_hits,
        exact_phase_ms=stats.exact_phase_ms,
        fuzzy_phase_ms=stats.fuzzy_phase_ms,
        total_match_ms=round(total_match_ms, 2),
        fuzzy_candidates_evaluated=stats.fuzzy_candidates_evaluated,
    )


def _prepare_sentences(
    sentences: list[str],
    auxiliary_sentences: list[str] | None = None,
) -> list[PreparedSentence]:
    prepared_sentences: list[PreparedSentence] = []
    normalized_auxiliary_sentences = list(auxiliary_sentences or [])
    if len(normalized_auxiliary_sentences) < len(sentences):
        normalized_auxiliary_sentences.extend([""] * (len(sentences) - len(normalized_auxiliary_sentences)))

    for index, sentence in enumerate(sentences):
        normalized_sentence = normalize_text(sentence)
        auxiliary_sentence = normalized_auxiliary_sentences[index]
        auxiliary_normalized = normalize_text(auxiliary_sentence)
        auxiliary_match_text = normalize_match_text(auxiliary_sentence) or auxiliary_normalized
        prepared_sentences.append(
            PreparedSentence(
                source_sentence=sentence,
                normalized_sentence=normalized_sentence,
                match_text=normalize_match_text(sentence) or normalized_sentence,
                source_hash=build_source_hash(sentence) if normalized_sentence else "",
                auxiliary_sentence=auxiliary_sentence,
                auxiliary_normalized=auxiliary_normalized,
                auxiliary_match_text=auxiliary_match_text,
                auxiliary_hash=build_source_hash(auxiliary_sentence) if auxiliary_normalized else "",
            )
        )

    return prepared_sentences


def _resolve_matches(
    db: Session,
    prepared_sentences: list[PreparedSentence],
    similarity_threshold: float,
    total_input_sentences: int,
) -> tuple[list[ResolvedMatch], MatchStats]:
    resolved_matches: list[ResolvedMatch | None] = [None] * len(prepared_sentences)
    matchable_items = [
        (index, sentence)
        for index, sentence in enumerate(prepared_sentences)
        if sentence.normalized_sentence
    ]

    exact_started_at = perf_counter()
    (
        exact_matches_by_hash,
        exact_matches_by_normalized,
        exact_matches_by_source_text,
    ) = _find_exact_matches(
        db,
        [sentence for _, sentence in matchable_items],
    )
    exact_phase_ms = (perf_counter() - exact_started_at) * 1000

    unresolved_items: list[tuple[int, PreparedSentence]] = []
    exact_hits = 0
    none_hits = 0
    for index, sentence in enumerate(prepared_sentences):
        if not sentence.normalized_sentence:
            resolved_matches[index] = ResolvedMatch(
                status="none",
                score=0.0,
                matched_source_text=None,
                target_text=None,
            )
            none_hits += 1
            continue

        exact_match = exact_matches_by_hash.get(sentence.auxiliary_hash)
        if exact_match is None:
            exact_match = exact_matches_by_normalized.get(sentence.auxiliary_normalized)
        if exact_match is None:
            exact_match = exact_matches_by_normalized.get(sentence.auxiliary_match_text)
        if exact_match is None:
            exact_match = exact_matches_by_source_text.get(sentence.auxiliary_normalized)
        if exact_match is None:
            exact_match = exact_matches_by_source_text.get(sentence.auxiliary_match_text)
        if exact_match is None:
            exact_match = exact_matches_by_hash.get(sentence.source_hash)
        if exact_match is None:
            exact_match = exact_matches_by_normalized.get(sentence.normalized_sentence)
        if exact_match is None:
            exact_match = exact_matches_by_normalized.get(sentence.match_text)
        if exact_match is None:
            exact_match = exact_matches_by_source_text.get(sentence.normalized_sentence)
        if exact_match is None:
            exact_match = exact_matches_by_source_text.get(sentence.match_text)

        if exact_match:
            resolved_matches[index] = ResolvedMatch(
                status="exact",
                score=1.0,
                matched_source_text=exact_match.source_text,
                target_text=exact_match.target_text,
            )
            exact_hits += 1
            continue

        unresolved_items.append((index, sentence))

    fuzzy_started_at = perf_counter()
    fuzzy_matches, fuzzy_candidates_evaluated = _find_fuzzy_matches(
        db=db,
        prepared_sentences=[sentence for _, sentence in unresolved_items],
        similarity_threshold=similarity_threshold,
    )
    fuzzy_phase_ms = (perf_counter() - fuzzy_started_at) * 1000

    fuzzy_hits = 0
    for offset, (index, _) in enumerate(unresolved_items):
        fuzzy_match = fuzzy_matches[offset]
        if fuzzy_match is not None:
            resolved_matches[index] = fuzzy_match
            fuzzy_hits += 1
            continue

        resolved_matches[index] = ResolvedMatch(
            status="none",
            score=0.0,
            matched_source_text=None,
            target_text=None,
        )
        none_hits += 1

    return [match for match in resolved_matches if match is not None], MatchStats(
        total_input_sentences=total_input_sentences,
        prepared_sentences=len(prepared_sentences),
        unique_sentences=len({sentence.match_text for sentence in prepared_sentences if sentence.match_text}),
        exact_hits=exact_hits,
        fuzzy_hits=fuzzy_hits,
        none_hits=none_hits,
        exact_phase_ms=round(exact_phase_ms, 2),
        fuzzy_phase_ms=round(fuzzy_phase_ms, 2),
        total_match_ms=0.0,
        fuzzy_candidates_evaluated=fuzzy_candidates_evaluated,
    )


def _find_exact_matches(
    db: Session,
    sentences: Iterable[PreparedSentence],
) -> tuple[
    dict[str, TranslationMemory],
    dict[str, TranslationMemory],
    dict[str, TranslationMemory],
]:
    sentences = list(sentences)
    source_hashes = [
        source_hash
        for sentence in sentences
        for source_hash in (sentence.auxiliary_hash, sentence.source_hash)
        if source_hash
    ]
    normalized_candidates = list(
        {
            sentence.normalized_sentence
            for sentence in sentences
            if sentence.normalized_sentence
        }
        | {
            sentence.auxiliary_normalized
            for sentence in sentences
            if sentence.auxiliary_normalized
        }
        | {sentence.match_text for sentence in sentences if sentence.match_text}
        | {sentence.auxiliary_match_text for sentence in sentences if sentence.auxiliary_match_text}
    )
    source_text_candidates = normalized_candidates

    if not source_hashes and not normalized_candidates and not source_text_candidates:
        return {}, {}, {}

    matches_by_hash: dict[str, TranslationMemory] = {}
    matches_by_normalized: dict[str, TranslationMemory] = {}
    matches_by_source_text: dict[str, TranslationMemory] = {}
    for chunk in _chunked(source_hashes, EXACT_MATCH_BATCH_SIZE):
        stmt = select(TranslationMemory).where(TranslationMemory.source_hash.in_(chunk))
        for match in db.execute(stmt).scalars():
            matches_by_hash.setdefault(match.source_hash, match)

    for chunk in _chunked(normalized_candidates, EXACT_MATCH_BATCH_SIZE):
        stmt = select(TranslationMemory).where(
            TranslationMemory.source_normalized.in_(chunk)
        )
        for match in db.execute(stmt).scalars():
            matches_by_normalized.setdefault(match.source_normalized, match)

    for chunk in _chunked(source_text_candidates, EXACT_MATCH_BATCH_SIZE):
        stmt = select(TranslationMemory).where(TranslationMemory.source_text.in_(chunk))
        for match in db.execute(stmt).scalars():
            matches_by_source_text.setdefault(match.source_text, match)

    return matches_by_hash, matches_by_normalized, matches_by_source_text


def _find_fuzzy_matches(
    db: Session,
    prepared_sentences: list[PreparedSentence],
    similarity_threshold: float,
) -> tuple[list[ResolvedMatch | None], int]:
    if not prepared_sentences:
        return [], 0

    matches: list[ResolvedMatch | None] = []
    total_candidates = 0
    for chunk in _chunked(prepared_sentences, FUZZY_MATCH_BATCH_SIZE):
        chunk_matches, chunk_candidates = _find_fuzzy_matches_chunk(
            db=db,
            prepared_sentences=chunk,
            similarity_threshold=similarity_threshold,
        )
        matches.extend(chunk_matches)
        total_candidates += chunk_candidates

    return matches, total_candidates


def _find_fuzzy_matches_chunk(
    db: Session,
    prepared_sentences: list[PreparedSentence],
    similarity_threshold: float,
) -> tuple[list[ResolvedMatch | None], int]:
    trigram_prefilter_threshold = _get_trigram_prefilter_threshold(similarity_threshold)
    params = {
        "candidate_limit": FUZZY_CANDIDATE_LIMIT,
        "trigram_limit": trigram_prefilter_threshold,
    }
    value_rows: list[str] = []

    for index, sentence in enumerate(prepared_sentences):
        source_param_name = f"query_source_{index}"
        params[source_param_name] = sentence.match_text
        value_rows.append(f"({index}, 'source', :{source_param_name})")
        if sentence.auxiliary_match_text and sentence.auxiliary_match_text != sentence.match_text:
            auxiliary_param_name = f"query_aux_{index}"
            params[auxiliary_param_name] = sentence.auxiliary_match_text
            value_rows.append(f"({index}, 'auxiliary', :{auxiliary_param_name})")

    stmt = text(
        f"""
        WITH input(query_index, query_kind, query_text) AS (
            VALUES {", ".join(value_rows)}
        )
        SELECT
            input.query_index,
            input.query_kind,
            input.query_text,
            matched.compare_text,
            matched.source_text,
            matched.target_text,
            matched.trigram_score
        FROM input
        LEFT JOIN LATERAL (
            SELECT
                tm.source_normalized AS compare_text,
                tm.source_text,
                tm.target_text,
                similarity(tm.source_normalized, input.query_text) AS trigram_score
            FROM translation_memory AS tm
            WHERE tm.source_normalized IS NOT NULL
              AND tm.source_normalized % input.query_text
            ORDER BY similarity(tm.source_normalized, input.query_text) DESC, tm.updated_at DESC
            LIMIT :candidate_limit
        ) AS matched ON TRUE
        ORDER BY input.query_index ASC
        """
    )

    db.execute(
        text(
            "SELECT set_config('pg_trgm.similarity_threshold', CAST(:trigram_limit AS text), true)"
        ),
        {"trigram_limit": trigram_prefilter_threshold},
    )
    rows = db.execute(stmt, params).mappings().all()

    grouped_candidates: dict[int, dict[tuple[str, str], dict]] = {}
    for row in rows:
        if row["source_text"] is None or row["target_text"] is None:
            continue
        query_index = int(row["query_index"])
        candidate_key = (row["source_text"], row["target_text"])
        candidate_entry = grouped_candidates.setdefault(query_index, {}).setdefault(
            candidate_key,
            {
                "compare_text": row["compare_text"],
                "source_text": row["source_text"],
                "target_text": row["target_text"],
                "source_trigram_score": 0.0,
                "auxiliary_trigram_score": 0.0,
            },
        )
        if row["query_kind"] == "auxiliary":
            candidate_entry["auxiliary_trigram_score"] = max(
                float(row["trigram_score"]),
                float(candidate_entry["auxiliary_trigram_score"]),
            )
        else:
            candidate_entry["source_trigram_score"] = max(
                float(row["trigram_score"]),
                float(candidate_entry["source_trigram_score"]),
            )

    matches: list[ResolvedMatch | None] = []
    for index, sentence in enumerate(prepared_sentences):
        best_candidate = _pick_best_fuzzy_candidate(
            sentence=sentence,
            candidates=list(grouped_candidates.get(index, {}).values()),
            similarity_threshold=similarity_threshold,
        )
        if best_candidate is None:
            matches.append(None)
            continue

        matches.append(
            ResolvedMatch(
                status="fuzzy",
                score=round(float(best_candidate["score"]), 4),
                matched_source_text=best_candidate["source_text"],
                target_text=best_candidate["target_text"],
            )
        )

    candidate_count = sum(len(candidates) for candidates in grouped_candidates.values())
    return matches, candidate_count


def _pick_best_fuzzy_candidate(
    sentence: PreparedSentence,
    candidates: list[dict],
    similarity_threshold: float,
) -> dict | None:
    best_candidate = None
    best_score = 0.0
    best_base_score = 0.0

    for candidate in candidates:
        compare_text = normalize_match_text(candidate["compare_text"]) or candidate["compare_text"]
        source_sequence_score = SequenceMatcher(None, sentence.match_text, compare_text).ratio()
        source_score = max(float(candidate["source_trigram_score"]), source_sequence_score)

        auxiliary_score = 0.0
        if sentence.auxiliary_match_text:
            auxiliary_sequence_score = SequenceMatcher(
                None,
                sentence.auxiliary_match_text,
                compare_text,
            ).ratio()
            auxiliary_score = max(
                float(candidate["auxiliary_trigram_score"]),
                auxiliary_sequence_score,
            )

        base_score = max(source_score, auxiliary_score)
        final_score = base_score
        if auxiliary_score > source_score:
            final_score += min(auxiliary_score - source_score, 0.05)

        if final_score > best_score or (
            final_score == best_score and base_score > best_base_score
        ):
            best_score = final_score
            best_base_score = base_score
            best_candidate = {
                "source_text": candidate["source_text"],
                "target_text": candidate["target_text"],
                "score": final_score,
            }

    if best_candidate and best_base_score >= similarity_threshold:
        return best_candidate

    return None


def _get_trigram_prefilter_threshold(similarity_threshold: float) -> float:
    return min(max(similarity_threshold, 0.01), 0.3)


def _chunked(items: list[T], chunk_size: int) -> list[list[T]]:
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]
