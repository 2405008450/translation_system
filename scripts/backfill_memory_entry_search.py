from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from sqlalchemy import bindparam, text

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import engine


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill the memory_entry_search projection table in small batches.",
    )
    parser.add_argument("--batch-size", type=int, default=20000)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--limit-batches", type=int, default=0)
    parser.add_argument("--resume-from", default="")
    parser.add_argument("--statement-timeout-ms", type=int, default=120000)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    batch_size = max(args.batch_size, 1)
    sleep_seconds = max(args.sleep, 0.0)
    limit_batches = max(args.limit_batches, 0)
    statement_timeout_ms = max(args.statement_timeout_ms, 0)
    last_id = args.resume_from.strip() or None

    if engine.dialect.name != "postgresql":
        raise RuntimeError("memory_entry_search backfill only supports PostgreSQL.")

    total_rows = 0
    batch_index = 0
    started_at = time.perf_counter()

    while True:
        with engine.begin() as connection:
            if statement_timeout_ms:
                connection.execute(
                    text("SELECT set_config('statement_timeout', :timeout_value, true)"),
                    {"timeout_value": f"{statement_timeout_ms}ms"},
                )

            cursor_filter_sql = ""
            select_params: dict[str, object] = {"batch_size": batch_size}
            if last_id:
                cursor_filter_sql = "AND id > CAST(:last_id AS uuid)"
                select_params["last_id"] = last_id

            ids = [
                row.entry_id
                for row in connection.execute(
                    text(
                        f"""
                        SELECT id AS entry_id
                        FROM memory_entries
                        WHERE source_normalized IS NOT NULL
                          AND char_length(source_normalized) > 0
                          AND source_language IS NOT NULL
                          AND target_language IS NOT NULL
                          {cursor_filter_sql}
                        ORDER BY id
                        LIMIT :batch_size
                        """
                    ),
                    select_params,
                )
            ]

            if not ids:
                break

            last_id = str(ids[-1])
            if args.dry_run:
                affected_rows = len(ids)
            else:
                delete_stmt = text(
                    "DELETE FROM memory_entry_search WHERE entry_id IN :entry_ids"
                ).bindparams(bindparam("entry_ids", expanding=True))
                insert_stmt = text(
                    """
                    INSERT INTO memory_entry_search (
                        entry_id,
                        collection_id,
                        source_language,
                        target_language,
                        source_hash,
                        source_normalized,
                        source_length,
                        updated_at
                    )
                    SELECT
                        id,
                        collection_id,
                        source_language,
                        target_language,
                        source_hash,
                        source_normalized,
                        char_length(source_normalized),
                        COALESCE(updated_at, NOW())
                    FROM memory_entries
                    WHERE id IN :entry_ids
                      AND source_normalized IS NOT NULL
                      AND char_length(source_normalized) > 0
                      AND source_language IS NOT NULL
                      AND target_language IS NOT NULL
                    """
                ).bindparams(bindparam("entry_ids", expanding=True))

                connection.execute(delete_stmt, {"entry_ids": ids})
                result = connection.execute(insert_stmt, {"entry_ids": ids})
                affected_rows = int(result.rowcount or 0)

        batch_index += 1
        total_rows += affected_rows
        elapsed = time.perf_counter() - started_at
        print(
            f"[memory-entry-search] batch={batch_index} rows={affected_rows} "
            f"total={total_rows} last_id={last_id} elapsed={elapsed:.1f}s",
            flush=True,
        )

        if limit_batches and batch_index >= limit_batches:
            break
        if sleep_seconds:
            time.sleep(sleep_seconds)

    elapsed = time.perf_counter() - started_at
    print(
        f"[memory-entry-search] done batches={batch_index} rows={total_rows} "
        f"last_id={last_id or ''} elapsed={elapsed:.1f}s",
        flush=True,
    )


if __name__ == "__main__":
    main()
