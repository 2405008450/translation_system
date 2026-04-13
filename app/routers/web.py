import logging
from time import perf_counter

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.services.document_workspace import build_docx_workspace, build_document_html_from_segments
from app.services.document_service import create_document_with_segments, create_txt_document_with_segments, get_document_with_segments, list_documents
from app.services.file_parser import parse_uploaded_file
from app.services.matcher import match_sentences_with_stats
from app.services.sentence_splitter import split_sentences
from app.services.tm_importer import XLSX_EXTENSIONS, import_tm_from_xlsx_upload


logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()


def _render_index(
    request: Request,
    import_summary=None,
    error_message: str | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "default_threshold": settings.default_similarity_threshold,
            "import_summary": import_summary,
            "error_message": error_message,
        },
    )


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return _render_index(request)


@router.get("/tasks", response_class=HTMLResponse)
def tasks_list(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """任务列表页面"""
    documents = list_documents(db, skip=0, limit=100)
    return templates.TemplateResponse(
        request,
        "tasks.html",
        {"request": request, "documents": documents},
    )


@router.get("/tasks/{document_id}", response_class=HTMLResponse)
def continue_task(
    request: Request,
    document_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """继续翻译任务"""
    result = get_document_with_segments(db, document_id)
    if not result:
        return templates.TemplateResponse(
            request,
            "tasks.html",
            {"request": request, "documents": list_documents(db), "error_message": "任务不存在"},
        )

    doc = result["document"]
    segments = result["segments"]

    # 转换 segments 为 results 格式
    results = [
        type("MatchResult", (), {
            "source_sentence": seg.display_text,
            "status": seg.status,
            "score": seg.score,
            "matched_source_text": seg.matched_source_text,
            "target_text": seg.target_text,
            "sentence_id": seg.sentence_id,
        })()
        for seg in segments
    ]

    # 从 segments 重建预览 HTML
    document_html = build_document_html_from_segments(segments) if segments else ""
    supports_preview = bool(document_html)

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "request": request,
            "filename": doc.filename,
            "threshold": 0.6,
            "sentence_count": len(results),
            "results": results,
            "performance_summary": None,
            "document_html": document_html,
            "is_docx": supports_preview,
            "document_id": document_id,
        },
    )


@router.post("/match", response_class=HTMLResponse)
async def upload_and_match(
    request: Request,
    file: UploadFile = File(...),
    threshold: float = Form(default=settings.default_similarity_threshold),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if not 0 <= threshold <= 1:
        raise HTTPException(status_code=400, detail="模糊匹配阈值必须在 0 到 1 之间。")

    extension = f".{(file.filename or '').split('.')[-1].lower()}" if file.filename else ""
    is_docx_file = extension == ".docx"
    document_html = ""
    document_id = None

    parse_started_at = perf_counter()

    if is_docx_file:
        # DOCX: 创建文档记录并保存片段
        raw_bytes = await file.read()
        if not raw_bytes:
            raise HTTPException(status_code=400, detail="文件为空。")

        # 创建持久化文档
        document = create_document_with_segments(
            db=db,
            raw_bytes=raw_bytes,
            filename=file.filename or "untitled.docx",
            similarity_threshold=threshold,
        )
        document_id = document.id

        # 重新获取 workspace 数据用于渲染（包含 document_html）
        workspace_data = build_docx_workspace(
            db=db,
            raw_bytes=raw_bytes,
            similarity_threshold=threshold,
        )
        document_html = workspace_data["document_html"]
        # 转换 segments 为 results 格式
        results = [
            type("MatchResult", (), {
                "source_sentence": seg["source_text"],
                "status": seg["status"],
                "score": seg["score"],
                "matched_source_text": seg["matched_source_text"],
                "target_text": seg["target_text"],
                "sentence_id": seg["sentence_id"],
            })()
            for seg in workspace_data["segments"]
        ]
        match_stats = type("MatchStats", (), workspace_data["match_stats"])()
        parse_ms = (perf_counter() - parse_started_at) * 1000
        split_ms = 0.0
        route_match_ms = parse_ms
    else:
        # TXT: 使用原有逻辑
        try:
            content = await parse_uploaded_file(file)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        parse_ms = (perf_counter() - parse_started_at) * 1000

        split_started_at = perf_counter()
        sentences = split_sentences(content)
        split_ms = (perf_counter() - split_started_at) * 1000

        match_started_at = perf_counter()
        results, match_stats = match_sentences_with_stats(
            db=db,
            sentences=sentences,
            similarity_threshold=threshold,
        )
        route_match_ms = (perf_counter() - match_started_at) * 1000

        # 创建 TXT 文档记录并保存片段
        document = create_txt_document_with_segments(
            db=db,
            content=content,
            filename=file.filename or "untitled.txt",
            results=results,
        )
        document_id = document.id

        # 为结果添加 sentence_id 并生成预览 HTML
        for i, r in enumerate(results):
            r.sentence_id = f"sent-{i+1:05d}"
        
        # 获取保存后的 segments 生成预览 HTML
        doc_result = get_document_with_segments(db, document_id)
        if doc_result:
            document_html = build_document_html_from_segments(doc_result["segments"])

    logger.info(
        "match request file=%s total=%s prepared=%s unique=%s exact=%s fuzzy=%s none=%s "
        "parse_ms=%.2f split_ms=%.2f exact_ms=%.2f fuzzy_ms=%.2f route_match_ms=%.2f total_match_ms=%.2f candidates=%s",
        file.filename,
        match_stats.total_input_sentences,
        match_stats.prepared_sentences,
        match_stats.unique_sentences,
        match_stats.exact_hits,
        match_stats.fuzzy_hits,
        match_stats.none_hits,
        parse_ms,
        split_ms,
        match_stats.exact_phase_ms,
        match_stats.fuzzy_phase_ms,
        route_match_ms,
        match_stats.total_match_ms,
        match_stats.fuzzy_candidates_evaluated,
    )

    performance_summary = {
        "parse_ms": round(parse_ms, 2),
        "split_ms": round(split_ms, 2),
        "route_match_ms": round(route_match_ms, 2),
        "stats": match_stats,
    }

    # 只要有 document_html 就支持预览
    supports_preview = bool(document_html)

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "request": request,
            "filename": file.filename,
            "threshold": threshold,
            "sentence_count": len(results),
            "results": results,
            "performance_summary": performance_summary,
            "document_html": document_html,
            "is_docx": supports_preview,
            "document_id": document_id,
        },
    )


@router.post("/workspace", response_class=HTMLResponse)
async def open_workspace(
    request: Request,
    workspace_file: UploadFile = File(...),
    threshold: float = Form(default=settings.default_similarity_threshold),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """已废弃，重定向到 /match"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/", status_code=303)


@router.post("/import-xlsx", response_class=HTMLResponse)
async def import_xlsx(
    request: Request,
    xlsx_file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    extension = f".{(xlsx_file.filename or '').split('.')[-1].lower()}" if xlsx_file.filename else ""
    if extension not in XLSX_EXTENSIONS:
        return _render_index(request, error_message="仅支持上传 .xlsx 文件。")

    raw_bytes = await xlsx_file.read()
    if not raw_bytes:
        return _render_index(request, error_message="上传的 XLSX 文件为空。")

    try:
        import_summary = import_tm_from_xlsx_upload(
            db=db,
            raw_bytes=raw_bytes,
            filename=xlsx_file.filename or "uploaded.xlsx",
        )
    except Exception as exc:
        db.rollback()
        return _render_index(request, error_message=f"导入失败：{exc}")

    return _render_index(request, import_summary=import_summary)
