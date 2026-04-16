import logging
from io import BytesIO
from time import perf_counter
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.services.document_exporter import (
    DOCX_MEDIA_TYPE,
    build_translated_docx_filename,
    export_translated_docx,
)
from app.services.document_workspace import (
    build_docx_preview_html,
    build_docx_workspace,
    build_document_html_from_segments,
)
from app.services.file_record_service import (
    count_file_records,
    create_file_record_with_segments,
    create_txt_file_record_with_segments,
    get_file_record as get_file_record_model,
    get_file_record_with_segments,
    get_tm_target_text_map,
    list_file_records,
    list_segments_for_file_record,
    load_file_record_source,
)
from app.services.file_parser import parse_uploaded_file
from app.services.matcher import match_sentences_with_stats
from app.services.sentence_splitter import split_sentences
from app.services.tm_importer import XLSX_EXTENSIONS, import_tm_from_xlsx_upload


logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()

DEFAULT_TASKS_PAGE_SIZE = 24
MAX_TASKS_PAGE_SIZE = 100
DEFAULT_SEGMENTS_PAGE_SIZE = 200
MAX_SEGMENTS_PAGE_SIZE = 500


def _build_docx_download_response(filename: str, docx_bytes: bytes) -> StreamingResponse:
    export_filename = build_translated_docx_filename(filename)
    ascii_filename = export_filename.encode("ascii", "ignore").decode("ascii").strip() or "translated.docx"
    ascii_filename = ascii_filename.replace('"', "")
    quoted_filename = quote(export_filename)

    return StreamingResponse(
        BytesIO(docx_bytes),
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_filename}"; '
                f"filename*=UTF-8''{quoted_filename}"
            )
        },
    )


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


def _clamp_page(page: int) -> int:
    return max(page, 1)


def _clamp_page_size(page_size: int, default: int, maximum: int) -> int:
    if page_size < 1:
        return default
    return min(page_size, maximum)


def _build_pagination(base_path: str, page: int, page_size: int, total_items: int) -> dict:
    total_pages = max(1, (total_items + page_size - 1) // page_size) if total_items else 1
    current_page = min(_clamp_page(page), total_pages)
    start_item = 0 if total_items == 0 else (current_page - 1) * page_size + 1
    end_item = 0 if total_items == 0 else min(current_page * page_size, total_items)
    page_start = max(1, current_page - 2)
    page_end = min(total_pages, current_page + 2)

    def make_url(target_page: int) -> str:
        return f"{base_path}?page={target_page}&page_size={page_size}"

    return {
        "page": current_page,
        "page_size": page_size,
        "total_items": total_items,
        "total_pages": total_pages,
        "start_item": start_item,
        "end_item": end_item,
        "has_prev": current_page > 1,
        "has_next": current_page < total_pages,
        "prev_page": current_page - 1,
        "next_page": current_page + 1,
        "prev_url": make_url(current_page - 1) if current_page > 1 else None,
        "next_url": make_url(current_page + 1) if current_page < total_pages else None,
        "page_numbers": list(range(page_start, page_end + 1)),
        "page_urls": {target_page: make_url(target_page) for target_page in range(page_start, page_end + 1)},
    }


def _build_match_results(
    segments,
    tm_target_text_map: dict[str, str] | None = None,
) -> list:
    target_map = tm_target_text_map or {}
    return [
        type("MatchResult", (), {
            "source_sentence": seg.display_text,
            "status": seg.status,
            "score": seg.score,
            "matched_source_text": seg.matched_source_text,
            "target_text": seg.target_text,
            "sentence_id": seg.sentence_id,
            "source": seg.source,
            "tm_target_text": target_map.get(seg.matched_source_text or "", seg.target_text if seg.source == "tm" else ""),
        })()
        for seg in segments
    ]


def _build_tm_target_text_map(db: Session, segments) -> dict[str, str]:
    matched_source_texts = [
        seg.matched_source_text
        for seg in segments
        if getattr(seg, "matched_source_text", None)
    ]
    return get_tm_target_text_map(db, matched_source_texts)


def _render_tasks_list(
    request: Request,
    db: Session,
    page: int = 1,
    page_size: int = DEFAULT_TASKS_PAGE_SIZE,
    error_message: str | None = None,
) -> HTMLResponse:
    safe_page = _clamp_page(page)
    safe_page_size = _clamp_page_size(page_size, DEFAULT_TASKS_PAGE_SIZE, MAX_TASKS_PAGE_SIZE)
    total_file_records = count_file_records(db)
    pagination = _build_pagination("/tasks", safe_page, safe_page_size, total_file_records)
    file_records = list_file_records(
        db,
        skip=(pagination["page"] - 1) * safe_page_size,
        limit=safe_page_size,
    )
    return templates.TemplateResponse(
        request,
        "tasks.html",
        {
            "request": request,
            "file_records": file_records,
            "pagination": pagination,
            "error_message": error_message,
        },
    )


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return _render_index(request)


@router.get("/tasks", response_class=HTMLResponse)
def tasks_list(
    request: Request,
    page: int = 1,
    page_size: int = DEFAULT_TASKS_PAGE_SIZE,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """任务列表页面"""
    return _render_tasks_list(request, db=db, page=page, page_size=page_size)


@router.get("/tasks/{file_record_id}", response_class=HTMLResponse)
def continue_task(
    request: Request,
    file_record_id: UUID,
    page: int = 1,
    page_size: int = DEFAULT_SEGMENTS_PAGE_SIZE,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """继续翻译任务"""
    safe_page = _clamp_page(page)
    safe_page_size = _clamp_page_size(page_size, DEFAULT_SEGMENTS_PAGE_SIZE, MAX_SEGMENTS_PAGE_SIZE)
    result = get_file_record_with_segments(
        db,
        file_record_id,
        skip=(safe_page - 1) * safe_page_size,
        limit=safe_page_size,
    )
    if not result:
        return _render_tasks_list(request, db=db, error_message="任务不存在")

    pagination = _build_pagination(
        f"/tasks/{file_record_id}",
        safe_page,
        safe_page_size,
        result["total_segments"],
    )
    if pagination["page"] != safe_page:
        result = get_file_record_with_segments(
            db,
            file_record_id,
            skip=(pagination["page"] - 1) * safe_page_size,
            limit=safe_page_size,
        )

    file_record = result["file_record"]
    segments = result["segments"]
    tm_target_text_map = _build_tm_target_text_map(db, segments)

    # 转换 segments 为 results 格式
    results = _build_match_results(segments, tm_target_text_map=tm_target_text_map)

    source_bytes = load_file_record_source(file_record)
    if source_bytes:
        document_html = build_docx_preview_html(source_bytes)
    else:
        document_html = build_document_html_from_segments(segments) if segments else ""
    supports_preview = bool(document_html)

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "request": request,
            "filename": file_record.filename,
            "threshold": 0.6,
            "sentence_count": result["total_segments"],
            "results": results,
            "performance_summary": None,
            "document_html": document_html,
            "is_docx": supports_preview,
            "can_export_docx": bool(source_bytes),
            "file_record_id": file_record_id,
            "pagination": pagination,
        },
    )


@router.get("/tasks/{file_record_id}/export")
def export_task_docx(
    file_record_id: UUID,
    db: Session = Depends(get_db),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File record not found.")

    raw_bytes = load_file_record_source(file_record)
    if raw_bytes is None:
        raise HTTPException(status_code=400, detail="The source DOCX is unavailable for export.")

    segments = list_segments_for_file_record(db, file_record_id)
    try:
        translated_docx = export_translated_docx(raw_bytes=raw_bytes, segments=segments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _build_docx_download_response(file_record.filename, translated_docx)


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
    file_record_id = None

    parse_started_at = perf_counter()

    if is_docx_file:
        # DOCX: 创建文档记录并保存片段
        raw_bytes = await file.read()
        if not raw_bytes:
            raise HTTPException(status_code=400, detail="文件为空。")

        workspace_started_at = perf_counter()
        workspace_data = build_docx_workspace(
            db=db,
            raw_bytes=raw_bytes,
            similarity_threshold=threshold,
        )
        route_match_ms = (perf_counter() - workspace_started_at) * 1000

        # 使用预计算的 workspace 数据创建持久化文档，避免重复解析和匹配
        file_record = create_file_record_with_segments(
            db=db,
            raw_bytes=raw_bytes,
            filename=file.filename or "untitled.docx",
            similarity_threshold=threshold,
            workspace_data=workspace_data,
        )
        file_record_id = file_record.id

        document_html = workspace_data["document_html"]
        # 转换 segments 为 results 格式
        results = [
            type("MatchResult", (), {
                "source_sentence": seg["display_text"],
                "status": seg["status"],
                "score": seg["score"],
                "matched_source_text": seg["matched_source_text"],
                "target_text": seg["target_text"],
                "sentence_id": seg["sentence_id"],
                "source": "tm" if seg["status"] in ("exact", "fuzzy") else "none",
                "tm_target_text": seg["target_text"] if seg["status"] in ("exact", "fuzzy") else "",
            })()
            for seg in workspace_data["segments"]
        ]
        match_stats = type("MatchStats", (), workspace_data["match_stats"])()
        parse_ms = (perf_counter() - parse_started_at) * 1000
        split_ms = 0.0
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
        file_record = create_txt_file_record_with_segments(
            db=db,
            content=content,
            filename=file.filename or "untitled.txt",
            results=results,
        )
        file_record_id = file_record.id

        # 为结果添加 sentence_id 并生成预览 HTML
        for i, r in enumerate(results):
            r.sentence_id = f"sent-{i+1:05d}"
            r.source = "tm" if r.status in ("exact", "fuzzy") else "none"
            r.tm_target_text = r.target_text if r.source == "tm" else ""
        
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

    sentence_count = len(results)
    pagination = None
    display_results = results
    can_export_docx = False

    if file_record_id is not None:
        pagination = _build_pagination(
            f"/tasks/{file_record_id}",
            page=1,
            page_size=DEFAULT_SEGMENTS_PAGE_SIZE,
            total_items=sentence_count,
        )
        doc_result = get_file_record_with_segments(
            db,
            file_record_id,
            skip=0,
            limit=pagination["page_size"],
        )
        if doc_result:
            tm_target_text_map = _build_tm_target_text_map(db, doc_result["segments"])
            display_results = _build_match_results(
                doc_result["segments"],
                tm_target_text_map=tm_target_text_map,
            )
            source_bytes = load_file_record_source(doc_result["file_record"])
            can_export_docx = bool(source_bytes)
            if source_bytes:
                document_html = build_docx_preview_html(source_bytes)
            else:
                document_html = build_document_html_from_segments(doc_result["segments"]) if doc_result["segments"] else ""

    # 只要有 document_html 就支持预览
    supports_preview = bool(document_html)

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "request": request,
            "filename": file.filename,
            "threshold": threshold,
            "sentence_count": sentence_count,
            "results": display_results,
            "performance_summary": performance_summary,
            "document_html": document_html,
            "is_docx": supports_preview,
            "can_export_docx": can_export_docx,
            "file_record_id": file_record_id,
            "pagination": pagination,
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
