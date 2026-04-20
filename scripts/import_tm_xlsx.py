import argparse
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.tm_importer import import_tm_from_xlsx_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import translation memory from XLSX.")
    parser.add_argument("--database-url", required=True, help="SQLAlchemy database URL")
    parser.add_argument("--xlsx-path", required=True, help="XLSX file path")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Rows inserted per batch",
    )
    parser.add_argument(
        "--collection-id",
        type=UUID,
        default=None,
        help="TM collection UUID to import into",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = create_engine(args.database_url, future=True)

    with Session(engine) as session:
        summary = import_tm_from_xlsx_path(
            db=session,
            xlsx_path=args.xlsx_path,
            batch_size=args.batch_size,
            collection_id=args.collection_id,
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
