import argparse
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.term_importer import import_terms_from_xlsx_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import term base entries from XLSX.")
    parser.add_argument("--database-url", required=True, help="SQLAlchemy database URL")
    parser.add_argument("--xlsx-path", required=True, help="XLSX file path")
    parser.add_argument("--term-base-id", type=UUID, required=True, help="Term base UUID to import into")
    parser.add_argument("--source-language", required=True, help="Source language code, e.g. zh-CN")
    parser.add_argument("--target-language", required=True, help="Target language code, e.g. en-US")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Rows inserted per batch",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = create_engine(args.database_url, future=True)

    with Session(engine) as session:
        summary = import_terms_from_xlsx_path(
            db=session,
            xlsx_path=args.xlsx_path,
            term_base_id=args.term_base_id,
            batch_size=args.batch_size,
            source_language=args.source_language,
            target_language=args.target_language,
        )

    print(
        f"Processed {summary.imported_rows} rows from {summary.filename}; "
        f"created: {summary.created_rows}; "
        f"updated: {summary.updated_rows}; "
        f"skipped header rows: {summary.skipped_header_rows}; "
        f"skipped empty rows: {summary.skipped_empty_rows}"
    )


if __name__ == "__main__":
    main()
