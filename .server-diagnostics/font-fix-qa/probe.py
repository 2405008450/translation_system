"""Compare in-memory exports; no source, translation or export-task writes."""
import contextlib
import io
import json
import logging
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from sqlalchemy import text
from app.database import SessionLocal
from app.models import FileRecord
from app.services.file_record_service import load_file_record_source, list_segments_for_file_record
import app.services.document_exporter as exporter

logging.disable(logging.CRITICAL)
project_id = UUID("cd230f40-2cd6-47aa-a9ab-3a3ee362928b")
original_code = Path(exporter.__file__).read_text()
patched_code = Path("/tmp/docx-font-fix-qa/document_exporter.py").read_text()


def summary(raw):
    fonts = {}
    explicit_sizes = 0
    contents = {}
    structures = {}
    with ZipFile(io.BytesIO(raw)) as archive:
        for name in archive.namelist():
            if not name.startswith("word/") or not name.endswith(".xml"):
                continue
            root = ET.fromstring(archive.read(name))
            contents[name] = [(node.tag, node.text or "") for node in root.iter() if node.tag in {exporter._qn("w", "t"), exporter._qn("a", "t"), exporter._qn("w", "instrText")}]
            structures[name] = {tag: len(root.findall(".//w:" + tag, exporter.NS)) for tag in ("tbl", "tr", "tc", "drawing", "hyperlink", "br")}
            for run in root.findall(".//w:r", exporter.NS):
                if not any(t.text for t in run.findall("w:t", exporter.NS)):
                    continue
                rf = run.find("w:rPr/w:rFonts", exporter.NS)
                family = rf.get(exporter._qn("w", "ascii"), "inherited") if rf is not None else "inherited"
                fonts[family] = fonts.get(family, 0) + 1
                explicit_sizes += run.find("w:rPr/w:sz", exporter.NS) is not None
    return {"fonts": fonts, "explicit_sizes": explicit_sizes}, contents, structures


with SessionLocal() as db:
    db.execute(text("SET TRANSACTION READ ONLY"))
    records = db.query(FileRecord).filter(FileRecord.project_id == project_id).all()
    for record in records:
        raw = load_file_record_source(record)
        segments = list_segments_for_file_record(db, record.id)
        options = dict(document_parse_mode=record.document_parse_mode, document_parse_options=record.document_parse_options, target_language=record.target_language)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            exec(compile(original_code, exporter.__file__, "exec"), exporter.__dict__)
            old = exporter.export_translated_docx(raw, segments, **options)
            exec(compile(patched_code, exporter.__file__, "exec"), exporter.__dict__)
            new = exporter.export_translated_docx(raw, segments, **options)
        old_summary, old_text, old_structures = summary(old)
        new_summary, new_text, new_structures = summary(new)
        source_summary, _, _ = summary(raw)
        # Compare logical text, not run fragmentation, which may legitimately change.
        same_text = {k: "".join(t for _, t in v) for k, v in old_text.items()} == {k: "".join(t for _, t in v) for k, v in new_text.items()}
        same_structure = old_structures == new_structures
        print(json.dumps({"file_id": str(record.id), "segments": len(segments), "source": source_summary, "before": old_summary, "after": new_summary, "same_text": same_text, "same_structure": same_structure}, ensure_ascii=True), flush=True)
        assert same_text and same_structure
