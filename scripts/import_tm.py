import argparse
import csv

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import TranslationMemory
from app.services.normalizer import build_source_hash, normalize_match_text, normalize_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import translation memory from CSV.")
    parser.add_argument("--database-url", required=True, help="SQLAlchemy database URL")
    parser.add_argument("--csv-path", required=True, help="CSV file path")
    parser.add_argument(
        "--source-column",
        default="source_text",
        help="CSV source text column name",
    )
    parser.add_argument(
        "--target-column",
        default="target_text",
        help="CSV target text column name",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = create_engine(args.database_url, future=True)

    inserted = 0
    with open(args.csv_path, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        with Session(engine) as session:
            for row in reader:
                source_text = (row.get(args.source_column) or "").strip()
                target_text = (row.get(args.target_column) or "").strip()
                if not source_text or not target_text:
                    continue

                session.add(
                    TranslationMemory(
                        source_text=source_text,
                        target_text=target_text,
                        source_hash=build_source_hash(source_text),
                        source_normalized=normalize_match_text(source_text)
                        or normalize_text(source_text),
                    )
                )
                inserted += 1

            session.commit()

    print(f"Imported {inserted} rows.")


if __name__ == "__main__":
    main()
