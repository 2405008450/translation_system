import argparse

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


STATS_SQL = text(
    """
    SELECT
        COUNT(*) AS total_rows,
        COUNT(*) FILTER (WHERE source_hash IS NULL) AS null_source_hash_rows,
        COUNT(*) FILTER (WHERE source_hash IS NOT NULL) AS non_null_source_hash_rows
    FROM translation_memory
    """
)


DUPLICATE_STATS_SQL = text(
    """
    WITH duplicate_groups AS (
        SELECT
            source_hash,
            COUNT(*) AS row_count,
            COUNT(DISTINCT source_text) AS distinct_source_text_count,
            COUNT(DISTINCT target_text) AS distinct_target_text_count
        FROM translation_memory
        WHERE source_hash IS NOT NULL
        GROUP BY source_hash
        HAVING COUNT(*) > 1
    )
    SELECT
        COUNT(*) AS duplicate_group_count,
        COALESCE(SUM(row_count), 0) AS duplicate_row_count,
        COALESCE(SUM(row_count) - COUNT(*), 0) AS rows_to_delete,
        COUNT(*) FILTER (WHERE distinct_source_text_count > 1) AS conflicting_source_group_count,
        COUNT(*) FILTER (WHERE distinct_target_text_count > 1) AS conflicting_target_group_count
    FROM duplicate_groups
    """
)


TOP_DUPLICATES_SQL = text(
    """
    WITH duplicate_groups AS (
        SELECT
            source_hash,
            COUNT(*) AS row_count,
            COUNT(DISTINCT source_text) AS distinct_source_text_count,
            COUNT(DISTINCT target_text) AS distinct_target_text_count,
            MAX(COALESCE(updated_at, created_at)) AS latest_timestamp
        FROM translation_memory
        WHERE source_hash IS NOT NULL
        GROUP BY source_hash
        HAVING COUNT(*) > 1
    )
    SELECT
        source_hash,
        row_count,
        distinct_source_text_count,
        distinct_target_text_count,
        latest_timestamp
    FROM duplicate_groups
    ORDER BY row_count DESC, latest_timestamp DESC NULLS LAST
    LIMIT :limit
    """
)


DELETE_DUPLICATES_SQL = text(
    """
    WITH duplicate_rows AS (
        SELECT
            id,
            source_hash,
            updated_at,
            created_at,
            COUNT(*) OVER (PARTITION BY source_hash, target_text) AS target_frequency,
            COUNT(*) OVER (PARTITION BY source_hash, source_text) AS source_frequency
        FROM translation_memory
        WHERE source_hash IS NOT NULL
    ),
    ranked_rows AS (
        SELECT
            id,
            ROW_NUMBER() OVER (
                PARTITION BY source_hash
                ORDER BY
                    target_frequency DESC,
                    source_frequency DESC,
                    updated_at DESC NULLS LAST,
                    created_at DESC NULLS LAST,
                    id DESC
            ) AS row_rank
        FROM duplicate_rows
    ),
    deleted_rows AS (
        DELETE FROM translation_memory tm
        USING ranked_rows rr
        WHERE tm.id = rr.id
          AND rr.row_rank > 1
        RETURNING tm.id
    )
    SELECT COUNT(*) AS deleted_count
    FROM deleted_rows
    """
)


DROP_NON_UNIQUE_INDEX_SQL = text(
    """
    DROP INDEX IF EXISTS ix_translation_memory_source_hash
    """
)


CREATE_UNIQUE_INDEX_SQL = text(
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_translation_memory_source_hash
        ON translation_memory (source_hash)
        WHERE source_hash IS NOT NULL
    """
)


ANALYZE_SQL = text("ANALYZE translation_memory")


INDEX_PERMISSION_SQL = text(
    """
    SELECT pg_get_userbyid(c.relowner) = current_user AS can_manage_index
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = current_schema()
      AND c.relname = 'translation_memory'
      AND c.relkind = 'r'
    """
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Deduplicate translation_memory rows by source_hash. "
            "The kept row is chosen by target_text frequency, then source_text frequency, "
            "then the latest updated/created row."
        )
    )
    parser.add_argument("--database-url", required=True, help="SQLAlchemy database URL")
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="How many duplicate groups to print in the summary",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete duplicates and create the unique source_hash index",
    )
    return parser.parse_args()


def fetch_stats(connection) -> tuple[dict, dict]:
    overview = dict(connection.execute(STATS_SQL).mappings().one())
    duplicates = dict(connection.execute(DUPLICATE_STATS_SQL).mappings().one())
    return overview, duplicates


def print_summary(connection, top: int) -> None:
    overview, duplicates = fetch_stats(connection)

    print("=== translation_memory summary ===")
    print(f"total_rows={overview['total_rows']}")
    print(f"null_source_hash_rows={overview['null_source_hash_rows']}")
    print(f"non_null_source_hash_rows={overview['non_null_source_hash_rows']}")
    print(f"duplicate_group_count={duplicates['duplicate_group_count']}")
    print(f"duplicate_row_count={duplicates['duplicate_row_count']}")
    print(f"rows_to_delete={duplicates['rows_to_delete']}")
    print(f"conflicting_source_group_count={duplicates['conflicting_source_group_count']}")
    print(f"conflicting_target_group_count={duplicates['conflicting_target_group_count']}")

    if not duplicates["duplicate_group_count"]:
        return

    print("\n=== top duplicate groups ===")
    rows = connection.execute(TOP_DUPLICATES_SQL, {"limit": top}).mappings().all()
    for row in rows:
        print(
            "source_hash={source_hash} row_count={row_count} "
            "distinct_source_text_count={distinct_source_text_count} "
            "distinct_target_text_count={distinct_target_text_count} latest_timestamp={latest_timestamp}".format(
                **row
            )
        )


def apply_cleanup(connection) -> int:
    deleted_count = connection.execute(DELETE_DUPLICATES_SQL).scalar_one()
    connection.execute(ANALYZE_SQL)
    return deleted_count


def ensure_unique_index(engine) -> str:
    with engine.begin() as connection:
        can_manage_index = connection.execute(INDEX_PERMISSION_SQL).scalar_one_or_none()
        if not can_manage_index:
            return (
                "Skipped unique index creation because the current user is not "
                "the owner of table translation_memory."
            )

        connection.execute(DROP_NON_UNIQUE_INDEX_SQL)
        connection.execute(CREATE_UNIQUE_INDEX_SQL)
        return "Created or verified uq_translation_memory_source_hash."


def main() -> None:
    args = parse_args()
    engine = create_engine(args.database_url, future=True)

    with engine.connect() as connection:
        print_summary(connection, top=args.top)

    if not args.apply:
        print("\nDry run only. Re-run with --apply to delete duplicate rows.")
        return

    with engine.begin() as connection:
        deleted_count = apply_cleanup(connection)
    print(f"\nDeleted {deleted_count} duplicate rows.")

    try:
        index_result = ensure_unique_index(engine)
    except SQLAlchemyError as exc:
        index_result = f"Unique index step failed: {exc}"
    print(index_result)
    print()

    with engine.connect() as connection:
        print_summary(connection, top=args.top)


if __name__ == "__main__":
    main()
