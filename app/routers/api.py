"""
API 路由模块 - 文件上传、解析和导出接口

支持多种文档格式的上传、解析和导出。
"""
import json
from io import BytesIO
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models import Segment, Term, TermbaseCollection, TMCollection, TranslationMemory, User
from app.services.comment_service import (
    create_segment_comment,
    create_segment_comment_reply,
    delete_segment_comment,
    list_segment_comments_for_file_record,
    serialize_segment_comment,
    update_segment_comment,
)
from app.services.document_exporter import (
    DOCX_MEDIA_TYPE,
    build_translated_docx_filename,
    export_translated_docx,
)
from app.services.document_workspace import (
    build_docx_preview_html,
    build_document_html_from_segments,
    build_docx_workspace,
)
from app.services.file_record_service import (
    batch_update_segments,
    create_file_record_with_segments,
    delete_file_record,
    get_file_record as get_file_record_model,
    get_file_record_with_segments,
    get_tm_target_text_map,
    list_file_records,
    list_segments_for_file_record,
    load_file_record_source,
    update_segment_by_sentence_id,
    update_segment_with_llm_result,
)
from app.services.llm_service import (
    LLMConfigurationError,
    LLMTranslationFailure,
    LLMTranslationTask,
    iter_batch_translate,
    validate_provider_choice,
)
from app.services.normalizer import build_source_hash, normalize_match_text, normalize_text
from app.services.matcher import get_tm_candidates
from app.services.slate_parser import parse_docx_for_slate
from app.services.adapters import (
    get_registry,
    UnsupportedFormatError,
    FileTooLargeError,
    ParseError,
    ExportService,
    ExportError,
    DocumentAST,
    DitaExporter,
    SvgExporter,
    TmxExporter,
    XliffExporter,
    XliffImporter,
)
from app.services.tm_importer import XLSX_EXTENSIONS, import_tm_from_xlsx_upload
from app.services.tm_vector import sync_tm_embeddings


router = APIRouter(dependencies=[Depends(get_current_user)])


class SegmentUpdate(BaseModel):
    sentence_id: str
    target_text: str
    source: str = "manual"


class BatchSegmentUpdate(BaseModel):
    updates: list[SegmentUpdate]


class LLMTranslateRequest(BaseModel):
    scope: Literal["fuzzy_only", "none_only", "all", "all_with_exact"] = "all"
    provider: Literal["auto", "deepseek", "openrouter"] = "auto"


class TMCollectionPayload(BaseModel):
    name: str
    description: str | None = None


class TermbaseCollectionPayload(BaseModel):
    name: str
    description: str | None = None


class TermPayload(BaseModel):
    source_text: str
    target_text: str
    collection_id: UUID | None = None


class CommentCreateRequest(BaseModel):
    sentence_id: str | None = None
    segment_id: UUID | None = None
    anchor_mode: Literal["sentence", "range"] = "range"
    range_start_offset: int | None = None
    range_end_offset: int | None = None
    anchor_text: str | None = None
    body: str


class CommentUpdateRequest(BaseModel):
    body: str | None = None
    status: Literal["open", "resolved"] | None = None


class CommentReplyRequest(BaseModel):
    body: str


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


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_llm_translation_tasks(
    db: Session,
    file_record_id: UUID,
    scope: Literal["fuzzy_only", "none_only", "all", "all_with_exact"],
) -> list[LLMTranslationTask]:
    statuses_by_scope = {
        "fuzzy_only": {"fuzzy"},
        "none_only": {"none"},
        "all": {"fuzzy", "none"},
        "all_with_exact": {"exact", "fuzzy", "none"},
    }
    target_statuses = statuses_by_scope[scope]
    segments = list_segments_for_file_record(db, file_record_id)
    tm_target_text_map = get_tm_target_text_map(
        db,
        [segment.matched_source_text for segment in segments if segment.matched_source_text],
    )

    tasks: list[LLMTranslationTask] = []
    for segment in segments:
        if segment.status not in target_statuses:
            continue

        tm_target_text = tm_target_text_map.get(
            segment.matched_source_text or "",
            segment.target_text if segment.source == "tm" else "",
        )

        tasks.append(
            LLMTranslationTask(
                sentence_id=segment.sentence_id,
                status=segment.status,
                source_text=segment.source_text,
                block_type=segment.block_type,
                matched_source_text=segment.matched_source_text,
                tm_target_text=tm_target_text,
            )
        )

    return tasks

# 支持的文件扩展名
SUPPORTED_EXTENSIONS = {".docx", ".txt", ".pdf", ".pptx", ".dita", ".ditamap", ".xml", ".svg"}


def _get_file_extension(filename: str) -> str:
    """获取文件扩展名（小写）"""
    return Path(filename or "").suffix.lower()


def _validate_file_upload(file: UploadFile, allowed_extensions: set[str] | None = None) -> str:
    """验证上传的文件
    
    Args:
        file: 上传的文件
        allowed_extensions: 允许的扩展名集合，None 表示使用默认支持的扩展名
        
    Returns:
        str: 文件扩展名
        
    Raises:
        HTTPException: 当文件格式不支持时
    """
    ext = _get_file_extension(file.filename)
    allowed = allowed_extensions or SUPPORTED_EXTENSIONS
    
    if ext not in allowed:
        supported_list = ", ".join(sorted(allowed))
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式 '{ext}'。支持的格式: {supported_list}"
        )
    
    return ext


def _validate_docx_upload(file: UploadFile) -> None:
    """验证 DOCX 文件上传（向后兼容）"""
    _validate_file_upload(file, {".docx"})


def _normalize_collection_name(name: str) -> str:
    return " ".join(name.strip().split())


def _validate_collection_ids(
    db: Session,
    collection_ids: list[UUID] | None,
) -> list[UUID] | None:
    if not collection_ids:
        return None

    normalized_ids = list(dict.fromkeys(collection_ids))
    existing_collections = (
        db.query(TMCollection)
        .filter(TMCollection.id.in_(normalized_ids))
        .all()
    )
    existing_ids = {collection.id for collection in existing_collections}
    missing_ids = [collection_id for collection_id in normalized_ids if collection_id not in existing_ids]
    if missing_ids:
        raise HTTPException(status_code=404, detail="选择的 TM 记忆库不存在。")

    return normalized_ids


def _get_collection_or_404(db: Session, collection_id: UUID | None) -> TMCollection | None:
    if collection_id is None:
        return None

    collection = db.query(TMCollection).filter(TMCollection.id == collection_id).first()
    if collection is None:
        raise HTTPException(status_code=404, detail="TM 记忆库不存在。")
    return collection


def _filter_tm_collection(query, collection_id: UUID | None):
    if collection_id is None:
        return query.filter(TranslationMemory.collection_id.is_(None))
    return query.filter(TranslationMemory.collection_id == collection_id)


def _serialize_tm_collection(collection: TMCollection, entry_count: int = 0) -> dict:
    return {
        "id": collection.id,
        "name": collection.name,
        "description": collection.description,
        "created_at": collection.created_at.isoformat(),
        "updated_at": collection.updated_at.isoformat(),
        "entry_count": entry_count,
    }


@router.post("/parser/slate")
async def upload_for_slate(
    file: UploadFile = File(...),
    threshold: float = Form(default=0.6),
    collection_ids: list[UUID] | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """上传文件并解析为 Slate 编辑器格式
    
    目前仅支持 DOCX 格式。
    """
    _validate_docx_upload(file)

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")

    selected_collection_ids = _validate_collection_ids(db, collection_ids)
    try:
        result = parse_docx_for_slate(
            db=db,
            raw_bytes=raw_bytes,
            similarity_threshold=threshold,
            collection_ids=selected_collection_ids,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/parser/workspace")
async def upload_for_workspace(
    file: UploadFile = File(...),
    threshold: float = Form(default=0.6),
    collection_ids: list[UUID] | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """上传文件并构建翻译工作台
    
    目前仅支持 DOCX 格式。
    """
    _validate_docx_upload(file)

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")

    selected_collection_ids = _validate_collection_ids(db, collection_ids)
    try:
        return build_docx_workspace(
            db=db,
            raw_bytes=raw_bytes,
            similarity_threshold=threshold,
            collection_ids=selected_collection_ids,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/parser/parse")
async def parse_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """通用文档解析接口
    
    使用适配器系统解析多种格式的文档。
    """
    ext = _validate_file_upload(file)
    
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")
    
    try:
        registry = get_registry()
        adapter = registry.get_adapter(file.filename)
        result = adapter.parse_with_validation(raw_bytes, file.filename)
        
        return {
            "filename": file.filename,
            "format": ext,
            "ast": result.ast.to_dict(),
            "segments": [seg.to_dict() for seg in result.segments],
            "metadata": result.metadata,
        }
    except UnsupportedFormatError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except ParseError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"解析失败: {str(exc)}") from exc


@router.get("/parser/formats")
async def get_supported_formats():
    """获取支持的文件格式列表"""
    registry = get_registry()
    
    formats = []
    for ext in sorted(registry.list_supported_extensions()):
        adapter = registry.get_adapter(f"test{ext}")
        formats.append({
            "extension": ext,
            "adapter": adapter.__class__.__name__,
            "max_size_mb": adapter.get_max_file_size() / (1024 * 1024),
        })
    
    return {
        "formats": formats,
        "total": len(formats),
    }


# ============== 适配器导出相关模型 ==============

class AdapterExportRequest(BaseModel):
    """导出请求模型"""
    segments: List[dict]
    format: str = "txt"
    bilingual: bool = False
    filename: Optional[str] = None


class DitaExportRequest(BaseModel):
    """DITA 导出请求模型"""
    ast: dict
    translations: Dict[str, str]
    original_content: Optional[str] = None


class SvgExportRequest(BaseModel):
    """SVG 导出请求模型"""
    original_content: str
    translations: Dict[str, str]
    bilingual: bool = False


class TmxExportRequest(BaseModel):
    """TMX 导出请求模型"""
    segments: List[dict]
    source_lang: str = "zh-CN"
    target_lang: str = "en-US"
    filename: Optional[str] = None


class XliffExportRequest(BaseModel):
    """XLIFF 导出请求模型"""
    segments: List[dict]
    source_lang: str = "zh-CN"
    target_lang: str = "en-US"
    filename: str = "document"
    version: str = "1.2"


# ============== 适配器导出接口 ==============

@router.post("/export/txt")
async def export_txt(request: AdapterExportRequest):
    """导出为 TXT 格式"""
    try:
        service = ExportService()
        from app.services.adapters.models import BlockNode, NodeType
        nodes = []
        for seg in request.segments:
            text = seg.get("target_text") or seg.get("source_text", "")
            if text:
                nodes.append(BlockNode(node_type=NodeType.PARAGRAPH, text_content=text))
        
        ast = DocumentAST(nodes=nodes, source_format=".txt")
        translations = {
            seg.get("segment_id", f"seg_{i}"): seg.get("target_text", "")
            for i, seg in enumerate(request.segments)
        }
        
        if request.bilingual:
            nodes_bilingual = []
            for seg in request.segments:
                source = seg.get("source_text", "")
                if source:
                    nodes_bilingual.append(BlockNode(node_type=NodeType.PARAGRAPH, text_content=source))
            ast_bilingual = DocumentAST(nodes=nodes_bilingual, source_format=".txt")
            content = service.export_bilingual(ast_bilingual, translations, format="txt")
            filename = "bilingual_export.txt"
        else:
            content = service.export_txt(ast, translations)
            filename = "export.txt"
        
        return Response(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}") from e


@router.post("/export/docx")
async def export_adapter_docx(request: AdapterExportRequest):
    """通过适配器导出为 DOCX 格式"""
    try:
        service = ExportService()
        from app.services.adapters.models import BlockNode, NodeType
        nodes = []
        for seg in request.segments:
            text = seg.get("target_text") or seg.get("source_text", "")
            if text:
                nodes.append(BlockNode(node_type=NodeType.PARAGRAPH, text_content=text))
        
        ast = DocumentAST(nodes=nodes, source_format=".docx")
        translations = {
            seg.get("segment_id", f"seg_{i}"): seg.get("target_text", "")
            for i, seg in enumerate(request.segments)
        }
        
        if request.bilingual:
            nodes_bilingual = []
            for seg in request.segments:
                source = seg.get("source_text", "")
                if source:
                    nodes_bilingual.append(BlockNode(node_type=NodeType.PARAGRAPH, text_content=source))
            ast_bilingual = DocumentAST(nodes=nodes_bilingual, source_format=".docx")
            content = service.export_bilingual(ast_bilingual, translations, format="docx")
            filename = "bilingual_export.docx"
        else:
            content = service.export_docx(ast, translations)
            filename = "export.docx"
        
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}") from e


@router.post("/export/dita")
async def export_dita(request: DitaExportRequest):
    """导出为 DITA 格式"""
    try:
        import base64
        exporter = DitaExporter()
        ast = DocumentAST.from_dict(request.ast)
        original_bytes = None
        if request.original_content:
            original_bytes = base64.b64decode(request.original_content)
        content = exporter.export(ast, request.translations, original_bytes)
        return Response(
            content=content,
            media_type="application/xml",
            headers={"Content-Disposition": 'attachment; filename="export.dita"'},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DITA 导出失败: {str(e)}") from e


@router.post("/export/svg")
async def export_svg(request: SvgExportRequest):
    """导出为 SVG 格式"""
    try:
        import base64
        exporter = SvgExporter()
        original_bytes = base64.b64decode(request.original_content)
        if request.bilingual:
            content, warnings = exporter.export_bilingual(original_bytes, request.translations)
            filename = "bilingual_export.svg"
        else:
            content, warnings = exporter.export(original_bytes, request.translations)
            filename = "export.svg"
        return Response(
            content=content,
            media_type="image/svg+xml",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Export-Warnings": str(len(warnings)),
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SVG 导出失败: {str(e)}") from e


@router.get("/export/formats")
async def get_export_formats():
    """获取支持的导出格式列表"""
    return {
        "formats": [
            {"id": "txt", "name": "纯文本 (TXT)", "extension": ".txt", "bilingual": True},
            {"id": "docx", "name": "Word 文档 (DOCX)", "extension": ".docx", "bilingual": True},
            {"id": "dita", "name": "DITA XML", "extension": ".dita", "bilingual": False},
            {"id": "svg", "name": "SVG 矢量图", "extension": ".svg", "bilingual": True},
            {"id": "tmx", "name": "翻译记忆库 (TMX)", "extension": ".tmx", "bilingual": False},
            {"id": "xliff", "name": "XLIFF 离线文件", "extension": ".xlf", "bilingual": False},
        ]
    }


@router.post("/export/tmx")
async def export_tmx(request: TmxExportRequest):
    """导出为 TMX 格式"""
    try:
        exporter = TmxExporter(source_lang=request.source_lang, target_lang=request.target_lang)
        content = exporter.export(request.segments, request.filename)
        filename = "export.tmx"
        if request.filename:
            base_name = request.filename.rsplit(".", 1)[0]
            filename = f"{base_name}.tmx"
        return Response(
            content=content,
            media_type="application/x-tmx+xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TMX 导出失败: {str(e)}") from e


@router.post("/export/xliff")
async def export_xliff(request: XliffExportRequest):
    """导出为 XLIFF 格式"""
    try:
        exporter = XliffExporter(
            source_lang=request.source_lang,
            target_lang=request.target_lang,
            version=request.version,
        )
        original_format = "plaintext"
        if request.filename:
            ext = request.filename.rsplit(".", 1)[-1].lower()
            format_map = {
                "docx": "winword", "pdf": "pdf", "pptx": "powerpoint",
                "txt": "plaintext", "xml": "xml", "dita": "xml",
            }
            original_format = format_map.get(ext, "plaintext")
        content = exporter.export(request.segments, request.filename or "document", original_format)
        filename = "export.xlf"
        if request.filename:
            base_name = request.filename.rsplit(".", 1)[0]
            filename = f"{base_name}.xlf"
        return Response(
            content=content,
            media_type="application/xliff+xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"XLIFF 导出失败: {str(e)}") from e


@router.post("/import/xliff")
async def import_xliff(file: UploadFile = File(...)):
    """导入 XLIFF 文件"""
    if not file.filename or not file.filename.lower().endswith((".xlf", ".xliff")):
        raise HTTPException(status_code=400, detail="请上传 XLIFF 文件 (.xlf 或 .xliff)")
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空")
    try:
        importer = XliffImporter()
        segments = importer.import_xliff(raw_bytes)
        return {"filename": file.filename, "segments": segments, "count": len(segments)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"XLIFF 导入失败: {str(e)}") from e


# ========== 文档管理 API ==========

@router.post("/file-records")
@router.post("/documents", include_in_schema=False)
async def create_file_record(
    file: UploadFile = File(...),
    threshold: float = Form(default=0.6),
    collection_ids: list[UUID] | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """上传文档并创建持久化记录"""
    _validate_docx_upload(file)

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")

    selected_collection_ids = _validate_collection_ids(db, collection_ids)
    try:
        file_record = create_file_record_with_segments(
            db=db,
            raw_bytes=raw_bytes,
            filename=file.filename or "untitled.docx",
            similarity_threshold=threshold,
            collection_ids=selected_collection_ids,
        )
        return {
            "id": file_record.id,
            "filename": file_record.filename,
            "status": file_record.status,
            "created_at": file_record.created_at.isoformat(),
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/file-records")
@router.get("/documents", include_in_schema=False)
def get_file_records(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """获取文档列表"""
    file_records = list_file_records(db, skip=skip, limit=limit)
    return [
        {
            "id": file_record.id,
            "filename": file_record.filename,
            "status": file_record.status,
            "created_at": file_record.created_at.isoformat(),
            "updated_at": file_record.updated_at.isoformat(),
        }
        for file_record in file_records
    ]


@router.get("/file-records/{file_record_id}")
@router.get("/documents/{file_record_id}", include_in_schema=False)
def get_file_record(
    file_record_id: UUID,
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """获取文档详情及片段，支持分页"""
    safe_skip = max(skip, 0)
    safe_limit = min(max(limit, 1), 1000)
    result = get_file_record_with_segments(
        db,
        file_record_id,
        skip=safe_skip,
        limit=safe_limit,
    )
    if not result:
        raise HTTPException(status_code=404, detail="文档不存在。")

    file_record = result["file_record"]
    segments = result["segments"]

    return {
        "id": file_record.id,
        "filename": file_record.filename,
        "status": file_record.status,
        "created_at": file_record.created_at.isoformat(),
        "updated_at": file_record.updated_at.isoformat(),
        "total_segments": result["total_segments"],
        "skip": result["skip"],
        "limit": result["limit"],
        "segments": [
            {
                "id": seg.id,
                "sentence_id": seg.sentence_id,
                "source_text": seg.source_text,
                "display_text": seg.display_text,
                "target_text": seg.target_text,
                "status": seg.status,
                "score": seg.score,
                "matched_source_text": seg.matched_source_text,
                "source": seg.source,
                "block_type": seg.block_type,
                "block_index": seg.block_index,
                "row_index": seg.row_index,
                "cell_index": seg.cell_index,
            }
            for seg in segments
        ],
    }


@router.get("/file-records/{file_record_id}/preview")
@router.get("/documents/{file_record_id}/preview", include_in_schema=False)
def get_file_record_preview(
    file_record_id: UUID,
    db: Session = Depends(get_db),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    source_bytes = load_file_record_source(file_record)
    if source_bytes:
        preview_html = build_docx_preview_html(source_bytes)
    else:
        segments = list_segments_for_file_record(db, file_record_id)
        preview_html = build_document_html_from_segments(segments) if segments else ""

    return {
        "id": file_record.id,
        "filename": file_record.filename,
        "supports_preview": bool(preview_html),
        "preview_html": preview_html,
    }


@router.get("/file-records/{file_record_id}/export-docx")
@router.get("/documents/{file_record_id}/export-docx", include_in_schema=False)
def export_file_record_docx(
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


@router.get("/file-records/{file_record_id}/segments/{sentence_id}/tm-candidates")
def get_segment_tm_candidates(
    file_record_id: UUID,
    sentence_id: str,
    threshold: float = 0.6,
    max_candidates: int = 5,
    db: Session = Depends(get_db),
):
    """获取指定句段的 TM 匹配候选项"""
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    segment = (
        db.query(Segment)
        .filter(Segment.file_record_id == file_record_id, Segment.sentence_id == sentence_id)
        .first()
    )
    if not segment:
        raise HTTPException(status_code=404, detail="句段不存在。")

    candidates = get_tm_candidates(
        db=db,
        source_text=segment.source_text,
        similarity_threshold=threshold,
        max_candidates=max_candidates,
    )

    return {
        "sentence_id": sentence_id,
        "source_text": segment.source_text,
        "candidates": [
            {
                "source_text": c.source_text,
                "target_text": c.target_text,
                "score": c.score,
                "diff_html": c.diff_html,
            }
            for c in candidates
        ],
    }


@router.put("/file-records/{file_record_id}/segments/{sentence_id}")
@router.put("/documents/{file_record_id}/segments/{sentence_id}", include_in_schema=False)
def update_segment(
    file_record_id: UUID,
    sentence_id: str,
    update: SegmentUpdate,
    db: Session = Depends(get_db),
):
    """更新单个片段的译文"""
    segment = update_segment_by_sentence_id(
        db=db,
        file_record_id=file_record_id,
        sentence_id=sentence_id,
        target_text=update.target_text,
        source=update.source,
    )
    if not segment:
        raise HTTPException(status_code=404, detail="片段不存在。")

    return {
        "id": segment.id,
        "sentence_id": segment.sentence_id,
        "target_text": segment.target_text,
        "status": segment.status,
        "source": segment.source,
    }


@router.put("/file-records/{file_record_id}/segments")
@router.put("/documents/{file_record_id}/segments", include_in_schema=False)
def batch_update(
    file_record_id: UUID,
    batch: BatchSegmentUpdate,
    db: Session = Depends(get_db),
):
    """批量更新片段译文"""
    updated_count = batch_update_segments(
        db=db,
        file_record_id=file_record_id,
        updates=[u.model_dump() for u in batch.updates],
    )
    return {"updated_count": updated_count}


@router.get("/file-records/{file_record_id}/comments")
@router.get("/documents/{file_record_id}/comments", include_in_schema=False)
def get_file_record_comments(
    file_record_id: UUID,
    db: Session = Depends(get_db),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    comments = list_segment_comments_for_file_record(db, file_record_id)
    return [serialize_segment_comment(comment) for comment in comments]


@router.post("/file-records/{file_record_id}/comments")
@router.post("/documents/{file_record_id}/comments", include_in_schema=False)
def create_file_record_comment(
    file_record_id: UUID,
    payload: CommentCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    comment = create_segment_comment(
        db,
        file_record_id=file_record_id,
        sentence_id=payload.sentence_id,
        segment_id=payload.segment_id,
        anchor_mode=payload.anchor_mode,
        range_start_offset=payload.range_start_offset,
        range_end_offset=payload.range_end_offset,
        anchor_text=payload.anchor_text,
        body=payload.body,
        author=current_user,
    )
    return serialize_segment_comment(comment)


@router.patch("/comments/{comment_id}")
def patch_comment(
    comment_id: UUID,
    payload: CommentUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    comment = update_segment_comment(
        db,
        comment_id=comment_id,
        body=payload.body,
        status=payload.status,
        current_user=current_user,
    )
    return serialize_segment_comment(comment)


@router.delete("/comments/{comment_id}")
def remove_comment(
    comment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    delete_segment_comment(
        db,
        comment_id=comment_id,
        current_user=current_user,
    )
    return {"message": "批注已删除。"}


@router.post("/comments/{comment_id}/replies")
def create_comment_reply(
    comment_id: UUID,
    payload: CommentReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    comment = create_segment_comment_reply(
        db,
        comment_id=comment_id,
        body=payload.body,
        author=current_user,
    )
    return serialize_segment_comment(comment)


@router.post("/file-records/{file_record_id}/llm-translate")
@router.post("/documents/{file_record_id}/llm-translate", include_in_schema=False)
async def llm_translate_file_record(
    file_record_id: UUID,
    request: Request,
    payload: LLMTranslateRequest | None = None,
    db: Session = Depends(get_db),
):
    """对指定范围的片段触发 LLM 译文修正，并通过 SSE 逐条返回结果。"""
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    body = payload or LLMTranslateRequest()
    try:
        validate_provider_choice(body.provider)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    translation_tasks = _build_llm_translation_tasks(
        db=db,
        file_record_id=file_record_id,
        scope=body.scope,
    )

    async def event_stream():
        updated_count = 0
        error_count = 0
        total_count = len(translation_tasks)

        yield _sse_event(
            "start",
            {
                "file_record_id": str(file_record_id),
                "scope": body.scope,
                "provider": body.provider,
                "total": total_count,
            },
        )

        if total_count == 0:
            yield _sse_event(
                "complete",
                {
                    "file_record_id": str(file_record_id),
                    "updated_count": 0,
                    "error_count": 0,
                    "total": 0,
                },
            )
            return

        async for result in iter_batch_translate(
            translation_tasks,
            provider=body.provider,
        ):
            if await request.is_disconnected():
                break

            if isinstance(result, LLMTranslationFailure):
                error_count += 1
                yield _sse_event(
                    "error",
                    {
                        "sentence_id": result.sentence_id,
                        "status": result.status,
                        "message": result.error_message,
                    },
                )
                continue

            try:
                segment = update_segment_with_llm_result(
                    db=db,
                    file_record_id=file_record_id,
                    sentence_id=result.sentence_id,
                    target_text=result.translated_text,
                )
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                error_count += 1
                yield _sse_event(
                    "error",
                    {
                        "sentence_id": result.sentence_id,
                        "status": result.status,
                        "message": f"数据库更新失败：{exc}",
                    },
                )
                continue

            if not segment:
                error_count += 1
                yield _sse_event(
                    "error",
                    {
                        "sentence_id": result.sentence_id,
                        "status": result.status,
                        "message": "片段不存在，无法写回 LLM 译文。",
                    },
                )
                continue

            updated_count += 1
            yield _sse_event(
                "segment",
                {
                    "sentence_id": segment.sentence_id,
                    "target_text": segment.target_text,
                    "status": segment.status,
                    "source": segment.source,
                    "provider": result.provider,
                    "model": result.model,
                },
            )

        if not await request.is_disconnected():
            yield _sse_event(
                "complete",
                {
                    "file_record_id": str(file_record_id),
                    "updated_count": updated_count,
                    "error_count": error_count,
                    "total": total_count,
                },
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/file-records/{file_record_id}")
@router.delete("/documents/{file_record_id}", include_in_schema=False)
def remove_file_record(
    file_record_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """删除文档及其所有片段"""
    success = delete_file_record(db, file_record_id)
    if not success:
        raise HTTPException(status_code=404, detail="文档不存在。")
    return {"message": "文档已删除。"}


# ========== TM 管理 API ==========

class TMEntry(BaseModel):
    source_text: str
    target_text: str
    collection_id: UUID | None = None


class BatchTMEntry(BaseModel):
    collection_id: UUID | None = None
    entries: list[TMEntry]


@router.get("/tm/collections")
def list_tm_collections(
    db: Session = Depends(get_db),
):
    rows = (
        db.query(TMCollection, func.count(TranslationMemory.id).label("entry_count"))
        .outerjoin(TranslationMemory, TranslationMemory.collection_id == TMCollection.id)
        .group_by(TMCollection.id)
        .order_by(TMCollection.created_at.desc())
        .all()
    )
    return [
        _serialize_tm_collection(collection, int(entry_count))
        for collection, entry_count in rows
    ]


@router.post("/tm/collections")
def create_tm_collection(
    payload: TMCollectionPayload,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    name = _normalize_collection_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="记忆库名称不能为空。")

    collection = TMCollection(
        name=name,
        description=normalize_text(payload.description or "") or None,
    )
    db.add(collection)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名记忆库已存在。") from exc

    db.refresh(collection)
    return _serialize_tm_collection(collection)


@router.put("/tm/collections/{collection_id}")
def update_tm_collection(
    collection_id: UUID,
    payload: TMCollectionPayload,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    collection = _get_collection_or_404(db, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="TM 记忆库不存在。")

    name = _normalize_collection_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="记忆库名称不能为空。")

    collection.name = name
    collection.description = normalize_text(payload.description or "") or None
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名记忆库已存在。") from exc

    db.refresh(collection)
    entry_count = (
        db.query(TranslationMemory)
        .filter(TranslationMemory.collection_id == collection.id)
        .count()
    )
    return _serialize_tm_collection(collection, entry_count)


@router.delete("/tm/collections/{collection_id}")
def delete_tm_collection(
    collection_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    collection = _get_collection_or_404(db, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="TM 记忆库不存在。")

    entry_count = (
        db.query(TranslationMemory)
        .filter(TranslationMemory.collection_id == collection.id)
        .count()
    )
    if entry_count:
        raise HTTPException(status_code=409, detail="请先清空该记忆库中的 TM 记录。")

    db.delete(collection)
    db.commit()
    return {"message": "记忆库已删除。"}


@router.post("/tm/import-xlsx")
async def import_tm_xlsx(
    file: UploadFile = File(...),
    collection_id: UUID | None = Form(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    extension = f".{(file.filename or '').split('.')[-1].lower()}" if file.filename else ""
    if extension not in XLSX_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持上传 .xlsx 文件。")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="上传的 XLSX 文件为空。")

    collection = _get_collection_or_404(db, collection_id)
    try:
        import_summary = import_tm_from_xlsx_upload(
            db=db,
            raw_bytes=raw_bytes,
            filename=file.filename or "uploaded.xlsx",
            collection_id=collection_id,
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"TM 导入失败：{exc}") from exc

    return {
        "filename": import_summary.filename,
        "created_rows": import_summary.created_rows,
        "updated_rows": import_summary.updated_rows,
        "skipped_empty_rows": import_summary.skipped_empty_rows,
        "skipped_header_rows": import_summary.skipped_header_rows,
        "imported_rows": import_summary.imported_rows,
        "collection_id": collection.id if collection else None,
        "collection_name": collection.name if collection else None,
    }


@router.post("/tm/add")
def add_tm_entry(
    entry: TMEntry,
    db: Session = Depends(get_db),
):
    """添加单条 TM 记录（去重：相同原文不重复添加）"""
    source_text = normalize_text(entry.source_text)
    target_text = normalize_text(entry.target_text)

    if not source_text or not target_text:
        raise HTTPException(status_code=400, detail="原文和译文不能为空。")

    _get_collection_or_404(db, entry.collection_id)
    source_hash = build_source_hash(source_text)

    # 检查是否已存在
    existing_query = db.query(TranslationMemory).filter(
        TranslationMemory.source_hash == source_hash
    )
    existing = _filter_tm_collection(existing_query, entry.collection_id).first()

    if existing:
        # 已存在，更新译文
        existing.source_text = source_text
        existing.target_text = target_text
        existing.source_hash = source_hash
        existing.source_normalized = normalize_match_text(source_text) or source_text
        db.commit()
        sync_tm_embeddings(db, [(existing.id, existing.source_text)])
        return {"status": "updated", "id": existing.id, "message": "已更新现有记录。"}

    # 不存在，新增
    tm = TranslationMemory(
        collection_id=entry.collection_id,
        source_text=source_text,
        target_text=target_text,
        source_hash=source_hash,
        source_normalized=normalize_match_text(source_text) or source_text,
    )
    db.add(tm)
    db.commit()
    db.refresh(tm)
    sync_tm_embeddings(db, [(tm.id, tm.source_text)])

    return {"status": "created", "id": tm.id, "message": "已添加新记录。"}


@router.post("/tm/batch-add")
def batch_add_tm_entries(
    batch: BatchTMEntry,
    db: Session = Depends(get_db),
):
    """批量添加 TM 记录（去重）"""
    created_count = 0
    updated_count = 0
    skipped_count = 0
    sync_candidates: list[TranslationMemory] = []
    collection_ids = [
        collection_id
        for collection_id in (
            [batch.collection_id]
            + [entry.collection_id for entry in batch.entries]
        )
        if collection_id is not None
    ]
    _validate_collection_ids(db, collection_ids)

    for entry in batch.entries:
        source_text = normalize_text(entry.source_text)
        target_text = normalize_text(entry.target_text)
        collection_id = entry.collection_id or batch.collection_id

        if not source_text or not target_text:
            skipped_count += 1
            continue

        source_hash = build_source_hash(source_text)

        existing_query = db.query(TranslationMemory).filter(
            TranslationMemory.source_hash == source_hash
        )
        existing = _filter_tm_collection(existing_query, collection_id).first()

        if existing:
            existing.source_text = source_text
            existing.target_text = target_text
            existing.source_hash = source_hash
            existing.source_normalized = normalize_match_text(source_text) or source_text
            existing.collection_id = collection_id
            sync_candidates.append(existing)
            updated_count += 1
        else:
            tm = TranslationMemory(
                collection_id=collection_id,
                source_text=source_text,
                target_text=target_text,
                source_hash=source_hash,
                source_normalized=normalize_match_text(source_text) or source_text,
            )
            db.add(tm)
            sync_candidates.append(tm)
            created_count += 1

    sync_rows: list[tuple[UUID, str]] = []
    if sync_candidates:
        db.flush()
        sync_rows = list(
            {
                row.id: row.source_text
                for row in sync_candidates
                if row.id is not None and row.source_text
            }.items()
        )
        db.commit()

    sync_tm_embeddings(db, sync_rows)

    return {
        "created": created_count,
        "updated": updated_count,
        "skipped": skipped_count,
    }


# ========== 术语库管理 API ==========

def _serialize_termbase_collection(collection: TermbaseCollection, entry_count: int = 0) -> dict:
    return {
        "id": collection.id,
        "name": collection.name,
        "description": collection.description,
        "created_at": collection.created_at.isoformat(),
        "updated_at": collection.updated_at.isoformat(),
        "entry_count": entry_count,
    }


def _get_termbase_collection_or_404(db: Session, collection_id: UUID | None) -> TermbaseCollection | None:
    if collection_id is None:
        return None

    collection = db.query(TermbaseCollection).filter(TermbaseCollection.id == collection_id).first()
    if collection is None:
        raise HTTPException(status_code=404, detail="术语库不存在。")
    return collection


@router.get("/termbase/collections")
def list_termbase_collections(
    db: Session = Depends(get_db),
):
    rows = (
        db.query(TermbaseCollection, func.count(Term.id).label("entry_count"))
        .outerjoin(Term, Term.collection_id == TermbaseCollection.id)
        .group_by(TermbaseCollection.id)
        .order_by(TermbaseCollection.created_at.desc())
        .all()
    )
    return [
        _serialize_termbase_collection(collection, int(entry_count))
        for collection, entry_count in rows
    ]


@router.post("/termbase/collections")
def create_termbase_collection(
    payload: TermbaseCollectionPayload,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    name = _normalize_collection_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="术语库名称不能为空。")

    collection = TermbaseCollection(
        name=name,
        description=normalize_text(payload.description or "") or None,
    )
    db.add(collection)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名术语库已存在。") from exc

    db.refresh(collection)
    return _serialize_termbase_collection(collection)


@router.delete("/termbase/collections/{collection_id}")
def delete_termbase_collection(
    collection_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    collection = _get_termbase_collection_or_404(db, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="术语库不存在。")

    entry_count = (
        db.query(Term)
        .filter(Term.collection_id == collection.id)
        .count()
    )
    if entry_count:
        raise HTTPException(status_code=409, detail="请先清空该术语库中的术语记录。")

    db.delete(collection)
    db.commit()
    return {"message": "术语库已删除。"}


@router.get("/termbase/terms")
def list_terms(
    collection_id: UUID | None = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    query = db.query(Term)
    if collection_id:
        query = query.filter(Term.collection_id == collection_id)
    
    total = query.count()
    terms = query.order_by(Term.source_text).offset(skip).limit(limit).all()
    
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "terms": [
            {
                "id": term.id,
                "source_text": term.source_text,
                "target_text": term.target_text,
                "collection_id": term.collection_id,
                "created_at": term.created_at.isoformat(),
            }
            for term in terms
        ],
    }


@router.post("/termbase/terms")
def add_term(
    payload: TermPayload,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    source_text = normalize_text(payload.source_text)
    target_text = normalize_text(payload.target_text)

    if not source_text or not target_text:
        raise HTTPException(status_code=400, detail="原文和译文不能为空。")

    _get_termbase_collection_or_404(db, payload.collection_id)

    # 检查是否已存在相同原文的术语
    existing = (
        db.query(Term)
        .filter(Term.source_text == source_text, Term.collection_id == payload.collection_id)
        .first()
    )

    if existing:
        existing.target_text = target_text
        db.commit()
        return {"status": "updated", "id": existing.id, "message": "已更新现有术语。"}

    term = Term(
        collection_id=payload.collection_id,
        source_text=source_text,
        target_text=target_text,
    )
    db.add(term)
    db.commit()
    db.refresh(term)

    return {"status": "created", "id": term.id, "message": "已添加新术语。"}


@router.delete("/termbase/terms/{term_id}")
def delete_term(
    term_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    term = db.query(Term).filter(Term.id == term_id).first()
    if not term:
        raise HTTPException(status_code=404, detail="术语不存在。")

    db.delete(term)
    db.commit()
    return {"message": "术语已删除。"}


@router.post("/termbase/import-xlsx")
async def import_termbase_xlsx(
    file: UploadFile = File(...),
    collection_id: UUID | None = Form(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    extension = f".{(file.filename or '').split('.')[-1].lower()}" if file.filename else ""
    if extension not in XLSX_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持上传 .xlsx 文件。")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="上传的 XLSX 文件为空。")

    collection = _get_termbase_collection_or_404(db, collection_id)
    
    try:
        from openpyxl import load_workbook
        from io import BytesIO
        
        wb = load_workbook(filename=BytesIO(raw_bytes), read_only=True, data_only=True)
        ws = wb.active
        
        created_count = 0
        updated_count = 0
        skipped_count = 0
        
        for row_idx, row in enumerate(ws.iter_rows(min_row=1, values_only=True), start=1):
            if not row or len(row) < 2:
                skipped_count += 1
                continue
            
            source_text = normalize_text(str(row[0] or ""))
            target_text = normalize_text(str(row[1] or ""))
            
            if not source_text or not target_text:
                skipped_count += 1
                continue
            
            # 跳过表头
            if row_idx == 1 and (source_text.lower() in ("source", "原文", "术语") or target_text.lower() in ("target", "译文", "翻译")):
                skipped_count += 1
                continue
            
            existing = (
                db.query(Term)
                .filter(Term.source_text == source_text, Term.collection_id == collection_id)
                .first()
            )
            
            if existing:
                existing.target_text = target_text
                updated_count += 1
            else:
                term = Term(
                    collection_id=collection_id,
                    source_text=source_text,
                    target_text=target_text,
                )
                db.add(term)
                created_count += 1
        
        db.commit()
        wb.close()
        
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"术语库导入失败：{exc}") from exc

    return {
        "filename": file.filename,
        "created_rows": created_count,
        "updated_rows": updated_count,
        "skipped_rows": skipped_count,
        "imported_rows": created_count + updated_count,
        "collection_id": collection.id if collection else None,
        "collection_name": collection.name if collection else None,
    }


@router.get("/termbase/match")
def match_terms(
    text: str,
    collection_ids: list[UUID] | None = None,
    db: Session = Depends(get_db),
):
    """匹配文本中的术语，返回匹配到的术语列表（长术语优先）"""
    if not text:
        return {"matches": []}
    
    query = db.query(Term)
    if collection_ids:
        query = query.filter(Term.collection_id.in_(collection_ids))
    
    all_terms = query.all()
    
    # 按原文长度降序排序（长术语优先）
    sorted_terms = sorted(all_terms, key=lambda t: len(t.source_text), reverse=True)
    
    matches = []
    matched_positions = set()
    text_lower = text.lower()
    
    for term in sorted_terms:
        term_lower = term.source_text.lower()
        start = 0
        while True:
            pos = text_lower.find(term_lower, start)
            if pos == -1:
                break
            
            end_pos = pos + len(term.source_text)
            # 检查是否与已匹配的位置重叠
            overlap = False
            for matched_start, matched_end in matched_positions:
                if not (end_pos <= matched_start or pos >= matched_end):
                    overlap = True
                    break
            
            if not overlap:
                matched_positions.add((pos, end_pos))
                matches.append({
                    "term_id": str(term.id),
                    "source_text": term.source_text,
                    "target_text": term.target_text,
                    "start": pos,
                    "end": end_pos,
                })
            
            start = pos + 1
    
    # 按位置排序
    matches.sort(key=lambda m: m["start"])
    
    return {"matches": matches}
