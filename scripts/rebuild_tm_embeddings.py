from __future__ import annotations

import argparse
from datetime import datetime
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services.tm_vector import TM_EMBEDDING_VERSION, is_tm_vector_ready, sync_tm_embeddings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill source_embedding for translation memory rows."
    )
    parser.add_argument("--database-url", required=True, help="SQLAlchemy database URL")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Number of rows to update per batch",
    )
    parser.add_argument(
        "--rebuild-all",
        action="store_true",
        help="Rebuild embeddings for all rows instead of only missing/outdated rows",
    )
    return parser.parse_args()


def _load_batch(
    session: Session,
    *,
    batch_size: int,
    rebuild_all: bool,
    last_created_at: datetime | None,
    last_id: UUID | None,
):
    cursor_clause = ""
    params: dict[str, object] = {
        "batch_size": batch_size,
        "embedding_version": TM_EMBEDDING_VERSION,
        "rebuild_all": rebuild_all,
    }
    if last_created_at is not None and last_id is not None:
        cursor_clause = """
          AND (
                created_at > :last_created_at
             OR (
                    created_at = :last_created_at
                AND id > CAST(:last_id AS uuid)
             )
          )
        """
        params["last_created_at"] = last_created_at
        params["last_id"] = str(last_id)

    stmt = text(
        f"""
        SELECT id, source_text, created_at
        FROM translation_memory
        WHERE source_text IS NOT NULL
          AND (
                :rebuild_all
             OR source_embedding IS NULL
             OR source_embedding_version IS DISTINCT FROM :embedding_version
          )
          {cursor_clause}
        ORDER BY created_at ASC, id ASC
        LIMIT :batch_size
        """
    )
    return session.execute(stmt, params).mappings().all()


def main() -> None:
    args = parse_args()
    engine = create_engine(args.database_url, future=True)

    processed = 0
    last_created_at: datetime | None = None
    last_id: UUID | None = None

    with Session(engine) as session:
        if not is_tm_vector_ready(session):
            raise SystemExit(
                "pgvector support is not ready. Run scripts/add_tm_pgvector_support.sql first."
            )

        while True:
            rows = _load_batch(
                session,
                batch_size=args.batch_size,
                rebuild_all=args.rebuild_all,
                last_created_at=last_created_at,
                last_id=last_id,
            )
            if not rows:
                break

            sync_rows: list[tuple[UUID, str]] = []
            for row in rows:
                row_id = row["id"]
                source_text = row["source_text"]
                created_at = row["created_at"]
                if row_id is not None:
                    last_created_at = created_at
                    last_id = UUID(str(row_id))
                if row_id is None or not source_text:
                    continue
                sync_rows.append((UUID(str(row_id)), str(source_text)))

            processed += sync_tm_embeddings(session, sync_rows)
            print(f"Updated {processed} rows...", flush=True)

    print(f"Rebuilt embeddings for {processed} rows.")


if __name__ == "__main__":
    main()
