"""预览或按预览清单回填保护；默认只读，输出 UTF-8 JSONL。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import FileRecord, Segment
from app.services.auto_write_protection import set_auto_write_protection
from app.services.protection_backfill import inspect_backfill_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True, type=UUID)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--after-id", type=UUID)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000:
        parser.error("--limit 必须在 1 至 10000 之间")
    if args.apply and not args.manifest:
        parser.error("执行回填必须通过 --manifest 指定已核验的预览清单")
    if args.manifest and args.output.resolve() == args.manifest.resolve():
        parser.error("输出文件不能覆盖预览清单")
    records = []
    with SessionLocal() as db:
        if args.apply:
            manifest = [json.loads(line) for line in args.manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
            for item in manifest:
                if not item.get("eligible"):
                    continue
                if len(records) >= args.limit:
                    break
                segment = db.query(Segment).join(FileRecord).filter(
                    Segment.id == UUID(item["segment_id"]), FileRecord.project_id == args.project_id,
                ).with_for_update(of=Segment).populate_existing().first()
                if not segment:
                    records.append({**item, "applied": False, "reason": "not_found_in_project"})
                    continue
                current = inspect_backfill_candidate(db, segment, db.get(FileRecord, segment.file_record_id))
                if (current["version"] != item["version"] or current.get("tm_entry_id") != item.get("tm_entry_id")
                        or current.get("tm_updated_at") != item.get("tm_updated_at")):
                    current.update(eligible=False, reason="preview_evidence_changed")
                current["applied"] = bool(current["eligible"] and set_auto_write_protection(
                    segment, "protected", reason="tm_exact",
                ))
                db.commit()
                records.append(current)
        else:
            query = db.query(Segment).join(FileRecord).filter(FileRecord.project_id == args.project_id)
            if args.after_id:
                query = query.filter(Segment.id > args.after_id)
            for segment in query.order_by(Segment.id).limit(args.limit):
                records.append(inspect_backfill_candidate(db, segment, db.get(FileRecord, segment.file_record_id)))
            db.rollback()
    args.output.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records), encoding="utf-8")
    print(json.dumps({"records": len(records), "applied": sum(bool(item.get("applied")) for item in records),
                      "next_after_id": records[-1]["segment_id"] if records and not args.apply else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
