import logging
from time import perf_counter

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.services.document_workspace import build_docx_workspace, build_workspace_with_adapters
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


@router.post("/match", response_class=HTMLResponse)
async def upload_and_match(
    request: Request,
    file: UploadFile = File(...),
    threshold: float = Form(default=settings.default_similarity_threshold),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if not 0 <= threshold <= 1:
        raise HTTPException(status_code=400, detail="模糊匹配阈值必须在 0 到 1 之间。")

    parse_started_at = perf_counter()
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

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "request": request,
            "filename": file.filename,
            "threshold": threshold,
            "sentence_count": len(sentences),
            "results": results,
            "performance_summary": performance_summary,
        },
    )


@router.post("/workspace", response_class=HTMLResponse)
async def open_workspace(
    request: Request,
    workspace_file: UploadFile = File(...),
    threshold: float = Form(default=settings.default_similarity_threshold),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if not 0 <= threshold <= 1:
        raise HTTPException(status_code=400, detail="模糊匹配阈值必须在 0 到 1 之间。")

    filename = workspace_file.filename or ""
    extension = f".{filename.split('.')[-1].lower()}" if filename else ""
    
    # 支持的格式
    supported_extensions = {".txt", ".docx", ".pdf", ".pptx", ".dita", ".ditamap", ".xml", ".svg", ".yaml", ".yml", ".json", ".php"}
    if extension not in supported_extensions:
        supported_list = ", ".join(sorted(supported_extensions))
        raise HTTPException(
            status_code=400, 
            detail=f"不支持的文件格式 '{extension}'。支持的格式: {supported_list}"
        )

    raw_bytes = await workspace_file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="上传的文件为空。")

    workspace_started_at = perf_counter()
    
    # 使用适配器系统构建工作台
    try:
        workspace_data = build_workspace_with_adapters(
            db=db,
            raw_bytes=raw_bytes,
            filename=filename,
            similarity_threshold=threshold,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception(f"解析文件失败: {filename}")
        raise HTTPException(status_code=500, detail=f"解析文件失败: {str(e)}") from e
    
    workspace_ms = (perf_counter() - workspace_started_at) * 1000

    return templates.TemplateResponse(
        request,
        "workspace.html",
        {
            "request": request,
            "filename": workspace_file.filename,
            "threshold": threshold,
            "document_html": workspace_data["document_html"],
            "segments": workspace_data["segments"],
            "match_stats": workspace_data["match_stats"],
            "workspace_ms": round(workspace_ms, 2),
        },
    )


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
