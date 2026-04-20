import argparse
from uuid import UUID

from sqlalchemy import and_, create_engine, or_, select
from sqlalchemy.orm import Session

from app.models import TranslationMemory
from app.services.normalizer import build_source_hash, normalize_match_text, normalize_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild source_hash and source_normalized for translation memory rows."
    )
    parser.add_argument("--database-url", required=True, help="SQLAlchemy database URL")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Number of rows to update per batch",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = create_engine(args.database_url, future=True)

    updated = 0
    last_created_at = None
    last_id: UUID | None = None

    with Session(engine) as session:
        while True:
            stmt = (
                select(TranslationMemory)
                .order_by(TranslationMemory.created_at.asc(), TranslationMemory.id.asc())
                .limit(args.batch_size)
            )
            if last_created_at is not None and last_id is not None:
                stmt = stmt.where(
                    or_(
                        TranslationMemory.created_at > last_created_at,
                        and_(
                            TranslationMemory.created_at == last_created_at,
                            TranslationMemory.id > last_id,
                        ),
                    )
                )

            rows = session.execute(stmt).scalars().all()
            if not rows:
                break

            for row in rows:
                row.source_hash = build_source_hash(row.source_text)
                row.source_normalized = normalize_match_text(row.source_text) or normalize_text(
                    row.source_text
                )
                last_created_at = row.created_at
                last_id = row.id
                updated += 1

            session.commit()
            print(f"Updated {updated} rows...", flush=True)

    print(f"Rebuilt fields for {updated} rows.")


if __name__ == "__main__":
    main()
