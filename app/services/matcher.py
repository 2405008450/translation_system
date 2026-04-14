from dataclasses import dataclass, field
from difflib import SequenceMatcher
from collections.abc import Iterable
from time import perf_counter

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import TranslationMemory
from app.schemas import MatchResult
from app.services.normalizer import build_source_hash, normalize_match_text, normalize_text


EXACT_MATCH_BATCH_SIZE = 1000
FUZZY_MATCH_BATCH_SIZE = 200
FUZZY_CANDIDATE_LIMIT = 10  # 增加候选数量以获取更多匹配
MAX_FUZZY_RESULTS = 5  # 最多返回5条模糊匹配结果


@dataclass(frozen=True)
class PreparedSentence:
    source_sentence: str
    normalized_sentence: str
    match_text: str
    source_hash: str


@dataclass(frozen=True)
class ResolvedMatch:
    status: str
    score: float
    matched_source_text: str | None
    target_text: str | None
    fuzzy_candidates: tuple = field(default_factory=tuple)  # 所有超过阈值的模糊匹配候选


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
) -> list[MatchResult]:
    results, _ = match_sentences_with_stats(db, sentences, similarity_threshold)
    return results


def match_sentences_with_stats(
    db: Session,
    sentences: list[str],
    similarity_threshold: float,
) -> tuple[list[MatchResult], MatchStats]:
    total_started_at = perf_counter()
    prepared_sentences = _prepare_sentences(sentences)
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

    unique_sentences = _deduplicate_sentences(prepared_sentences)
    resolved_matches, stats = _resolve_matches(
        db=db,
        unique_sentences=unique_sentences,
        similarity_threshold=similarity_threshold,
        total_input_sentences=len(sentences),
        prepared_sentence_count=len(prepared_sentences),
    )

    results = [
        MatchResult(
            source_sentence=sentence.source_sentence,
            status=resolved_matches[sentence.match_text].status,
            score=resolved_matches[sentence.match_text].score,
            matched_source_text=resolved_matches[sentence.match_text].matched_source_text,
            target_text=resolved_matches[sentence.match_text].target_text,
            fuzzy_candidates=[
                {
                    "source_text": c["source_text"],
                    "target_text": c["target_text"],
                    "score": round(float(c["score"]), 4),
                }
                for c in (resolved_matches[sentence.match_text].fuzzy_candidates or ())
            ],
        )
        for sentence in prepared_sentences
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


def _prepare_sentences(sentences: list[str]) -> list[PreparedSentence]:
    prepared_sentences: list[PreparedSentence] = []

    for sentence in sentences:
        normalized_sentence = normalize_text(sentence)
        if not normalized_sentence:
            continue

        prepared_sentences.append(
            PreparedSentence(
                source_sentence=sentence,
                normalized_sentence=normalized_sentence,
                match_text=normalize_match_text(sentence) or normalized_sentence,
                source_hash=build_source_hash(sentence),
            )
        )

    return prepared_sentences


def _deduplicate_sentences(
    prepared_sentences: list[PreparedSentence],
) -> dict[str, PreparedSentence]:
    unique_sentences: dict[str, PreparedSentence] = {}

    for sentence in prepared_sentences:
        unique_sentences.setdefault(sentence.match_text, sentence)

    return unique_sentences


def _resolve_matches(
    db: Session,
    unique_sentences: dict[str, PreparedSentence],
    similarity_threshold: float,
    total_input_sentences: int,
    prepared_sentence_count: int,
) -> tuple[dict[str, ResolvedMatch], MatchStats]:
    resolved_matches: dict[str, ResolvedMatch] = {}

    exact_started_at = perf_counter()
    (
        exact_matches_by_hash,
        exact_matches_by_normalized,
        exact_matches_by_source_text,
    ) = _find_exact_matches(
        db,
        unique_sentences.values(),
    )
    exact_phase_ms = (perf_counter() - exact_started_at) * 1000

    unresolved_sentences: dict[str, PreparedSentence] = {}
    exact_hits = 0
    for match_text, sentence in unique_sentences.items():
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
            resolved_matches[match_text] = ResolvedMatch(
                status="exact",
                score=1.0,
                matched_source_text=exact_match.source_text,
                target_text=exact_match.target_text,
            )
            exact_hits += 1
            continue

        unresolved_sentences[match_text] = sentence

    fuzzy_started_at = perf_counter()
    fuzzy_matches, fuzzy_candidates_evaluated = _find_fuzzy_matches(
        db,
        [sentence.match_text for sentence in unresolved_sentences.values()],
        similarity_threshold,
    )
    fuzzy_phase_ms = (perf_counter() - fuzzy_started_at) * 1000

    fuzzy_hits = 0
    none_hits = 0
    for match_text, sentence in unresolved_sentences.items():
        fuzzy_match = fuzzy_matches.get(sentence.match_text)
        if fuzzy_match:
            best = fuzzy_match["best"]
            all_candidates = fuzzy_match["all_candidates"]
            resolved_matches[match_text] = ResolvedMatch(
                status="fuzzy",
                score=round(float(best["score"]), 4),
                matched_source_text=best["source_text"],
                target_text=best["target_text"],
                fuzzy_candidates=tuple(all_candidates),
            )
            fuzzy_hits += 1
            continue

        resolved_matches[match_text] = ResolvedMatch(
            status="none",
            score=0.0,
            matched_source_text=None,
            target_text=None,
            fuzzy_candidates=(),
        )
        none_hits += 1

    return resolved_matches, MatchStats(
        total_input_sentences=total_input_sentences,
        prepared_sentences=prepared_sentence_count,
        unique_sentences=len(unique_sentences),
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
    source_hashes = [sentence.source_hash for sentence in sentences]
    normalized_candidates = list(
        {
            sentence.normalized_sentence
            for sentence in sentences
            if sentence.normalized_sentence
        }
        | {sentence.match_text for sentence in sentences if sentence.match_text}
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
    normalized_sentences: list[str],
    similarity_threshold: float,
):
    if not normalized_sentences:
        return {}, 0

    matches = {}
    total_candidates = 0
    for chunk in _chunked(normalized_sentences, FUZZY_MATCH_BATCH_SIZE):
        chunk_matches, chunk_candidates = _find_fuzzy_matches_chunk(
            db=db,
            normalized_sentences=chunk,
            similarity_threshold=similarity_threshold,
        )
        matches.update(chunk_matches)
        total_candidates += chunk_candidates

    return matches, total_candidates


def _find_fuzzy_matches_chunk(
    db: Session,
    normalized_sentences: list[str],
    similarity_threshold: float,
):
    trigram_prefilter_threshold = _get_trigram_prefilter_threshold(similarity_threshold)
    params = {
        "candidate_limit": FUZZY_CANDIDATE_LIMIT,
        "trigram_limit": trigram_prefilter_threshold,
    }
    value_rows: list[str] = []

    for index, sentence in enumerate(normalized_sentences):
        param_name = f"query_{index}"
        params[param_name] = sentence
        value_rows.append(f"(:{param_name})")

    stmt = text(
        f"""
        WITH input(query_text) AS (
            VALUES {", ".join(value_rows)}
        )
        SELECT
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
        """
    )

    db.execute(
        text(
            "SELECT set_config('pg_trgm.similarity_threshold', CAST(:trigram_limit AS text), true)"
        ),
        {"trigram_limit": trigram_prefilter_threshold},
    )
    rows = db.execute(stmt, params).mappings().all()

    grouped_candidates: dict[str, list[dict]] = {}
    for row in rows:
        if row["source_text"] is None or row["target_text"] is None:
            continue
        grouped_candidates.setdefault(row["query_text"], []).append(dict(row))

    matches = {}
    for query_text, candidates in grouped_candidates.items():
        best_candidate, all_candidates = _pick_best_fuzzy_candidate(
            query_text=query_text,
            candidates=candidates,
            similarity_threshold=similarity_threshold,
        )
        if best_candidate:
            matches[query_text] = {
                "best": best_candidate,
                "all_candidates": all_candidates,
            }

    candidate_count = sum(len(candidates) for candidates in grouped_candidates.values())
    return matches, candidate_count


def _pick_best_fuzzy_candidate(
    query_text: str,
    candidates: list[dict],
    similarity_threshold: float,
):
    """返回最佳候选和所有超过阈值的候选列表"""
    scored_candidates = []

    for candidate in candidates:
        compare_text = normalize_match_text(candidate["compare_text"]) or candidate["compare_text"]
        sequence_score = SequenceMatcher(None, query_text, compare_text).ratio()
        trigram_score = float(candidate["trigram_score"])
        final_score = max(trigram_score, sequence_score)

        if final_score >= similarity_threshold:
            scored_candidates.append({
                "query_text": query_text,
                "source_text": candidate["source_text"],
                "target_text": candidate["target_text"],
                "score": final_score,
            })

    if not scored_candidates:
        return None, []

    # 按分数降序排序
    scored_candidates.sort(key=lambda x: x["score"], reverse=True)
    
    # 去重（相同source_text只保留最高分的）
    seen_sources = set()
    unique_candidates = []
    for c in scored_candidates:
        if c["source_text"] not in seen_sources:
            seen_sources.add(c["source_text"])
            unique_candidates.append(c)
    
    # 最多返回5条
    unique_candidates = unique_candidates[:MAX_FUZZY_RESULTS]
    
    best_candidate = unique_candidates[0] if unique_candidates else None
    return best_candidate, unique_candidates


def _get_trigram_prefilter_threshold(similarity_threshold: float) -> float:
    return min(max(similarity_threshold, 0.01), 0.3)


def _chunked(items: list[str], chunk_size: int) -> list[list[str]]:
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]
