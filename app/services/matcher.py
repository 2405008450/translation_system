from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from time import perf_counter
from typing import TypeVar
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import TranslationMemory
from app.schemas import MatchResult
from app.services.normalizer import build_source_hash, normalize_match_text, normalize_text
from app.services.tm_vector import (
    TM_EMBEDDING_VERSION,
    build_tm_embedding_literal,
    get_tm_vector_dimensions,
    is_tm_vector_ready,
    mark_tm_vector_unavailable,
)


EXACT_MATCH_BATCH_SIZE = 1000
FUZZY_MATCH_BATCH_SIZE = 200
FUZZY_CANDIDATE_LIMIT = 3
T = TypeVar("T")
logger = logging.getLogger(__name__)


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
    collection_ids: list[UUID] | None = None,
) -> list[MatchResult]:
    results, _ = match_sentences_with_stats(
        db,
        sentences,
        similarity_threshold,
        auxiliary_sentences=auxiliary_sentences,
        collection_ids=collection_ids,
    )
    return results


@dataclass(frozen=True)
class TMCandidate:
    source_text: str
    target_text: str
    score: float
    diff_html: str


def get_tm_candidates(
    db: Session,
    source_text: str,
    similarity_threshold: float,
    max_candidates: int = 5,
    collection_ids: list[UUID] | None = None,
) -> list[TMCandidate]:
    """获取单个句子的 TM 匹配候选项，返回满足阈值的前 N 条记录"""
    normalized = normalize_text(source_text)
    if not normalized:
        return []

    match_text = normalize_match_text(source_text) or normalized
    source_hash = build_source_hash(source_text)

    # 先检查精确匹配（hash 完全相同）
    normalized_collection_ids = _normalize_collection_ids(collection_ids)
    exact_stmt = select(TranslationMemory).where(TranslationMemory.source_hash == source_hash)
    exact_stmt = _apply_collection_filter(exact_stmt, normalized_collection_ids)
    exact_match = db.execute(exact_stmt).scalars().first()

    candidates: list[TMCandidate] = []
    seen_sources: set[str] = set()

    if exact_match:
        # 计算实际相似度，而不是直接返回 1.0
        actual_score = SequenceMatcher(None, source_text, exact_match.source_text).ratio()
        seen_sources.add(exact_match.source_text)
        candidates.append(TMCandidate(
            source_text=exact_match.source_text,
            target_text=exact_match.target_text,
            score=round(actual_score, 4),
            diff_html=_build_diff_html(source_text, exact_match.source_text),
        ))

    # 模糊匹配
    trigram_threshold = _get_trigram_prefilter_threshold(similarity_threshold)
    params = {
        "query_text": match_text,
        "candidate_limit": max_candidates * 2,
        "trigram_limit": trigram_threshold,
    }

    collection_filter_sql = ""
    if normalized_collection_ids:
        collection_param_names: list[str] = []
        for index, collection_id in enumerate(normalized_collection_ids):
            param_name = f"collection_id_{index}"
            params[param_name] = collection_id
            collection_param_names.append(f":{param_name}")
        collection_filter_sql = f" AND tm.collection_id IN ({', '.join(collection_param_names)})"

    stmt = text(f"""
        SELECT
            tm.source_text,
            tm.target_text,
            tm.source_normalized,
            similarity(tm.source_normalized, :query_text) AS trigram_score
        FROM translation_memory AS tm
        WHERE tm.source_normalized IS NOT NULL
          AND tm.source_normalized % :query_text
          {collection_filter_sql}
        ORDER BY similarity(tm.source_normalized, :query_text) DESC, tm.updated_at DESC
        LIMIT :candidate_limit
    """)

    db.execute(
        text("SELECT set_config('pg_trgm.similarity_threshold', CAST(:trigram_limit AS text), true)"),
        {"trigram_limit": trigram_threshold},
    )
    rows = db.execute(stmt, params).mappings().all()

    for row in rows:
        if row["source_text"] in seen_sources:
            continue

        compare_text = normalize_match_text(row["source_normalized"]) or row["source_normalized"]
        sequence_score = SequenceMatcher(None, match_text, compare_text).ratio()
        final_score = max(float(row["trigram_score"]), sequence_score)

        if final_score >= similarity_threshold:
            seen_sources.add(row["source_text"])
            candidates.append(TMCandidate(
                source_text=row["source_text"],
                target_text=row["target_text"],
                score=round(final_score, 4),
                diff_html=_build_diff_html(source_text, row["source_text"]),
            ))

    # 按分数排序，取前 N 条
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:max_candidates]


def _build_diff_html(source: str, matched: str) -> str:
    """生成修订格式的 HTML，标记原文和匹配文本的差异"""
    matcher = SequenceMatcher(None, source, matched)
    result_parts: list[str] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            result_parts.append(_escape_html(matched[j1:j2]))
        elif tag == "replace":
            result_parts.append(f'<del>{_escape_html(source[i1:i2])}</del>')
            result_parts.append(f'<ins>{_escape_html(matched[j1:j2])}</ins>')
        elif tag == "delete":
            result_parts.append(f'<del>{_escape_html(source[i1:i2])}</del>')
        elif tag == "insert":
            result_parts.append(f'<ins>{_escape_html(matched[j1:j2])}</ins>')

    return "".join(result_parts)


def _escape_html(text: str) -> str:
    """转义 HTML 特殊字符"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def match_sentences_with_stats(
    db: Session,
    sentences: list[str],
    similarity_threshold: float,
    auxiliary_sentences: list[str] | None = None,
    collection_ids: list[UUID] | None = None,
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
        collection_ids=collection_ids,
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
    collection_ids: list[UUID] | None = None,
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
        collection_ids=collection_ids,
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
        collection_ids=collection_ids,
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
    collection_ids: list[UUID] | None = None,
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
    normalized_collection_ids = _normalize_collection_ids(collection_ids)
    for chunk in _chunked(source_hashes, EXACT_MATCH_BATCH_SIZE):
        stmt = select(TranslationMemory).where(TranslationMemory.source_hash.in_(chunk))
        stmt = _apply_collection_filter(stmt, normalized_collection_ids)
        for match in db.execute(stmt).scalars():
            matches_by_hash.setdefault(match.source_hash, match)

    for chunk in _chunked(normalized_candidates, EXACT_MATCH_BATCH_SIZE):
        stmt = select(TranslationMemory).where(
            TranslationMemory.source_normalized.in_(chunk)
        )
        stmt = _apply_collection_filter(stmt, normalized_collection_ids)
        for match in db.execute(stmt).scalars():
            matches_by_normalized.setdefault(match.source_normalized, match)

    for chunk in _chunked(source_text_candidates, EXACT_MATCH_BATCH_SIZE):
        stmt = select(TranslationMemory).where(TranslationMemory.source_text.in_(chunk))
        stmt = _apply_collection_filter(stmt, normalized_collection_ids)
        for match in db.execute(stmt).scalars():
            matches_by_source_text.setdefault(match.source_text, match)

    return matches_by_hash, matches_by_normalized, matches_by_source_text


def _find_fuzzy_matches(
    db: Session,
    prepared_sentences: list[PreparedSentence],
    similarity_threshold: float,
    collection_ids: list[UUID] | None = None,
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
            collection_ids=collection_ids,
        )
        matches.extend(chunk_matches)
        total_candidates += chunk_candidates

    return matches, total_candidates


def _find_fuzzy_matches_chunk(
    db: Session,
    prepared_sentences: list[PreparedSentence],
    similarity_threshold: float,
    collection_ids: list[UUID] | None = None,
) -> tuple[list[ResolvedMatch | None], int]:
    trigram_prefilter_threshold = _get_trigram_prefilter_threshold(similarity_threshold)
    params = {
        "candidate_limit": FUZZY_CANDIDATE_LIMIT,
        "trigram_limit": trigram_prefilter_threshold,
    }
    value_rows: list[str] = []
    collection_filter_sql = ""
    normalized_collection_ids = _normalize_collection_ids(collection_ids)
    if normalized_collection_ids:
        collection_param_names: list[str] = []
        for index, collection_id in enumerate(normalized_collection_ids):
            param_name = f"collection_id_{index}"
            params[param_name] = collection_id
            collection_param_names.append(f":{param_name}")
        collection_filter_sql = (
            f" AND tm.collection_id IN ({', '.join(collection_param_names)})"
        )

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
              {collection_filter_sql}
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
                "source_vector_score": 0.0,
                "auxiliary_vector_score": 0.0,
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

    if is_tm_vector_ready(db):
        _merge_vector_candidates(
            db=db,
            prepared_sentences=prepared_sentences,
            grouped_candidates=grouped_candidates,
            collection_ids=collection_ids,
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


def _merge_vector_candidates(
    db: Session,
    prepared_sentences: list[PreparedSentence],
    grouped_candidates: dict[int, dict[tuple[str, str], dict]],
    collection_ids: list[UUID] | None = None,
) -> None:
    settings = get_settings()
    vector_candidate_limit = max(int(settings.tm_vector_candidate_limit), 1)
    vector_similarity_floor = min(max(float(settings.tm_vector_similarity_floor), 0.0), 1.0)
    vector_dimensions = get_tm_vector_dimensions()
    params = {
        "vector_candidate_limit": vector_candidate_limit,
        "embedding_version": TM_EMBEDDING_VERSION,
        "vector_similarity_floor": vector_similarity_floor,
    }
    value_rows: list[str] = []
    collection_filter_sql = ""
    normalized_collection_ids = _normalize_collection_ids(collection_ids)
    if normalized_collection_ids:
        collection_param_names: list[str] = []
        for index, collection_id in enumerate(normalized_collection_ids):
            param_name = f"vector_collection_id_{index}"
            params[param_name] = collection_id
            collection_param_names.append(f":{param_name}")
        collection_filter_sql = (
            f" AND tm.collection_id IN ({', '.join(collection_param_names)})"
        )

    for index, sentence in enumerate(prepared_sentences):
        source_vector_param_name = f"query_source_vector_{index}"
        params[source_vector_param_name] = build_tm_embedding_literal(
            sentence.match_text,
            dimensions=vector_dimensions,
        )
        value_rows.append(
            f"({index}, 'source', CAST(:{source_vector_param_name} AS vector({vector_dimensions})))"
        )
        if sentence.auxiliary_match_text and sentence.auxiliary_match_text != sentence.match_text:
            auxiliary_vector_param_name = f"query_aux_vector_{index}"
            params[auxiliary_vector_param_name] = build_tm_embedding_literal(
                sentence.auxiliary_match_text,
                dimensions=vector_dimensions,
            )
            value_rows.append(
                f"({index}, 'auxiliary', "
                f"CAST(:{auxiliary_vector_param_name} AS vector({vector_dimensions})))"
            )

    if not value_rows:
        return

    stmt = text(
        f"""
        WITH input(query_index, query_kind, query_vector) AS (
            VALUES {", ".join(value_rows)}
        )
        SELECT
            input.query_index,
            input.query_kind,
            matched.compare_text,
            matched.source_text,
            matched.target_text,
            matched.vector_score
        FROM input
        LEFT JOIN LATERAL (
            SELECT
                COALESCE(tm.source_normalized, tm.source_text) AS compare_text,
                tm.source_text,
                tm.target_text,
                1 - (tm.source_embedding <=> input.query_vector) AS vector_score
            FROM translation_memory AS tm
            WHERE tm.source_embedding IS NOT NULL
              AND tm.source_embedding_version = :embedding_version
              AND 1 - (tm.source_embedding <=> input.query_vector) >= :vector_similarity_floor
              {collection_filter_sql}
            ORDER BY tm.source_embedding <=> input.query_vector ASC, tm.updated_at DESC
            LIMIT :vector_candidate_limit
        ) AS matched ON TRUE
        ORDER BY input.query_index ASC
        """
    )

    try:
        rows = db.execute(stmt, params).mappings().all()
    except SQLAlchemyError as exc:
        mark_tm_vector_unavailable(db)
        logger.warning("tm vector recall skipped because query failed: %s", exc)
        return

    for row in rows:
        if row["source_text"] is None or row["target_text"] is None:
            continue
        query_index = int(row["query_index"])
        candidate_key = (row["source_text"], row["target_text"])
        candidate_entry = grouped_candidates.setdefault(query_index, {}).setdefault(
            candidate_key,
            {
                "compare_text": row["compare_text"] or row["source_text"],
                "source_text": row["source_text"],
                "target_text": row["target_text"],
                "source_trigram_score": 0.0,
                "auxiliary_trigram_score": 0.0,
                "source_vector_score": 0.0,
                "auxiliary_vector_score": 0.0,
            },
        )
        if row["query_kind"] == "auxiliary":
            candidate_entry["auxiliary_vector_score"] = max(
                float(row["vector_score"]),
                float(candidate_entry["auxiliary_vector_score"]),
            )
        else:
            candidate_entry["source_vector_score"] = max(
                float(row["vector_score"]),
                float(candidate_entry["source_vector_score"]),
            )


def _pick_best_fuzzy_candidate(
    sentence: PreparedSentence,
    candidates: list[dict],
    similarity_threshold: float,
) -> dict | None:
    best_candidate = None
    best_score = 0.0
    best_base_score = 0.0

    for candidate in candidates:
        compare_text_raw = candidate.get("compare_text") or candidate["source_text"]
        compare_text = normalize_match_text(compare_text_raw) or compare_text_raw
        source_sequence_score = SequenceMatcher(None, sentence.match_text, compare_text).ratio()
        source_score = _blend_match_score(
            lexical_score=max(float(candidate["source_trigram_score"]), source_sequence_score),
            vector_score=float(candidate.get("source_vector_score") or 0.0),
        )

        auxiliary_score = 0.0
        if sentence.auxiliary_match_text:
            auxiliary_sequence_score = SequenceMatcher(
                None,
                sentence.auxiliary_match_text,
                compare_text,
            ).ratio()
            auxiliary_score = _blend_match_score(
                lexical_score=max(
                    float(candidate["auxiliary_trigram_score"]),
                    auxiliary_sequence_score,
                ),
                vector_score=float(candidate.get("auxiliary_vector_score") or 0.0),
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


def _blend_match_score(lexical_score: float, vector_score: float) -> float:
    lexical = min(max(lexical_score, 0.0), 1.0)
    weight = _get_tm_vector_weight()
    if weight <= 0:
        return lexical

    vector = min(max(vector_score, 0.0), 1.0)
    if vector <= 0:
        return lexical

    return max(lexical, (lexical * (1 - weight)) + (vector * weight))


def _get_tm_vector_weight() -> float:
    settings = get_settings()
    if not getattr(settings, "tm_vector_enabled", True):
        return 0.0
    return min(max(float(getattr(settings, "tm_vector_weight", 0.0)), 0.0), 1.0)


def _normalize_collection_ids(collection_ids: list[UUID] | None) -> list[UUID] | None:
    if not collection_ids:
        return None
    return list(dict.fromkeys(collection_ids))


def _apply_collection_filter(stmt, collection_ids: list[UUID] | None):
    if not collection_ids:
        return stmt
    return stmt.where(TranslationMemory.collection_id.in_(collection_ids))


def _chunked(items: list[T], chunk_size: int) -> list[list[T]]:
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]
