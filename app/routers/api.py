"""
API 路由模块 - 文件上传、解析和导出接口

支持多种文档格式的上传、解析和导出。
"""
import json
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Literal, Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user, get_user_display_name, require_admin
from app.database import get_db
from app.models import (
    FileRecord,
    MemoryBase,
    MemoryEntry,
    Segment,
    TMCollection,
    TermBase,
    TermEntry,
    TranslationMemory,
    User,
)
from app.services.adapters import (
    DocumentAST,
    ExportService,
    FileTooLargeError,
    ParseError,
    UnsupportedFormatError,
    get_registry,
)
from app.services.adapters.dita_exporter import DitaExporter
from app.services.adapters.svg_exporter import SvgExporter
from app.services.adapters.tmx_exporter import TmxExporter
from app.services.adapters.xliff_exporter import XliffExporter, XliffImporter
from app.services.comment_service import (
    create_segment_comment,
    create_segment_comment_reply,
    delete_segment_comment,
    list_segment_comments_for_file_record,
    serialize_segment_comment,
    update_segment_comment,
)
from app.services.file_record_service import (
    attach_source_document_to_file_record,
    batch_update_segments,
    calculate_file_record_progress,
    create_file_record_with_segments,
    delete_file_record,
    get_file_record as get_file_record_model,
    get_file_record_source_filename,
    get_file_record_with_segments,
    get_tm_target_text_map,
    list_file_records,
    list_segments_for_file_record,
    load_file_record_source,
    resolve_file_record_status,
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
from app.services.language_pairs import require_language_pair
from app.services.matcher import get_tm_candidates_for_text
from app.services.normalizer import build_source_hash, normalize_match_text, normalize_text
from app.services.revision_service import (
    accept_revision,
    batch_accept_revisions,
    batch_reject_revisions,
    list_revisions,
    reject_revision,
    serialize_segment_revision,
)
from app.services.slate_parser import parse_docx_for_slate
from app.services.task_file_service import (
    build_task_preview_html,
    build_task_workspace,
    can_export_task_file,
    export_translated_task_file,
    get_supported_task_extensions,
    get_task_file_extension,
    supports_task_file,
)
from app.services.tm_importer import (
    SDLTM_EXTENSIONS,
    TM_IMPORT_EXTENSIONS,
    XLSX_EXTENSIONS,
    import_tm_from_sdltm_upload,
    import_tm_from_xlsx_upload,
    preview_sdltm_metadata,
)
from app.services.tm_vector import sync_tm_embeddings
from app.services.xlsx_exporter import build_tabular_xlsx, build_xlsx_download_response


router = APIRouter(dependencies=[Depends(get_current_user)])


class SegmentUpdate(BaseModel):
    sentence_id: str
    target_text: str
    source: str = "manual"


class BatchSegmentUpdate(BaseModel):
    updates: list[SegmentUpdate]


class RevisionResolvePayload(BaseModel):
    status: Literal["accepted", "rejected"]


class LLMTranslateRequest(BaseModel):
    scope: Literal["fuzzy_only", "none_only", "all", "all_with_exact"] = "all"
    provider: Literal["auto", "deepseek", "openrouter"] = "deepseek"


class MemoryBasePayload(BaseModel):
    name: str
    description: str | None = None
    source_language: str
    target_language: str


class TermBasePayload(BaseModel):
    name: str
    description: str | None = None
    source_language: str = "zh"
    target_language: str = "en"


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


class ProjectCreatePayload(BaseModel):
    name: str
    source_language: str
    target_language: str
    deadline: str | None = None
    access_level: Literal["team", "private", "public"] = "team"
    collection_id: UUID | None = None
    term_base_id: UUID | None = None


class ProjectUpdatePayload(BaseModel):
    name: str | None = None
    source_language: str | None = None
    target_language: str | None = None
    deadline: str | None = None
    access_level: Literal["team", "private", "public"] | None = None


def _build_binary_download_response(
    filename: str,
    content: bytes,
    media_type: str,
) -> StreamingResponse:
    ascii_filename = filename.encode("ascii", "ignore").decode("ascii").strip() or "translated.bin"
    ascii_filename = ascii_filename.replace('"', "")
    quoted_filename = quote(filename)

    return StreamingResponse(
        BytesIO(content),
        media_type=media_type,
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
    source_language: str | None = None,
    target_language: str | None = None,
    collection_id: UUID | None = None,
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
        collection_id=collection_id,
        source_language=source_language,
        target_language=target_language,
    )

    tasks: list[LLMTranslationTask] = []
    for segment in segments:
        if segment.status not in target_statuses:
            continue

        segment_tm_target_text = segment.target_text if segment.source == "tm" else ""
        tm_target_text = segment_tm_target_text or tm_target_text_map.get(segment.matched_source_text or "", "")

        tasks.append(
            LLMTranslationTask(
                sentence_id=segment.sentence_id,
                status=segment.status,
                source_text=segment.source_text,
                source_language=source_language,
                target_language=target_language,
                block_type=segment.block_type,
                matched_source_text=segment.matched_source_text,
                tm_target_text=tm_target_text,
            )
        )

    return tasks

# 支持的文件扩展名（30种格式）
SUPPORTED_EXTENSIONS = {
    # 办公文档
    ".docx", ".txt", ".pdf", ".pptx", ".xlsx",
    # 本地化文件
    ".properties", ".po", ".pot", ".strings", ".yaml", ".yml", ".json", ".php",
    # 网页/排版
    ".html", ".htm", ".md", ".markdown", ".csv", ".srt",
    # 技术写作
    ".dita", ".ditamap", ".xml", ".svg",
    # 双语文件
    ".sdlxliff", ".txml",
    # 工程/设计
    ".dxf", ".idml", ".mif",
    # 压缩包
    ".zip", ".rar",
}


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


def _validate_task_upload(file: UploadFile) -> None:
    if supports_task_file(file.filename or ""):
        return

    supported_extensions = ", ".join(get_supported_task_extensions())
    raise HTTPException(
        status_code=400,
        detail=f"暂不支持该文件格式。当前支持：{supported_extensions}",
    )


def _normalize_collection_name(name: str) -> str:
    return " ".join(name.strip().split())


def _require_tm_language_pair(
    source_language: str | None,
    target_language: str | None,
) -> tuple[str, str]:
    try:
        return require_language_pair(source_language, target_language)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _resolve_collection_language_pair(
    collection: TMCollection | None,
    source_language: str | None,
    target_language: str | None,
) -> tuple[str, str]:
    if collection and collection.source_language and collection.target_language:
        if source_language or target_language:
            normalized_source_language, normalized_target_language = _require_tm_language_pair(
                source_language,
                target_language,
            )
            if (
                normalized_source_language != collection.source_language
                or normalized_target_language != collection.target_language
            ):
                raise HTTPException(status_code=400, detail="所选记忆库的语言对与本次提交不一致。")
        return collection.source_language, collection.target_language

    normalized_source_language, normalized_target_language = _require_tm_language_pair(
        source_language,
        target_language,
    )
    if collection is not None:
        collection.source_language = normalized_source_language
        collection.target_language = normalized_target_language
    return normalized_source_language, normalized_target_language


def _resolve_file_record_language_pair(file_record: FileRecord) -> tuple[str, str]:
    source_language = file_record.source_language
    target_language = file_record.target_language

    if (not source_language or not target_language) and file_record.collection:
        source_language = source_language or file_record.collection.source_language
        target_language = target_language or file_record.collection.target_language

    try:
        return require_language_pair(source_language, target_language)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="当前项目缺少源语言或目标语言，请先在项目任务中设置语言对。",
        ) from exc


def _apply_collection_language_pair(file_record: FileRecord, collection: TMCollection | None) -> None:
    if not collection:
        return
    if not file_record.source_language:
        file_record.source_language = collection.source_language
    if not file_record.target_language:
        file_record.target_language = collection.target_language


def _ensure_resource_language_pair_matches(
    resource,
    source_language: str,
    target_language: str,
    resource_label: str,
) -> None:
    resource_source_language = getattr(resource, "source_language", None)
    resource_target_language = getattr(resource, "target_language", None)
    if not resource_source_language or not resource_target_language:
        return
    if (
        resource_source_language != source_language
        or resource_target_language != target_language
    ):
        raise HTTPException(status_code=400, detail=f"所选{resource_label}的语言对与项目任务语言对不一致。")


def _validate_collection_ids(
    db: Session,
    collection_ids: list[UUID] | None,
) -> list[UUID] | None:
    if not collection_ids:
        return None

    normalized_ids = list(dict.fromkeys(collection_ids))
    existing_collections = (
        db.query(MemoryBase)
        .filter(MemoryBase.id.in_(normalized_ids))
        .all()
    )
    existing_ids = {collection.id for collection in existing_collections}
    missing_ids = [collection_id for collection_id in normalized_ids if collection_id not in existing_ids]
    if missing_ids:
        raise HTTPException(status_code=404, detail="选择的 TM 记忆库不存在。")

    return normalized_ids


def _require_selected_collection_ids(
    collection_ids: list[UUID] | None,
) -> list[UUID]:
    if not collection_ids:
        raise HTTPException(
            status_code=400,
            detail="请至少选择一个 TM 记忆库，避免全库模糊匹配拖慢处理进程。",
        )
    return collection_ids


def _get_collection_or_404(db: Session, collection_id: UUID | None) -> TMCollection | None:
    if collection_id is None:
        return None

    collection = db.query(MemoryBase).filter(MemoryBase.id == collection_id).first()
    if collection is None:
        raise HTTPException(status_code=404, detail="TM 记忆库不存在。")
    return collection


def _filter_tm_collection(
    query,
    collection_id: UUID | None,
    source_language: str | None = None,
    target_language: str | None = None,
):
    if collection_id is None:
        query = query.filter(TranslationMemory.collection_id.is_(None))
    else:
        query = query.filter(TranslationMemory.collection_id == collection_id)
    if source_language:
        query = query.filter(TranslationMemory.source_language == source_language)
    if target_language:
        query = query.filter(TranslationMemory.target_language == target_language)
    return query


def _serialize_tm_collection(collection: MemoryBase, entry_count: int = 0) -> dict:
    return {
        "id": collection.id,
        "name": collection.name,
        "description": collection.description,
        "source_language": collection.source_language,
        "target_language": collection.target_language,
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
    required_collection_ids = _require_selected_collection_ids(selected_collection_ids)
    try:
        result = parse_docx_for_slate(
            db=db,
            raw_bytes=raw_bytes,
            similarity_threshold=threshold,
            collection_ids=required_collection_ids,
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
    _validate_task_upload(file)

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")

    selected_collection_ids = _validate_collection_ids(db, collection_ids)
    required_collection_ids = _require_selected_collection_ids(selected_collection_ids)
    try:
        return build_task_workspace(
            db=db,
            raw_bytes=raw_bytes,
            filename=file.filename or "untitled.txt",
            similarity_threshold=threshold,
            collection_ids=required_collection_ids,
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
    term_base_id: UUID | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """上传文档并创建持久化记录"""
    _validate_task_upload(file)

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")

    selected_collection_ids = _validate_collection_ids(db, collection_ids)
    required_collection_ids = _require_selected_collection_ids(selected_collection_ids)
    primary_collection = _get_collection_or_404(db, required_collection_ids[0])

    # 验证术语库是否存在
    term_base = None
    if term_base_id is not None:
        term_base = db.query(TermBase).filter(TermBase.id == term_base_id).first()
        if term_base is None:
            raise HTTPException(status_code=404, detail="术语库不存在。")
        _ensure_resource_language_pair_matches(
            term_base,
            primary_collection.source_language,
            primary_collection.target_language,
            "术语库",
        )

    try:
        workspace_data = build_task_workspace(
            db=db,
            raw_bytes=raw_bytes,
            filename=file.filename or "untitled.txt",
            similarity_threshold=threshold,
            collection_ids=required_collection_ids,
        )
        file_record = create_file_record_with_segments(
            db=db,
            raw_bytes=raw_bytes,
            filename=file.filename or "untitled.txt",
            similarity_threshold=threshold,
            workspace_data=workspace_data,
            collection_ids=required_collection_ids,
        )
        # 写入绑定关系
        if required_collection_ids:
            file_record.collection_id = required_collection_ids[0]
            _apply_collection_language_pair(file_record, primary_collection)
        if term_base is not None:
            file_record.term_base_id = term_base_id
        db.commit()
        return {
            "id": file_record.id,
            "filename": file_record.filename,
            "status": file_record.status,
            "created_at": file_record.created_at.isoformat(),
        }
    except (UnsupportedFormatError, FileTooLargeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/projects")
def create_project(
    payload: ProjectCreatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """仅填写基础信息创建项目，文档导入在项目详情页完成"""
    from datetime import datetime as _dt

    deadline_dt = None
    if payload.deadline:
        try:
            deadline_dt = _dt.fromisoformat(payload.deadline)
        except ValueError:
            raise HTTPException(status_code=400, detail="截止期限格式不正确，请使用 ISO 格式。")

    # 验证库是否存在
    collection = None
    if payload.collection_id is not None:
        collection = db.query(TMCollection).filter(TMCollection.id == payload.collection_id).first()
        if collection is None:
            raise HTTPException(status_code=404, detail="记忆库不存在。")
    term_base = None
    if payload.term_base_id is not None:
        term_base = db.query(TermBase).filter(TermBase.id == payload.term_base_id).first()
        if term_base is None:
            raise HTTPException(status_code=404, detail="术语库不存在。")

    source_language, target_language = _require_tm_language_pair(
        payload.source_language,
        payload.target_language,
    )
    _ensure_resource_language_pair_matches(collection, source_language, target_language, "记忆库")
    _ensure_resource_language_pair_matches(term_base, source_language, target_language, "术语库")

    project = FileRecord(
        filename=payload.name.strip(),
        status="draft",
        source_language=source_language,
        target_language=target_language,
        creator_id=current_user.id,
        deadline=deadline_dt,
        access_level=payload.access_level,
        collection_id=payload.collection_id,
        term_base_id=payload.term_base_id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    return {
        "id": str(project.id),
        "filename": project.filename,
        "status": project.status,
        "source_language": project.source_language,
        "target_language": project.target_language,
        "creator": get_user_display_name(current_user),
        "deadline": project.deadline.isoformat() if project.deadline else None,
        "access_level": project.access_level,
        "collection_id": project.collection_id,
        "collection_name": collection.name if collection else None,
        "term_base_id": project.term_base_id,
        "term_base_name": term_base.name if term_base else None,
        "created_at": project.created_at.isoformat(),
    }


def _build_project_summary_payload(
    project: FileRecord,
    total_segments: int,
    translated_segments: int,
    creator_name: str | None = None,
) -> dict:
    progress = calculate_file_record_progress(total_segments, translated_segments)
    effective_status = resolve_file_record_status(
        project.status,
        total_segments,
        translated_segments,
    )

    return {
        "id": str(project.id),
        "filename": project.filename,
        "status": effective_status,
        "progress": progress,
        "total_segments": total_segments,
        "translated_segments": translated_segments,
        "source_language": project.source_language,
        "target_language": project.target_language,
        "creator": creator_name,
        "deadline": project.deadline.isoformat() if project.deadline else None,
        "access_level": project.access_level,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


def _build_project_detail_payload(
    project: FileRecord,
    total_segments: int,
    translated_segments: int,
) -> dict:
    creator_name = get_user_display_name(project.creator)
    source_bytes = load_file_record_source(project)
    payload = _build_project_summary_payload(
        project=project,
        total_segments=total_segments,
        translated_segments=translated_segments,
        creator_name=creator_name,
    )

    payload.update({
        "has_source_document": source_bytes is not None,
        "file_size_bytes": len(source_bytes) if source_bytes is not None else None,
        "collection_id": project.collection_id,
        "term_base_id": project.term_base_id,
    })
    return payload


@router.get("/projects/{project_id}")
def get_project_detail(
    project_id: UUID,
    db: Session = Depends(get_db),
):
    project = db.query(FileRecord).filter(FileRecord.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在。")

    total_segments = (
        db.query(func.count(Segment.id))
        .filter(Segment.file_record_id == project_id)
        .scalar()
        or 0
    )
    translated_segments = (
        db.query(func.count(Segment.id))
        .filter(
            Segment.file_record_id == project_id,
            Segment.target_text != "",
        )
        .scalar()
        or 0
    )

    return _build_project_detail_payload(project, total_segments, translated_segments)


@router.post("/projects/{project_id}/source-document")
def upload_project_source_document(
    project_id: UUID,
    file: UploadFile = File(...),
    threshold: float = Form(default=0.6),
    collection_ids: list[UUID] | None = Form(default=None),
    term_base_id: UUID | None = Form(default=None),
    db: Session = Depends(get_db),
):
    _validate_task_upload(file)
    project = get_file_record_model(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在。")
    if project.status != "draft":
        raise HTTPException(status_code=400, detail="该项目已经上传过待翻译文档，请直接继续处理。")

    existing_segments = (
        db.query(Segment.id)
        .filter(Segment.file_record_id == project_id)
        .first()
    )
    if existing_segments or load_file_record_source(project) is not None:
        raise HTTPException(status_code=400, detail="该项目已经存在源文档，请直接继续处理。")

    raw_bytes = file.file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")

    selected_collection_ids = _validate_collection_ids(db, collection_ids)
    required_collection_ids = _require_selected_collection_ids(selected_collection_ids)
    primary_collection = _get_collection_or_404(db, required_collection_ids[0])
    project_source_language, project_target_language = _resolve_file_record_language_pair(project)
    _ensure_resource_language_pair_matches(primary_collection, project_source_language, project_target_language, "记忆库")

    # 验证术语库是否存在
    term_base = None
    if term_base_id is not None:
        term_base = db.query(TermBase).filter(TermBase.id == term_base_id).first()
        if term_base is None:
            raise HTTPException(status_code=404, detail="术语库不存在。")
        _ensure_resource_language_pair_matches(term_base, project_source_language, project_target_language, "术语库")

    try:
        project = attach_source_document_to_file_record(
            db=db,
            file_record=project,
            raw_bytes=raw_bytes,
            source_filename=file.filename or "source.txt",
            similarity_threshold=threshold,
            collection_ids=required_collection_ids,
        )
        # 写入绑定关系
        if required_collection_ids:
            project.collection_id = required_collection_ids[0]
            _apply_collection_language_pair(project, primary_collection)
        project.term_base_id = term_base_id
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    total_segments = (
        db.query(func.count(Segment.id))
        .filter(Segment.file_record_id == project_id)
        .scalar()
        or 0
    )

    return {
        "id": str(project.id),
        "status": project.status,
        "filename": project.filename,
        "total_segments": total_segments,
        "has_source_document": True,
    }


@router.get("/projects")
def list_projects(
    skip: int = 0,
    limit: int = 50,
    search: str = "",
    db: Session = Depends(get_db),
):
    """获取项目列表（分页、搜索），含翻译进度统计"""
    from sqlalchemy import case as sql_case
    from sqlalchemy.orm import joinedload

    base_query = db.query(FileRecord).options(joinedload(FileRecord.creator))
    if search.strip():
        base_query = base_query.filter(FileRecord.filename.ilike(f"%{search.strip()}%"))

    total = base_query.count()
    safe_skip = max(skip, 0)
    safe_limit = min(max(limit, 1), 200)
    file_records = (
        base_query
        .order_by(FileRecord.created_at.desc())
        .offset(safe_skip)
        .limit(safe_limit)
        .all()
    )

    fr_ids = [fr.id for fr in file_records]
    segment_stats: dict = {}
    if fr_ids:
        stats_rows = (
            db.query(
                Segment.file_record_id,
                func.count(Segment.id).label("total"),
                func.count(sql_case((Segment.target_text != "", 1))).label("filled"),
            )
            .filter(Segment.file_record_id.in_(fr_ids))
            .group_by(Segment.file_record_id)
            .all()
        )
        for row in stats_rows:
            segment_stats[row.file_record_id] = {
                "total": row.total,
                "filled": row.filled,
            }

    items = []
    for fr in file_records:
        st = segment_stats.get(fr.id, {"total": 0, "filled": 0})
        total_segs = st["total"]
        filled_segs = st["filled"]

        creator_name = None
        if fr.creator:
            creator_name = get_user_display_name(fr.creator)

        items.append(
            _build_project_summary_payload(
                project=fr,
                total_segments=total_segs,
                translated_segments=filled_segs,
                creator_name=creator_name,
            )
        )

    return {
        "items": items,
        "total": total,
        "skip": safe_skip,
        "limit": safe_limit,
    }


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
            "source_language": file_record.source_language,
            "target_language": file_record.target_language,
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
    source_bytes = load_file_record_source(file_record)
    source_filename = get_file_record_source_filename(file_record)

    # 获取绑定的库信息
    collection_name = None
    if file_record.collection:
        collection_name = file_record.collection.name
    term_base_name = None
    if file_record.term_base:
        term_base_name = file_record.term_base.name

    return {
        "id": file_record.id,
        "filename": file_record.filename,
        "status": file_record.status,
        "source_language": file_record.source_language,
        "target_language": file_record.target_language,
        "collection_id": file_record.collection_id,
        "collection_name": collection_name,
        "term_base_id": file_record.term_base_id,
        "term_base_name": term_base_name,
        "created_at": file_record.created_at.isoformat(),
        "updated_at": file_record.updated_at.isoformat(),
        "total_segments": result["total_segments"],
        "skip": result["skip"],
        "limit": result["limit"],
        "source_extension": get_task_file_extension(source_filename),
        "has_source_document": source_bytes is not None,
        "can_export": can_export_task_file(source_filename, has_source_file=source_bytes is not None),
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
                "matched_collection_name": seg.matched_collection_name,
                "matched_creator_name": seg.matched_creator_name,
                "matched_created_at": seg.matched_created_at.isoformat() if seg.matched_created_at else None,
                "matched_updated_at": seg.matched_updated_at.isoformat() if seg.matched_updated_at else None,
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
    source_filename = get_file_record_source_filename(file_record)
    segments = list_segments_for_file_record(db, file_record_id)
    preview_html = build_task_preview_html(
        filename=source_filename,
        segments=segments,
        source_bytes=source_bytes,
    )

    return {
        "id": file_record.id,
        "filename": file_record.filename,
        "source_extension": get_task_file_extension(source_filename),
        "supports_preview": bool(preview_html),
        "preview_html": preview_html,
    }


@router.get("/file-records/{file_record_id}/segments/{segment_ref}/tm-candidates")
def get_segment_tm_candidates(
    file_record_id: UUID,
    segment_ref: str,
    threshold: float = 0.6,
    max_candidates: int = 5,
    db: Session = Depends(get_db),
):
    """获取指定句段的 TM 匹配候选列表，兼容句段 UUID 和 sentence_id。"""
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    segment = None
    try:
        segment_uuid = UUID(segment_ref)
    except ValueError:
        segment_uuid = None

    if segment_uuid is not None:
        segment = db.query(Segment).filter(
            Segment.id == segment_uuid,
            Segment.file_record_id == file_record_id,
        ).first()

    if segment is None:
        segment = db.query(Segment).filter(
            Segment.file_record_id == file_record_id,
            Segment.sentence_id == segment_ref,
        ).first()

    if not segment:
        raise HTTPException(status_code=404, detail="句段不存在。")

    collection_ids = None
    if file_record.collection_id:
        collection_ids = [file_record.collection_id]

    candidates = get_tm_candidates_for_text(
        db=db,
        source_text=segment.source_text,
        similarity_threshold=threshold,
        collection_ids=collection_ids,
        top_n=max_candidates,
    )

    return {
        "segment_id": str(segment.id),
        "sentence_id": segment.sentence_id,
        "source_text": segment.source_text,
        "candidates": [
            {
                "source_text": c.source_text,
                "target_text": c.target_text,
                "score": c.score,
                "diff_html": c.diff_html,
                "collection_name": c.collection_name,
                "creator_name": c.creator_name,
                "created_at": c.created_at,
                "updated_at": c.updated_at,
            }
            for c in candidates
        ],
    }


@router.get("/file-records/{file_record_id}/export")
@router.get("/documents/{file_record_id}/export", include_in_schema=False)
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
    source_filename = get_file_record_source_filename(file_record)
    if not can_export_task_file(source_filename, has_source_file=raw_bytes is not None):
        raise HTTPException(status_code=400, detail="Current file format does not support original export yet.")

    segments = list_segments_for_file_record(db, file_record_id)
    try:
        exported_file = export_translated_task_file(
            raw_bytes=raw_bytes,
            filename=source_filename,
            segments=segments,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _build_binary_download_response(
        filename=exported_file.filename,
        content=exported_file.content,
        media_type=exported_file.media_type,
    )


@router.get("/file-records/{file_record_id}/export-options")
@router.get("/documents/{file_record_id}/export-options", include_in_schema=False)
def get_file_record_export_options(
    file_record_id: UUID,
    db: Session = Depends(get_db),
):
    """获取文件支持的导出格式选项"""
    from app.services.adapters import get_export_options_for_file

    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    options = get_export_options_for_file(file_record.filename)

    return {
        "file_record_id": str(file_record_id),
        "filename": file_record.filename,
        "export_options": options,
    }


@router.get("/file-records/{file_record_id}/export/{export_type}")
@router.get("/documents/{file_record_id}/export/{export_type}", include_in_schema=False)
def export_file_record_with_type(
    file_record_id: UUID,
    export_type: str,
    db: Session = Depends(get_db),
):
    """多格式导出接口 - 支持原格式、双语、TMX、XLIFF 等导出类型

    Args:
        file_record_id: 文件记录 ID
        export_type: 导出类型 (original, bilingual, bilingual_txt, tmx, xliff, xliff2)
    """
    from app.services.adapters import export_file

    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    raw_bytes = load_file_record_source(file_record)
    segments = list_segments_for_file_record(db, file_record_id)

    # 转换句段格式
    segment_dicts = [
        {
            "segment_id": seg.sentence_id,
            "source_text": seg.source_text,
            "target_text": seg.target_text,
            "status": seg.status,
            "matched_source_text": seg.matched_source_text,
        }
        for seg in segments
    ]

    try:
        exported_bytes, mime_type, export_filename = export_file(
            export_type=export_type,
            segments=segment_dicts,
            filename=file_record.filename,
            original_bytes=raw_bytes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"导出失败: {str(exc)}") from exc

    # 构建下载响应
    ascii_filename = export_filename.encode("ascii", "ignore").decode("ascii").strip() or "exported"
    ascii_filename = ascii_filename.replace('"', "")
    quoted_filename = quote(export_filename)

    return StreamingResponse(
        BytesIO(exported_bytes),
        media_type=mime_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_filename}"; '
                f"filename*=UTF-8''{quoted_filename}"
            )
        },
    )


@router.put("/file-records/{file_record_id}/segments/{sentence_id}")
@router.put("/documents/{file_record_id}/segments/{sentence_id}", include_in_schema=False)
def update_segment(
    file_record_id: UUID,
    sentence_id: str,
    update: SegmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新单个片段的译文"""
    segment = update_segment_by_sentence_id(
        db=db,
        file_record_id=file_record_id,
        sentence_id=sentence_id,
        target_text=update.target_text,
        source=update.source,
        current_user=current_user,
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
    current_user: User = Depends(get_current_user),
):
    """批量更新片段译文"""
    updated_count = batch_update_segments(
        db=db,
        file_record_id=file_record_id,
        updates=[u.model_dump() for u in batch.updates],
        current_user=current_user,
    )
    return {"updated_count": updated_count}


@router.get("/file-records/{file_record_id}/revisions")
def get_file_record_revisions(
    file_record_id: UUID,
    sentence_id: str | None = None,
    db: Session = Depends(get_db),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File record not found.")

    revisions = list_revisions(
        db,
        file_record_id=file_record_id,
        sentence_id=sentence_id,
    )
    return [serialize_segment_revision(revision) for revision in revisions]


@router.patch("/revisions/{revision_id}")
def resolve_revision(
    revision_id: UUID,
    payload: RevisionResolvePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.status == "accepted":
        revision = accept_revision(
            db,
            revision_id=revision_id,
            current_user=current_user,
        )
    else:
        revision = reject_revision(
            db,
            revision_id=revision_id,
            current_user=current_user,
        )
    return serialize_segment_revision(revision)


@router.post("/file-records/{file_record_id}/revisions/batch-accept")
def resolve_all_revisions_as_accepted(
    file_record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File record not found.")

    updated_count = batch_accept_revisions(
        db,
        file_record_id=file_record_id,
        current_user=current_user,
    )
    return {"updated_count": updated_count}


@router.post("/file-records/{file_record_id}/revisions/batch-reject")
def resolve_all_revisions_as_rejected(
    file_record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File record not found.")

    updated_count = batch_reject_revisions(
        db,
        file_record_id=file_record_id,
        current_user=current_user,
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
    current_user: User = Depends(get_current_user),
):
    """对指定范围的片段触发 LLM 译文修正，并通过 SSE 逐条返回结果。"""
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    source_language, target_language = _resolve_file_record_language_pair(file_record)
    body = payload or LLMTranslateRequest()
    try:
        validate_provider_choice(body.provider)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    translation_tasks = _build_llm_translation_tasks(
        db=db,
        file_record_id=file_record_id,
        scope=body.scope,
        source_language=source_language,
        target_language=target_language,
        collection_id=file_record.collection_id,
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
                "source_language": source_language,
                "target_language": target_language,
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
                    current_user=current_user,
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
    source_language: str | None = None
    target_language: str | None = None


class BatchTMEntry(BaseModel):
    collection_id: UUID | None = None
    source_language: str | None = None
    target_language: str | None = None
    entries: list[TMEntry]


class TMEntryUpdatePayload(BaseModel):
    source_text: str
    target_text: str


def _serialize_tm_entry(entry: TranslationMemory) -> dict:
    return {
        "id": entry.id,
        "collection_id": entry.collection_id,
        "source_text": entry.source_text,
        "target_text": entry.target_text,
        "source_language": entry.source_language,
        "target_language": entry.target_language,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
    }


@router.get("/translation-memory/collections")
@router.get("/tm/collections", include_in_schema=False)
def list_tm_collections(
    db: Session = Depends(get_db),
):
    rows = (
        db.query(MemoryBase, func.count(MemoryEntry.id).label("entry_count"))
        .outerjoin(MemoryEntry, MemoryEntry.collection_id == MemoryBase.id)
        .group_by(MemoryBase.id)
        .order_by(MemoryBase.created_at.desc())
        .all()
    )
    return [
        _serialize_tm_collection(collection, int(entry_count))
        for collection, entry_count in rows
    ]


@router.get("/translation-memory/collections/{collection_id}")
@router.get("/tm/collections/{collection_id}", include_in_schema=False)
def get_tm_collection(
    collection_id: UUID,
    db: Session = Depends(get_db),
):
    collection = _get_collection_or_404(db, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="TM 记忆库不存在。")

    entry_count = (
        db.query(TranslationMemory)
        .filter(TranslationMemory.collection_id == collection.id)
        .count()
    )
    return _serialize_tm_collection(collection, entry_count)


@router.post("/translation-memory/collections")
@router.post("/tm/collections", include_in_schema=False)
def create_tm_collection(
    payload: MemoryBasePayload,
    db: Session = Depends(get_db),
):
    name = _normalize_collection_name(payload.name)
    source_language, target_language = _require_tm_language_pair(
        payload.source_language,
        payload.target_language,
    )
    if not name:
        raise HTTPException(status_code=400, detail="记忆库名称不能为空。")

    collection = MemoryBase(
        name=name,
        description=normalize_text(payload.description or "") or None,
        source_language=source_language,
        target_language=target_language,
    )
    db.add(collection)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名记忆库已存在。") from exc

    db.refresh(collection)
    return _serialize_tm_collection(collection)


@router.put("/translation-memory/collections/{collection_id}")
@router.put("/tm/collections/{collection_id}", include_in_schema=False)
def update_tm_collection(
    collection_id: UUID,
    payload: MemoryBasePayload,
    db: Session = Depends(get_db),
):
    collection = _get_collection_or_404(db, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="TM 记忆库不存在。")

    name = _normalize_collection_name(payload.name)
    source_language, target_language = _require_tm_language_pair(
        payload.source_language,
        payload.target_language,
    )
    if not name:
        raise HTTPException(status_code=400, detail="记忆库名称不能为空。")

    collection.name = name
    collection.description = normalize_text(payload.description or "") or None
    collection.source_language = source_language
    collection.target_language = target_language
    (
        db.query(TranslationMemory)
        .filter(TranslationMemory.collection_id == collection.id)
        .update(
            {
                TranslationMemory.source_language: source_language,
                TranslationMemory.target_language: target_language,
            },
            synchronize_session=False,
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名记忆库已存在。") from exc

    db.refresh(collection)
    entry_count = (
        db.query(MemoryEntry)
        .filter(MemoryEntry.collection_id == collection.id)
        .count()
    )
    return _serialize_tm_collection(collection, entry_count)


@router.delete("/translation-memory/collections/{collection_id}")
@router.delete("/tm/collections/{collection_id}", include_in_schema=False)
def delete_tm_collection(
    collection_id: UUID,
    db: Session = Depends(get_db),
):
    collection = _get_collection_or_404(db, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="TM 记忆库不存在。")

    entry_count = (
        db.query(MemoryEntry)
        .filter(MemoryEntry.collection_id == collection.id)
        .count()
    )
    if entry_count:
        raise HTTPException(status_code=409, detail="请先清空该记忆库中的 TM 记录。")

    db.delete(collection)
    db.commit()
    return {"message": "记忆库已删除。"}


@router.post("/translation-memory/preview-sdltm")
@router.post("/tm/preview-sdltm", include_in_schema=False)
async def preview_sdltm(
    file: UploadFile = File(...),
):
    """Preview SDLTM file metadata without importing."""
    extension = f".{(file.filename or '').split('.')[-1].lower()}" if file.filename else ""
    if extension not in SDLTM_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持 .sdltm 文件。")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="上传的文件为空。")

    try:
        metadata = preview_sdltm_metadata(raw_bytes)
        return {
            "name": metadata.name,
            "source_language": metadata.source_language,
            "target_language": metadata.target_language,
            "entry_count": metadata.entry_count,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取 SDLTM 元数据失败：{exc}") from exc


@router.post("/translation-memory/import-xlsx")
@router.post("/tm/import-xlsx", include_in_schema=False)
@router.post("/translation-memory/import", include_in_schema=False)
@router.post("/tm/import", include_in_schema=False)
async def import_tm_xlsx(
    file: UploadFile = File(...),
    collection_id: UUID | None = Form(default=None),
    source_language: str = Form(...),
    target_language: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    extension = f".{(file.filename or '').split('.')[-1].lower()}" if file.filename else ""
    if extension not in TM_IMPORT_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持上传 .xlsx 或 .sdltm 文件。")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="上传的文件为空。")

    collection = _get_collection_or_404(db, collection_id)
    resolved_source_language, resolved_target_language = _resolve_collection_language_pair(
        collection,
        source_language,
        target_language,
    )
    try:
        if extension in SDLTM_EXTENSIONS:
            import_summary = import_tm_from_sdltm_upload(
                db=db,
                raw_bytes=raw_bytes,
                filename=file.filename or "uploaded.sdltm",
                collection_id=collection_id,
                source_language=resolved_source_language,
                target_language=resolved_target_language,
                creator_id=current_user.id,
            )
        else:
            import_summary = import_tm_from_xlsx_upload(
                db=db,
                raw_bytes=raw_bytes,
                filename=file.filename or "uploaded.xlsx",
                collection_id=collection_id,
                source_language=resolved_source_language,
                target_language=resolved_target_language,
                creator_id=current_user.id,
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
        "source_language": resolved_source_language,
        "target_language": resolved_target_language,
    }


@router.get("/translation-memory/collections/{collection_id}/entries")
@router.get("/tm/collections/{collection_id}/entries", include_in_schema=False)
def list_tm_collection_entries(
    collection_id: UUID,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
    db: Session = Depends(get_db),
):
    collection = _get_collection_or_404(db, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="TM 记忆库不存在。")

    safe_skip = max(skip, 0)
    safe_limit = min(max(limit, 1), 200)
    query = (
        db.query(TranslationMemory)
        .filter(TranslationMemory.collection_id == collection.id)
    )
    normalized_search = normalize_text(search or "")
    if normalized_search:
        like_pattern = f"%{normalized_search}%"
        query = query.filter(
            or_(
                TranslationMemory.source_text.ilike(like_pattern),
                TranslationMemory.target_text.ilike(like_pattern),
            )
        )

    total = query.count()
    rows = (
        query
        .order_by(TranslationMemory.updated_at.desc(), TranslationMemory.created_at.desc())
        .offset(safe_skip)
        .limit(safe_limit)
        .all()
    )
    return {
        "items": [_serialize_tm_entry(row) for row in rows],
        "total": total,
        "skip": safe_skip,
        "limit": safe_limit,
    }


@router.get("/translation-memory/collections/{collection_id}/export-xlsx")
def export_tm_collection_entries_xlsx(
    collection_id: UUID,
    db: Session = Depends(get_db),
):
    collection = _get_collection_or_404(db, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="TM 记忆库不存在。")

    entries = (
        db.query(TranslationMemory)
        .filter(TranslationMemory.collection_id == collection.id)
        .order_by(TranslationMemory.updated_at.desc(), TranslationMemory.created_at.desc())
        .all()
    )
    rows = [
        [
            entry.source_text,
            entry.target_text,
            entry.source_language or "",
            entry.target_language or "",
            entry.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            entry.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
        ]
        for entry in entries
    ]
    xlsx_bytes = build_tabular_xlsx(
        sheet_title=collection.name,
        headers=["原文", "译文", "源语言", "目标语言", "创建时间", "更新时间"],
        rows=rows,
    )
    return build_xlsx_download_response(f"{collection.name}-tm.xlsx", xlsx_bytes)


@router.post("/translation-memory/collections/{collection_id}/entries")
def add_tm_collection_entry(
    collection_id: UUID,
    payload: TMEntryUpdatePayload,
    db: Session = Depends(get_db),
):
    collection = _get_collection_or_404(db, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="TM 记忆库不存在。")

    result = add_tm_entry(
        TMEntry(
            collection_id=collection.id,
            source_text=payload.source_text,
            target_text=payload.target_text,
            source_language=collection.source_language,
            target_language=collection.target_language,
        ),
        db,
    )
    entry = db.query(TranslationMemory).filter(TranslationMemory.id == result["id"]).first()
    if entry is None:
        raise HTTPException(status_code=500, detail="TM 条目保存成功，但读取结果失败。")
    return _serialize_tm_entry(entry)


@router.post("/translation-memory/entries")
@router.post("/tm/add", include_in_schema=False)
def add_tm_entry(
    entry: TMEntry,
    db: Session = Depends(get_db),
):
    """添加单条 TM 记录（去重：相同原文不重复添加）"""
    source_text = normalize_text(entry.source_text)
    target_text = normalize_text(entry.target_text)

    if not source_text or not target_text:
        raise HTTPException(status_code=400, detail="原文和译文不能为空。")

    collection = _get_collection_or_404(db, entry.collection_id)
    source_language, target_language = _resolve_collection_language_pair(
        collection,
        entry.source_language,
        entry.target_language,
    )
    source_hash = build_source_hash(source_text)

    # 检查是否已存在
    existing_query = db.query(MemoryEntry).filter(
        MemoryEntry.source_hash == source_hash
    )
    existing = _filter_tm_collection(
        existing_query,
        entry.collection_id,
        source_language=source_language,
        target_language=target_language,
    ).first()

    if existing:
        # 已存在，更新译文
        existing.source_text = source_text
        existing.target_text = target_text
        existing.source_hash = source_hash
        existing.source_normalized = normalize_match_text(source_text) or source_text
        existing.source_language = source_language
        existing.target_language = target_language
        db.commit()
        sync_tm_embeddings(db, [(existing.id, existing.source_text)])
        return {"status": "updated", "id": existing.id, "message": "已更新现有记录。"}

    # 不存在，新增
    tm = MemoryEntry(
        collection_id=entry.collection_id,
        source_text=source_text,
        target_text=target_text,
        source_hash=source_hash,
        source_normalized=normalize_match_text(source_text) or source_text,
        source_language=source_language,
        target_language=target_language,
    )
    db.add(tm)
    db.commit()
    db.refresh(tm)
    sync_tm_embeddings(db, [(tm.id, tm.source_text)])

    return {"status": "created", "id": tm.id, "message": "已添加新记录。"}


@router.put("/translation-memory/entries/{entry_id}")
@router.put("/tm/entries/{entry_id}", include_in_schema=False)
def update_tm_entry(
    entry_id: UUID,
    payload: TMEntryUpdatePayload,
    db: Session = Depends(get_db),
):
    entry = db.query(TranslationMemory).filter(TranslationMemory.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="TM 条目不存在。")

    source_text = normalize_text(payload.source_text)
    target_text = normalize_text(payload.target_text)
    if not source_text or not target_text:
        raise HTTPException(status_code=400, detail="原文和译文不能为空。")

    collection = _get_collection_or_404(db, entry.collection_id)
    source_language = collection.source_language if collection else entry.source_language
    target_language = collection.target_language if collection else entry.target_language
    if not source_language or not target_language:
        raise HTTPException(status_code=400, detail="当前 TM 条目缺少语言对，请先更新记忆库信息。")

    source_hash = build_source_hash(source_text)
    duplicate_query = db.query(TranslationMemory).filter(
        TranslationMemory.id != entry.id,
        or_(
            TranslationMemory.source_hash == source_hash,
            TranslationMemory.source_text == source_text,
        ),
    )
    duplicate = _filter_tm_collection(
        duplicate_query,
        entry.collection_id,
        source_language=source_language,
        target_language=target_language,
    ).first()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="当前记忆库中已存在相同原文的 TM 条目。")

    entry.source_text = source_text
    entry.target_text = target_text
    entry.source_hash = source_hash
    entry.source_normalized = normalize_match_text(source_text) or source_text
    entry.source_language = source_language
    entry.target_language = target_language
    db.commit()
    db.refresh(entry)
    sync_tm_embeddings(db, [(entry.id, entry.source_text)])
    return _serialize_tm_entry(entry)


@router.delete("/translation-memory/entries/{entry_id}")
@router.delete("/tm/entries/{entry_id}", include_in_schema=False)
def delete_tm_entry(
    entry_id: UUID,
    db: Session = Depends(get_db),
):
    entry = db.query(TranslationMemory).filter(TranslationMemory.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="TM 条目不存在。")

    db.delete(entry)
    db.commit()
    return {"message": "TM 条目已删除。"}


@router.post("/translation-memory/entries/batch")
@router.post("/tm/batch-add", include_in_schema=False)
def batch_add_tm_entries(
    batch: BatchTMEntry,
    db: Session = Depends(get_db),
):
    """批量添加 TM 记录（去重）"""
    created_count = 0
    updated_count = 0
    skipped_count = 0
    sync_candidates: list[MemoryEntry] = []
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
        collection = _get_collection_or_404(db, collection_id)
        source_language, target_language = _resolve_collection_language_pair(
            collection,
            entry.source_language or batch.source_language,
            entry.target_language or batch.target_language,
        )

        existing_query = db.query(MemoryEntry).filter(
            MemoryEntry.source_hash == source_hash
        )
        existing = _filter_tm_collection(
            existing_query,
            collection_id,
            source_language=source_language,
            target_language=target_language,
        ).first()

        if existing:
            existing.source_text = source_text
            existing.target_text = target_text
            existing.source_hash = source_hash
            existing.source_normalized = normalize_match_text(source_text) or source_text
            existing.collection_id = collection_id
            existing.source_language = source_language
            existing.target_language = target_language
            sync_candidates.append(existing)
            updated_count += 1
        else:
            tm = MemoryEntry(
                collection_id=collection_id,
                source_text=source_text,
                target_text=target_text,
                source_hash=source_hash,
                source_normalized=normalize_match_text(source_text) or source_text,
                source_language=source_language,
                target_language=target_language,
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

def _serialize_termbase_collection(collection: TermBase, entry_count: int = 0) -> dict:
    return {
        "id": collection.id,
        "name": collection.name,
        "description": collection.description,
        "source_language": collection.source_language,
        "target_language": collection.target_language,
        "created_at": collection.created_at.isoformat(),
        "updated_at": collection.updated_at.isoformat(),
        "entry_count": entry_count,
    }


def _get_termbase_collection_or_404(db: Session, collection_id: UUID | None) -> TermBase | None:
    if collection_id is None:
        return None

    collection = db.query(TermBase).filter(TermBase.id == collection_id).first()
    if collection is None:
        raise HTTPException(status_code=404, detail="术语库不存在。")
    return collection


@router.get("/termbase/collections")
def list_termbase_collections(
    db: Session = Depends(get_db),
):
    rows = (
        db.query(TermBase, func.count(TermEntry.id).label("entry_count"))
        .outerjoin(TermEntry, TermEntry.term_base_id == TermBase.id)
        .group_by(TermBase.id)
        .order_by(TermBase.created_at.desc())
        .all()
    )
    return [
        _serialize_termbase_collection(collection, int(entry_count))
        for collection, entry_count in rows
    ]


@router.post("/termbase/collections")
def create_termbase_collection(
    payload: TermBasePayload,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    name = _normalize_collection_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="术语库名称不能为空。")

    collection = TermBase(
        name=name,
        description=normalize_text(payload.description or "") or None,
        source_language=payload.source_language,
        target_language=payload.target_language,
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
        db.query(TermEntry)
        .filter(TermEntry.term_base_id == collection.id)
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
    query = db.query(TermEntry)
    if collection_id:
        query = query.filter(TermEntry.term_base_id == collection_id)

    total = query.count()
    terms = query.order_by(TermEntry.source_text).offset(skip).limit(limit).all()

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "terms": [
            {
                "id": term.id,
                "source_text": term.source_text,
                "target_text": term.target_text,
                "term_base_id": term.term_base_id,
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
        db.query(TermEntry)
        .filter(TermEntry.source_text == source_text, TermEntry.term_base_id == payload.collection_id)
        .first()
    )

    if existing:
        existing.target_text = target_text
        db.commit()
        return {"status": "updated", "id": existing.id, "message": "已更新现有术语。"}

    # 获取术语库的语言设置
    term_base = _get_termbase_collection_or_404(db, payload.collection_id)
    source_lang = term_base.source_language if term_base else "zh"
    target_lang = term_base.target_language if term_base else "en"

    term = TermEntry(
        term_base_id=payload.collection_id,
        source_text=source_text,
        target_text=target_text,
        source_language=source_lang,
        target_language=target_lang,
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
    term = db.query(TermEntry).filter(TermEntry.id == term_id).first()
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
                db.query(TermEntry)
                .filter(TermEntry.source_text == source_text, TermEntry.term_base_id == collection_id)
                .first()
            )

            if existing:
                existing.target_text = target_text
                updated_count += 1
            else:
                # 获取术语库的语言设置
                source_lang = collection.source_language if collection else "zh"
                target_lang = collection.target_language if collection else "en"
                term = TermEntry(
                    term_base_id=collection_id,
                    source_text=source_text,
                    target_text=target_text,
                    source_language=source_lang,
                    target_language=target_lang,
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

    query = db.query(TermEntry)
    if collection_ids:
        query = query.filter(TermEntry.term_base_id.in_(collection_ids))

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
