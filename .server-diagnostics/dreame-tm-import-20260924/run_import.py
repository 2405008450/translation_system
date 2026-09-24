import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import select, text

from app.database import SessionLocal
from app.models import MemoryBase, MemoryEntry
from app.services.resource_import_batch import create_resource_import_batch
from app.services.tm_importer import import_tm_from_tmx_path
from app.services.tmx_stream import iter_tmx_rows
from app.services.matcher import match_sentences_with_stats


ROOT = Path(__file__).resolve().parent
manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
write = '--import' in sys.argv
report = []

with SessionLocal() as db:
    print('database', db.execute(text('select current_database()')).scalar(), flush=True)
    # 写入前检查所有文件和库名，避免误覆盖已有记忆库。
    for item in manifest:
        path = ROOT / item['local_file']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
        existing = db.scalar(select(MemoryBase).where(MemoryBase.name == item['name']))
        print(json.dumps({'name': item['name'], 'existing_id': str(existing.id) if existing else None}, ensure_ascii=False), flush=True)
        if write:
            assert existing is None, '同名库已存在，停止重复导入'
    if not write:
        sys.exit(0)

    for item in manifest:
        path = ROOT / item['local_file']
        expected = {}
        for row in iter_tmx_rows(path, 'en-US', item['target_language']):
            if row.source_text and row.target_text:
                expected.setdefault(row.source_text, row.target_text)
        assert len(expected) == item['unique_sources']
        collection = MemoryBase(
            name=item['name'], source_language='en-US', target_language=item['target_language'],
            origin='manual',
            description='用户授权导入的追觅吸尘器测试记忆库；来源：' + item['filename'] + '；2026-09-24导入；重复原文保留首条；可单独删除。',
        )
        db.add(collection)
        db.flush()
        collection_id = collection.id
        batch = create_resource_import_batch(
            db, resource_type='tm', resource_id=collection_id, filename=item['filename'],
            file_path=path, file_format='tmx', source_language='en-US',
            target_language=item['target_language'], created_by_id=None,
        )
        batch_id = batch.id
        db.commit()
        result = dict(item, collection_id=str(collection_id), import_batch_id=str(batch_id))
        report.append(result)
        (ROOT / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        summary = import_tm_from_tmx_path(
            db, tmx_path=path, filename=item['filename'], source_language='en-US',
            target_language=item['target_language'], collection_id=collection_id,
            duplicate_policy='keep', batch_size=5000, import_batch_id=batch_id,
        )
        db.commit()
        result['summary'] = asdict(summary)
        assert summary.created_rows == len(expected)
        assert summary.updated_rows == 0
        assert summary.skipped_empty_rows == item['empty_rows']
        entries = list(db.scalars(select(MemoryEntry).where(MemoryEntry.collection_id == collection_id)))
        assert {e.source_text: e.target_text for e in entries} == expected
        assert all(e.source_language == 'en-US' and e.target_language == item['target_language'] and e.import_batch_id == batch_id and e.tmx_metadata for e in entries)
        actual_count = db.scalar(select(MemoryBase.entry_count).where(MemoryBase.id == collection_id))
        assert actual_count == len(expected)
        projected = db.execute(text('select count(*) from memory_entry_search where collection_id=:id'), {'id': collection_id}).scalar()
        assert projected == len(expected)
        result['verified_rows'] = len(entries)
        result['search_projection_rows'] = projected
        samples = [s for s in expected if len(s) >= 40][:10]
        matches, stats = match_sentences_with_stats(
            db, samples, 0.75, collection_ids=[collection_id], source_language='en-US',
            target_language=item['target_language'], include_fuzzy=False,
        )
        assert len(matches) == len(samples) and stats.exact_hits == len(samples)
        assert all(m.target_text == expected[s] for s, m in zip(samples, matches))
        result['exact_test'] = asdict(stats)
        fuzzy_samples = ['Please ' + s[0].lower() + s[1:] for s in samples[:3]]
        fuzzy, fuzzy_stats = match_sentences_with_stats(
            db, fuzzy_samples, 0.75, collection_ids=[collection_id], source_language='en-US',
            target_language=item['target_language'], include_fuzzy=True,
        )
        assert len(fuzzy) == 3 and fuzzy_stats.fuzzy_hits == 3
        assert all(m.matched_source_text in expected and m.target_text == expected[m.matched_source_text] for m in fuzzy)
        result['fuzzy_test'] = asdict(fuzzy_stats)
        result['fuzzy_samples'] = [m.model_dump(mode='json') for m in fuzzy]
        result['acceptance'] = 'passed'
        db.rollback()
        (ROOT / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'name': item['name'], 'id': str(collection_id), 'rows': len(entries), 'exact_hits': stats.exact_hits, 'fuzzy_hits': fuzzy_stats.fuzzy_hits, 'acceptance': 'passed'}, ensure_ascii=False), flush=True)
print('ALL_PASSED', flush=True)
