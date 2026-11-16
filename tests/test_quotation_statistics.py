import io
from uuid import uuid4
from openpyxl import Workbook
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.services.quotation_statistics import parse_quotation, statistics_from_segments, segment_text
from app.services.document_statistics import normalize_document_statistics
from app.services.document_match_analysis import DocumentMatchSegment, compute_document_match_analysis
from app.routers.quotation import router
from app.config import get_settings


def test_total_and_match_buckets_use_same_words():
    file_id = uuid4()
    texts = ["a@example.com", "a@example.com", "Привет мир", "مرحبا بالعالم", "中文，标点。"]
    stats = statistics_from_segments([dict(source_text=t) for t in texts])
    from app.services.quotation_counter import count_text, COUNT_POLICY_VERSION
    segments = [DocumentMatchSegment(file_id=file_id,source_text=t,source_word_count=count_text(t).word_count,
                count_policy_version=COUNT_POLICY_VERSION) for t in texts]
    analysis = compute_document_match_analysis(None,{file_id:segments})[file_id]
    assert stats['words'] == analysis['total_words'] == sum(row['word_count'] for row in analysis['rows']) == 12
    assert normalize_document_statistics(stats)['count_policy_version'] == COUNT_POLICY_VERSION


def test_hidden_grouped_columns_and_options():
    book = Workbook()
    sheet = book.active
    sheet['A1'] = '正文'
    sheet['B1'] = '隐藏列'
    sheet['C1'] = '同组隐藏列'
    sheet.column_dimensions.group('B','C',hidden=True)
    stream = io.BytesIO(); book.save(stream)
    visible = parse_quotation(stream.getvalue(),'sample.xlsx',{'xlsx_translate_hidden_content':False})
    all_text = parse_quotation(stream.getvalue(),'sample.xlsx',{'xlsx_translate_hidden_content':True})
    assert visible['statistics']['words'] == 2
    assert all_text['statistics']['words'] == 10
    assert sum(bool(u['metadata'].get('is_hidden')) for u in all_text['units']) == 2


def test_internal_markers_do_not_become_quote_words():
    assert segment_text(dict(source_text='Hello MATH1', segment_metadata={'quotation_text':'Hello MATH1',
        'quotation_excluded_placeholders':['MATH1']})) == 'Hello '


def test_api_auth_and_uploaded_text():
    app = FastAPI(); app.include_router(router,prefix='/api')
    settings = get_settings(); previous = settings.quotation_service_token
    settings.quotation_service_token = 'test-only-token'
    try:
        with TestClient(app) as client:
            assert client.get('/api/internal/quotation/config').status_code == 401
            response = client.post('/api/internal/quotation/parse',headers={'Authorization':'Bearer test-only-token'},
                files={'file':('test.txt','Привет мир'.encode())},data={'options':'{}'})
            assert response.status_code == 200
            assert response.json()['statistics']['words'] == 2
            assert client.post('/api/internal/quotation/parse',headers={'Authorization':'Bearer test-only-token'},
                files={'file':('test.txt',b'x')},data={'options':'[]'}).status_code == 422
    finally:
        settings.quotation_service_token = previous


def test_docx_import_and_quote_share_parser():
    from docx import Document
    from app.services.document_workspace import parse_docx_workspace
    doc = Document(); doc.add_paragraph('Visit https://example.com/a?x=1&y=2')
    doc.add_paragraph('中文，标点。'); stream = io.BytesIO(); doc.save(stream)
    workspace = parse_docx_workspace(stream.getvalue())
    quote = parse_quotation(stream.getvalue(),'sample.docx')
    assert workspace['document_statistics']['words'] == quote['statistics']['words'] == 8


def test_korean_proofing_language_uses_validated_word_semantics():
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    document = Document(); run = document.add_paragraph().add_run('한국어 문장')
    language = OxmlElement('w:lang'); language.set(qn('w:eastAsia'), 'ko-KR')
    run._r.get_or_add_rPr().append(language)
    stream = io.BytesIO(); document.save(stream)
    result = parse_quotation(stream.getvalue(), 'korean.docx')
    assert result['statistics']['words'] == 2
    assert result['units'][0]['text'] == '한국어 문장'


def test_direct_statistics_has_new_count_policy():
    from app.services.document_statistics import compute_document_statistics
    assert compute_document_statistics(b'a@example.com','source.txt')['words'] == 1
    assert compute_document_statistics(b'broken','source.unknown')['words'] is None
