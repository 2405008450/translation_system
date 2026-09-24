import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parent
import app.services
app.services.__path__.insert(0, str(ROOT / 'services'))
from app.database import SessionLocal
from app.services.document_workspace import parse_docx_workspace
from app.services.document_match_analysis import DocumentMatchSegment, compute_document_match_analysis
from app.services.matcher import match_sentences_with_stats

raw = (ROOT / 'source.docx').read_bytes()
workspaces = {
    'legacy': parse_docx_workspace(raw),
    'en_docx_v1': parse_docx_workspace(raw, document_parse_options={'segmentation_profile':'en_docx_v1'}),
}
collections = json.loads((ROOT / 'collections.json').read_text())
report = []
with SessionLocal() as db:
    for item in collections:
        result = {'language':item['target_language'], 'collection_id':item['collection_id']}
        for profile, ws in workspaces.items():
            fid = uuid4()
            cid = UUID(item['collection_id'])
            segments = [DocumentMatchSegment(file_id=fid,source_text=s['source_text'],display_text=s['display_text'],collection_ids=(cid,),source_language='en-US',target_language=item['target_language']) for s in ws['segments']]
            analysis = compute_document_match_analysis(db, {fid:segments})[fid]
            assert analysis['total_segments'] == len(segments)
            assert sum(row['word_count'] for row in analysis['rows']) == analysis['total_words']
            result[profile] = analysis
            if profile == 'en_docx_v1':
                example = next(s['source_text'] for s in ws['segments'] if 'Cleaning and maintenance' in s['source_text'])
                matches, _ = match_sentences_with_stats(db,[example],0.5,collection_ids=[cid],source_language='en-US',target_language=item['target_language'])
                result['example'] = matches[0].model_dump(mode='json')
        assert result['legacy']['total_words'] == result['en_docx_v1']['total_words']
        report.append(result)
        (ROOT / 'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'language':result['language'],'before':result['legacy']['rows'],'after':result['en_docx_v1']['rows'],'example':result['example']['score']},ensure_ascii=False),flush=True)
    db.rollback()
