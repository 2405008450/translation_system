import argparse

from sqlalchemy import create_engine, select
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
    last_id = 0

    with Session(engine) as session:
        while True:
            rows = session.execute(
                select(TranslationMemory)
                .where(TranslationMemory.id > last_id)
                .order_by(TranslationMemory.id.asc())
                .limit(args.batch_size)
            ).scalars().all()

            if not rows:
                break

            for row in rows:
                row.source_hash = build_source_hash(row.source_text)
                row.source_normalized = normalize_match_text(row.source_text) or normalize_text(
                    row.source_text
                )
                last_id = row.id
                updated += 1

            session.commit()
            print(f"Updated {updated} rows...", flush=True)

    print(f"Rebuilt fields for {updated} rows.")


if __name__ == "__main__":
    main()
