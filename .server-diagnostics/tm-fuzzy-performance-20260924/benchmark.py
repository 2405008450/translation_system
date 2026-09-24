import json
import logging
import time
from uuid import UUID

from app.database import SessionLocal
from app.models import FileRecord
from app.routers.api import _load_document_match_analysis_for_files
import app.services.matcher as matcher

logging.basicConfig(level=logging.INFO)
original = matcher._find_fuzzy_matches_chunk
counter = 0
baseline_seconds = 0
started = time.monotonic()


def measured(*args, **kwargs):
    global counter, baseline_seconds
    counter += 1
    before = time.monotonic()
    result = original(*args, **kwargs)
    elapsed = time.monotonic() - before
    if counter in (10, 50, 100, 150, 200, 250):
        before = time.monotonic()
        baseline = original(*args, **dict(kwargs, search_table=None))
        baseline_elapsed = time.monotonic() - before
        baseline_seconds += baseline_elapsed
        print("COMPARE", counter, "old", round(baseline_elapsed, 3),
              "new", round(elapsed, 3), "same", result == baseline, flush=True)
        assert result == baseline, ("candidate mismatch", counter)
    if counter % 20 == 0:
        print("PROGRESS", counter, "seconds",
              round(time.monotonic() - started - baseline_seconds, 2), flush=True)
    return result


matcher._find_fuzzy_matches_chunk = measured
with SessionLocal() as db:
    files = db.query(FileRecord).filter(
        FileRecord.project_id == UUID("a9607704-4a7d-427a-b4d8-035034bb9c8d")
    ).order_by(FileRecord.created_at, FileRecord.id).all()
    with matcher.document_statistics_match_timeout(db):
        result = _load_document_match_analysis_for_files(db, files)
    print("RESULT", json.dumps({str(k): v for k, v in result.items()},
                               ensure_ascii=False, default=str), flush=True)
    print("TOTAL_SECONDS", time.monotonic() - started - baseline_seconds,
          "BASELINE_COMPARISON_SECONDS", baseline_seconds, flush=True)
    db.rollback()
