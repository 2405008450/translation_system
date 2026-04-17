from __future__ import annotations

import hashlib
import logging
from math import sqrt
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.normalizer import normalize_match_text, normalize_text


logger = logging.getLogger(__name__)

TM_EMBEDDING_VERSION = 1
_VECTOR_AVAILABILITY_CACHE: dict[str, bool] = {}


def get_tm_vector_dimensions() -> int:
    settings = get_settings()
    return min(max(int(settings.tm_vector_dimensions), 32), 1024)


def build_tm_embedding_text(text_value: str) -> str:
    return normalize_match_text(text_value) or normalize_text(text_value)


def build_tm_embedding(text_value: str, dimensions: int | None = None) -> list[float]:
    normalized_text = build_tm_embedding_text(text_value)
    vector_dimensions = dimensions or get_tm_vector_dimensions()
    if not normalized_text:
        return [0.0] * vector_dimensions

    features = _collect_embedding_features(normalized_text)
    if not features:
        return [0.0] * vector_dimensions

    vector = [0.0] * vector_dimensions
    for feature, weight in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
        bucket = int.from_bytes(digest[:4], "big") % vector_dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign * weight

    norm = sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return [0.0] * vector_dimensions

    return [round(value / norm, 8) for value in vector]


def build_tm_embedding_literal(text_value: str, dimensions: int | None = None) -> str:
    vector = build_tm_embedding(text_value, dimensions=dimensions)
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


def is_tm_vector_ready(db: Session) -> bool:
    settings = get_settings()
    if not settings.tm_vector_enabled:
        return False

    cache_key = _resolve_cache_key(db)
    cached = _VECTOR_AVAILABILITY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        available = bool(
            db.execute(
                text(
                    """
                    SELECT
                        EXISTS (
                            SELECT 1
                            FROM pg_extension
                            WHERE extname = 'vector'
                        )
                        AND EXISTS (
                            SELECT 1
                            FROM information_schema.columns
                            WHERE table_schema = current_schema()
                              AND table_name = 'translation_memory'
                              AND column_name = 'source_embedding'
                        )
                        AND EXISTS (
                            SELECT 1
                            FROM information_schema.columns
                            WHERE table_schema = current_schema()
                              AND table_name = 'translation_memory'
                              AND column_name = 'source_embedding_version'
                        )
                    """
                )
            ).scalar()
        )
    except SQLAlchemyError as exc:
        logger.warning("tm vector availability probe failed: %s", exc)
        available = False

    _VECTOR_AVAILABILITY_CACHE[cache_key] = available
    return available


def mark_tm_vector_unavailable(db: Session) -> None:
    _VECTOR_AVAILABILITY_CACHE[_resolve_cache_key(db)] = False


def sync_tm_embeddings(
    db: Session,
    rows: list[tuple[UUID, str]],
) -> int:
    if not rows or not is_tm_vector_ready(db):
        return 0

    vector_dimensions = get_tm_vector_dimensions()
    payload = [
        {
            "id": str(row_id),
            "source_embedding": build_tm_embedding_literal(source_text, dimensions=vector_dimensions),
            "source_embedding_version": TM_EMBEDDING_VERSION,
        }
        for row_id, source_text in rows
        if source_text
    ]
    if not payload:
        return 0

    try:
        db.execute(
            text(
                f"""
                UPDATE translation_memory
                SET source_embedding = CAST(:source_embedding AS vector({vector_dimensions})),
                    source_embedding_version = :source_embedding_version
                WHERE id = CAST(:id AS uuid)
                """
            ),
            payload,
        )
        db.commit()
        return len(payload)
    except SQLAlchemyError as exc:
        db.rollback()
        mark_tm_vector_unavailable(db)
        logger.warning("tm embedding sync skipped because vector update failed: %s", exc)
        return 0


def _collect_embedding_features(normalized_text: str) -> list[tuple[str, float]]:
    features: list[tuple[str, float]] = []
    compact_text = normalized_text.replace(" ", "")

    for char in compact_text:
        features.append((f"char:{char}", 0.75))

    for index in range(len(compact_text) - 1):
        features.append((f"bigram:{compact_text[index:index + 2]}", 1.25))

    words = [word for word in normalized_text.split(" ") if word]
    for word in words:
        features.append((f"word:{word}", 1.0))

    for index in range(len(words) - 1):
        features.append((f"word_bigram:{words[index]} {words[index + 1]}", 1.1))

    return features


def _resolve_cache_key(db: Session) -> str:
    return db.get_bind().url.render_as_string(hide_password=True)
