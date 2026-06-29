"""
API 路由模块 - 文件上传、解析和导出接口

支持多种文档格式的上传、解析和导出。
"""
import asyncio
import json
import logging
import re
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional
from urllib.parse import quote, unquote, urlparse
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, literal, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from app.auth import (
    USER_ROLE,
    can_access_all_projects,
    get_current_user,
    get_user_by_id,
    get_user_display_name,
    is_admin_role,
    is_external_translator,
    require_admin,
    serialize_user,
)
from app.config import get_settings
from app.database import SessionLocal, get_db
from app.models import (
    AssignmentEvent,
    DocumentStatisticsReport,
    DocumentStatisticsReportItem,
    FileAssignment,
    FileExportTask,
    FileRecord,
    GlossaryBase,
    IssueMarker,
    MemoryBase,
    MemoryEntry,
    Notification,
    NumberCheckReport,
    NumberCheckReportItem,
    Project,
    ProjectAssignment,
    ProjectMergeView,
    PretranslationRun,
    PretranslationTask,
    ProjectWorkflowStep,
    Segment,
    SegmentQAIssue,
    SegmentRevision,
    TMCollection,
    TermQAReport,
    TermQAReportItem,
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
from app.services.analytics_service import (
    count_source_words,
    get_dashboard_payload,
    record_translation_metric_event,
    run_analytics_backfill_once,
)
from app.services.automatic_numbering import (
    get_automatic_numbering_text,
    is_word_document_filename,
    strip_automatic_numbering_prefix,
)
from app.services.auto_tm_sync import (
    AutoTMEnqueueSummary,
    enqueue_confirmed_segments_for_auto_tm,
    process_due_auto_tm_rematches,
    refresh_unconfirmed_segment_matches,
    register_file_rematch_work,
    register_project_collections_rematch_work,
    run_auto_tm_background_once,
    run_auto_tm_rematch_background_once,
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
from app.services.document_statistics import (
    STATISTIC_NUMBER_KEYS,
    compute_word_document_statistics,
    normalize_document_statistics,
    serialize_document_statistics,
)
from app.services.document_match_analysis import (
    DocumentMatchSegment,
    compute_document_match_analysis,
    empty_document_match_analysis,
    merge_document_match_analyses,
    normalize_document_match_analysis,
    reconcile_document_match_analysis_words,
)
from app.services.document_repetition_statistics import (
    compute_document_repetition_statistics,
    empty_repetition_statistics,
)
from app.services.issue_marker_service import (
    create_issue_marker,
    delete_issue_marker,
    list_issue_markers_for_project,
    serialize_issue_marker,
    update_issue_marker,
)
from app.services.import_task_storage import (
    cleanup_expired_import_staging,
    cleanup_import_task_staging,
    read_import_file_bytes,
    stage_import_file_payload,
    stage_import_file_payloads,
    stage_import_file_streams,
)
from app.services.import_task_state import (
    ImportTaskCanceled,
    get_import_task_status as shared_get_import_task_status,
    import_task_cache_key,
    import_task_cancel_requested,
    raise_if_import_task_canceled,
    request_import_task_cancel,
    set_import_task_status as shared_set_import_task_status,
)
from app.services.file_record_service import (
    SEGMENT_ORDERING,
    SegmentUpdateConflict,
    _can_auto_merge_stale_segment,
    backfill_file_record_source_html,
    batch_update_segments,
    calculate_file_record_progress,
    create_file_record_with_segments,
    delete_file_record,
    duplicate_file_record,
    get_file_record as get_file_record_model,
    get_file_record_document_statistics,
    get_file_record_source_filename,
    get_file_record_with_segments,
    get_segment_ordering_for_file_record,
    get_tm_target_text_map,
    list_file_records,
    list_segments_for_file_record,
    load_file_record_source,
    resolve_file_record_status,
    sync_file_record_status,
    update_segment_by_sentence_id,
    update_segment_source_text,
)
from app.services.file_operation_lock_service import (
    FILE_OPERATION_TOKEN_HEADER,
    PRE_TRANSLATE_OPERATION,
    acquire_file_operation_lock,
    clear_stale_file_operation_lock,
    ensure_file_record_write_allowed,
    heartbeat_file_operation_lock,
    release_file_operation_lock,
    serialize_file_operation_state,
)
from app.services.guideline_repository import (
    GuidelineTemplate,
    delete_guideline_template,
    list_guideline_templates,
    read_guideline_template,
    save_guideline_template,
    update_guideline_template,
)
from app.services.glossary_matcher import build_glossary_matches_by_text
from app.services.llm_service import (
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseValidationError,
    LLMTranslationFailure,
    LLMTranslationTask,
    iter_batch_translate,
    validate_provider_choice,
)
from app.services.term_entry_service import build_term_entry_conflict_items, save_term_entries_batch
from app.services.term_extraction_service import (
    TERM_EXTRACTION_MODEL,
    TERM_EXTRACTION_MODEL_OPTIONS,
    ExtractedTerm,
    TermExtractionError,
    extract_terms_from_segments,
    merge_extracted_terms,
)
from app.services.term_matcher import find_non_overlapping_term_text_matches, text_contains_term
from app.services.language_detection import detect_upload_language
from app.services.language_pairs import require_language_pair
from app.services.matcher import get_tm_candidates_for_text, match_sentences_with_stats
from app.services.normalizer import build_source_hash, normalize_match_text, normalize_text
from app.services.tm_match_state import (
    build_tm_match_signature,
    is_tm_match_signature_current,
    mark_tm_match_signature_current,
)
from app.services.notification_service import (
    build_resource_import_notification,
    build_save_to_tm_notification,
    create_operation_notification,
)
from app.services.spelling_grammar_qa import (
    QA_ISSUE_STATUS_IGNORED,
    QA_ISSUE_STATUS_OPEN,
    QA_RULE_SPELLING_GRAMMAR,
    QA_RULE_TERM_INCONSISTENCY,
    check_segments_with_languagetool,
    get_languagetool_language,
    get_supported_quality_qa_languages,
    is_languagetool_configured,
    load_open_segment_qa_issues_by_segment_id,
    load_quality_qa_settings,
    normalize_quality_qa_settings,
    run_spelling_grammar_qa_for_project,
    run_spelling_grammar_qa_for_segment_ids,
    serialize_segment_qa_issue,
    store_quality_qa_settings,
)
from app.services.file_export_queue import (
    build_file_export_download_response,
    get_file_export_task,
    queue_file_export,
    serialize_file_export_task,
    wait_for_file_export_task,
)
from app.services.project_file_export_zip_queue import (
    build_project_file_zip_export_download_response,
    ensure_project_file_zip_export_task_status,
    queue_project_file_zip_export,
)
from app.services.merge_view_service import (
    load_view_file_records,
    normalize_file_ids,
    parse_file_ids,
    serialize_file_ids,
    serialize_merge_view_detail,
    serialize_merge_view_summary,
)
from app.services.project_segment_sync import (
    ProjectSyncDisableSummary,
    disable_project_sync_for_segments,
    empty_project_segment_sync_summary,
    enable_project_sync_for_segments,
    sync_project_repeated_segments_from_file,
    sync_project_repeated_segments_from_segments,
)
from app.services.term_importer import (
    TERM_IMPORT_EXTENSIONS,
    TBX_EXTENSIONS,
    import_terms_from_tbx_path,
    import_terms_from_tmx_path,
    import_terms_from_xlsx_path,
    import_terms_from_xlsx_upload,
)
from app.services.revision_service import (
    accept_revision,
    batch_accept_revisions,
    batch_reject_revisions,
    get_revision_or_404,
    list_revisions,
    reject_revision,
    reject_stale_manual_revisions_for_segment,
    serialize_segment_revision,
)
from app.services.revision_settings_service import (
    DEFAULT_DELETE_COLOR,
    DEFAULT_INSERT_COLOR,
    get_revision_display_settings,
    upsert_revision_display_settings,
)
from app.services.resource_export_queue import (
    ResourceExportFormat,
    build_resource_export_download_response,
    cancel_resource_export_task,
    ensure_export_task_status,
    queue_resource_export,
)
from app.services.slate_parser import parse_docx_for_slate
from app.services.task_file_service import (
    BILINGUAL_DOCX_LAYOUT_EXPORT_ORDERS,
    DOCUMENT_PARSE_MODE_FULL,
    UploadLimitError,
    build_task_preview_html,
    build_task_workspace,
    can_export_task_file,
    export_bilingual_task_docx_with_layout,
    export_translated_task_file,
    get_max_upload_size_bytes,
    get_upload_capabilities,
    get_supported_task_extensions,
    get_task_file_extension,
    normalize_document_parse_options,
    normalize_document_parse_mode,
    supports_task_file,
    validate_expanded_upload_batch,
)
from app.services.document_workspace import build_docx_target_numbering_text_map
from app.services.tm_importer import (
    SDLTM_EXTENSIONS,
    TM_IMPORT_EXTENSIONS,
    TMX_EXTENSIONS,
    XLSX_EXTENSIONS,
    import_tm_from_sdltm_path,
    import_tm_from_tmx_path,
    import_tm_from_xlsx_path,
    import_tm_from_upload,
    preview_tm_from_sdltm_path,
    preview_tm_from_tmx_path,
    preview_tm_from_xlsx_path,
    preview_tm_from_upload,
    preview_sdltm_metadata_from_path,
)
from app.services.resource_import_batch import create_resource_import_batch
from app.services.number_check_service import (
    apply_number_check_item,
    apply_number_check_items_bulk,
    aiter_number_check_generation,
    create_number_check_report,
    ignore_number_check_items_bulk,
    load_number_check_items,
    restore_number_check_item,
    run_ai_number_check_all_segments,
    run_ai_number_check_for_report,
    serialize_number_check_report,
    set_number_check_item_ignored,
)
from app.services.tm_vector import sync_tm_embeddings
from app.services.translation_memory_service import TMUpsertEntry, batch_upsert_tm_entries
from app.services.xlsx_exporter import build_tabular_xlsx, build_xlsx_download_response

try:
    from arq import create_pool as arq_create_pool
    from arq.connections import RedisSettings
except ModuleNotFoundError:  # pragma: no cover - 本地未安装 ARQ 时使用 FastAPI 后台任务兜底
    arq_create_pool = None
    RedisSettings = None


logger = logging.getLogger(__name__)
_RESOURCE_IMPORT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="resource-import")
ARQ_MAINTENANCE_QUEUE_NAME = "arq:maintenance"
ARQ_PRETRANSLATION_QUEUE_NAME = "arq:pretranslation"
router = APIRouter(dependencies=[Depends(get_current_user)])
UPLOAD_READ_CHUNK_SIZE = 1024 * 1024


def _build_task_workspace_with_new_session(
    raw_bytes: bytes,
    filename: str,
    similarity_threshold: float,
    collection_ids: list[UUID] | None,
    document_parse_mode: str,
    document_parse_options: dict[str, object] | str | None = None,
) -> dict:
    with SessionLocal() as workspace_db:
        return build_task_workspace(
            db=workspace_db,
            raw_bytes=raw_bytes,
            filename=filename,
            similarity_threshold=similarity_threshold,
            collection_ids=collection_ids,
            document_parse_mode=document_parse_mode,
            document_parse_options=document_parse_options,
        )


async def _build_task_workspace_async(
    raw_bytes: bytes,
    filename: str,
    similarity_threshold: float,
    collection_ids: list[UUID] | None = None,
    document_parse_mode: str = DOCUMENT_PARSE_MODE_FULL,
    document_parse_options: dict[str, object] | str | None = None,
) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(
            _build_task_workspace_with_new_session,
            raw_bytes=raw_bytes,
            filename=filename,
            similarity_threshold=similarity_threshold,
            collection_ids=collection_ids,
            document_parse_mode=document_parse_mode,
            document_parse_options=document_parse_options,
        ),
    )


def _begin_repeatable_read_snapshot(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})


def _import_task_cache_key(task_id: str) -> str:
    return import_task_cache_key(task_id)


def _set_import_task_status(
    task_id: str,
    status: Literal["queued", "running", "completed", "failed", "canceling", "canceled"],
    *,
    progress: int = 0,
    message: str = "",
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return shared_set_import_task_status(
        task_id,
        status,
        progress=progress,
        message=message,
        result=result,
        error=error,
    )


def _get_import_task_status(task_id: str) -> dict[str, Any] | None:
    return shared_get_import_task_status(task_id)


def _build_arq_redis_settings(redis_url: str):
    if RedisSettings is None:
        return None

    parsed = urlparse(redis_url)
    database = int((parsed.path or "/0").lstrip("/") or "0")
    kwargs = {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 6379,
        "database": database,
        "password": unquote(parsed.password) if parsed.password else None,
        "ssl": parsed.scheme == "rediss",
    }
    if parsed.username:
        kwargs["username"] = unquote(parsed.username)
    try:
        return RedisSettings(**kwargs)
    except TypeError:
        kwargs.pop("username", None)
        return RedisSettings(**kwargs)


async def _close_arq_pool(redis_pool) -> None:
    close = getattr(redis_pool, "close", None)
    if close is not None:
        close_result = close()
        if asyncio.iscoroutine(close_result):
            await close_result

    wait_closed = getattr(redis_pool, "wait_closed", None)
    if wait_closed is not None:
        wait_result = wait_closed()
        if asyncio.iscoroutine(wait_result):
            await wait_result


async def _enqueue_arq_import_task(task_id: str, payload: dict[str, Any]) -> bool:
    settings = get_settings()
    if settings.import_queue_backend.lower() != "arq":
        return False
    if not settings.redis_url or arq_create_pool is None:
        return False

    redis_settings = _build_arq_redis_settings(settings.redis_url)
    if redis_settings is None:
        return False

    redis_pool = None
    try:
        redis_pool = await arq_create_pool(redis_settings)
        await redis_pool.enqueue_job(
            "process_import_task_job",
            task_id,
            payload,
            _queue_name=ARQ_MAINTENANCE_QUEUE_NAME,
        )
        return True
    except Exception:
        logger.warning("enqueue ARQ import task failed, fallback to local background task", exc_info=True)
        return False
    finally:
        if redis_pool is not None:
            await _close_arq_pool(redis_pool)


async def _enqueue_arq_job(
    function_name: str,
    *args: Any,
    queue_name: str = ARQ_MAINTENANCE_QUEUE_NAME,
) -> bool:
    """通用 ARQ 任务入队：成功返回 True，未启用/失败返回 False 以便回退本地执行。"""
    settings = get_settings()
    if settings.import_queue_backend.lower() != "arq":
        return False
    if not settings.redis_url or arq_create_pool is None:
        return False

    redis_settings = _build_arq_redis_settings(settings.redis_url)
    if redis_settings is None:
        return False

    redis_pool = None
    try:
        redis_pool = await arq_create_pool(redis_settings)
        await redis_pool.enqueue_job(function_name, *args, _queue_name=queue_name)
        return True
    except Exception:
        logger.warning(
            "enqueue ARQ job %s failed, fallback to local execution", function_name, exc_info=True
        )
        return False
    finally:
        if redis_pool is not None:
            await _close_arq_pool(redis_pool)


async def _dispatch_spelling_grammar_qa_segments(
    file_record_id: UUID, segment_ids: list[UUID]
) -> None:
    """优先把拼写语法 QA 投递到 arq worker 执行，未启用 arq 时回退到本地线程池。

    这样 LanguageTool 的网络请求与相关数据库写入都发生在独立 worker 进程，
    不再占用 web 进程的数据库连接池。
    """
    if not segment_ids:
        return
    if await _enqueue_arq_job(
        "spelling_grammar_qa_segments_job",
        str(file_record_id),
        [str(segment_id) for segment_id in segment_ids],
    ):
        return
    await asyncio.to_thread(run_spelling_grammar_qa_for_segment_ids, file_record_id, segment_ids)


async def _dispatch_spelling_grammar_qa_project(project_id: UUID) -> None:
    if await _enqueue_arq_job("spelling_grammar_qa_project_job", str(project_id)):
        return
    await asyncio.to_thread(run_spelling_grammar_qa_for_project, project_id)


async def _dispatch_auto_tm_background() -> None:
    """优先把 auto-TM 处理投递到 arq worker，未启用 arq 时回退到本地线程池。"""
    if await _enqueue_arq_job("auto_tm_background_job"):
        return
    await asyncio.to_thread(run_auto_tm_background_once)


async def _dispatch_auto_tm_rematch_background() -> None:
    """强制处理已入队的 TM 重匹配任务，用于项目绑定后的初始刷新。"""
    if await _enqueue_arq_job("auto_tm_rematch_background_job"):
        return
    await asyncio.to_thread(run_auto_tm_rematch_background_once)


async def _queue_import_task(
    background_tasks: BackgroundTasks,
    payload: dict[str, Any],
    *,
    staging_files: list[tuple[str, bytes]] | None = None,
    staging_file: tuple[str, bytes] | None = None,
    staging_upload_files: list[tuple[str, Any]] | None = None,
) -> JSONResponse:
    cleanup_expired_import_staging()
    task_id = str(uuid4())
    try:
        if staging_upload_files is not None:
            payload = {
                **payload,
                "files": await asyncio.to_thread(
                    stage_import_file_streams,
                    task_id,
                    staging_upload_files,
                ),
            }
        elif staging_files is not None:
            payload = {
                **payload,
                "files": stage_import_file_payloads(task_id, staging_files),
            }
        elif staging_file is not None:
            filename, raw_bytes = staging_file
            payload = {
                **payload,
                "file": stage_import_file_payload(task_id, filename, raw_bytes),
            }
        payload["staging_task_id"] = task_id

        _set_import_task_status(task_id, "queued", progress=0, message="任务已进入导入队列。")
        if not await _enqueue_arq_import_task(task_id, payload):
            background_tasks.add_task(_run_import_task, task_id, payload)

        return JSONResponse(
            status_code=202,
            content={
                "task_id": task_id,
                "status": "queued",
                "progress": 0,
                "message": "任务已进入导入队列。",
            },
        )
    except UploadLimitError as exc:
        cleanup_import_task_staging(task_id)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except Exception:
        cleanup_import_task_staging(task_id)
        raise


async def _queue_tm_resource_import_task(task_id: str, payload: dict[str, Any]) -> None:
    if await _enqueue_arq_job("tm_resource_import_job", task_id, payload):
        return
    future = _RESOURCE_IMPORT_EXECUTOR.submit(_run_tm_resource_import_task, task_id, payload)
    future.add_done_callback(_log_local_tm_resource_import_failure(task_id))


def _log_local_tm_resource_import_failure(task_id: str):
    def _callback(done: Any) -> None:
        try:
            exc = done.exception()
        except CancelledError:
            return
        if exc is not None:
            logger.error(
                "local TM resource import task failed task_id=%s",
                task_id,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    return _callback


def _uuid_list(values: list[str] | None) -> list[UUID]:
    return [UUID(value) for value in values or []]


def _load_file_record_uuid_ids(
    file_record: FileRecord,
    field_name: str,
    *,
    legacy_field_name: str | None = None,
) -> list[UUID]:
    raw_ids = getattr(file_record, field_name, "") or "[]"
    parsed_ids: list[UUID] = []
    try:
        values = json.loads(raw_ids)
    except (TypeError, ValueError):
        values = []
    if isinstance(values, list):
        for value in values:
            try:
                parsed_ids.append(value if isinstance(value, UUID) else UUID(str(value)))
            except (TypeError, ValueError):
                continue
    legacy_id = getattr(file_record, legacy_field_name, None) if legacy_field_name else None
    if not parsed_ids and legacy_id:
        parsed_ids.append(legacy_id if isinstance(legacy_id, UUID) else UUID(str(legacy_id)))
    return list(dict.fromkeys(parsed_ids))


def _store_file_record_uuid_ids(
    file_record: FileRecord,
    field_name: str,
    ids: list[UUID],
    *,
    legacy_field_name: str | None = None,
) -> None:
    normalized_ids = list(dict.fromkeys(ids))
    if legacy_field_name:
        setattr(file_record, legacy_field_name, normalized_ids[0] if normalized_ids else None)
    if hasattr(file_record, field_name):
        setattr(file_record, field_name, json.dumps([str(item_id) for item_id in normalized_ids]))


def _load_file_record_collection_ids(file_record: FileRecord) -> list[UUID]:
    return _load_file_record_uuid_ids(
        file_record,
        "collection_ids_json",
        legacy_field_name="collection_id",
    )


def _store_file_record_collection_ids(file_record: FileRecord, collection_ids: list[UUID]) -> None:
    _store_file_record_uuid_ids(
        file_record,
        "collection_ids_json",
        collection_ids,
        legacy_field_name="collection_id",
    )


def _load_file_record_term_base_ids(file_record: FileRecord) -> list[UUID]:
    return _load_file_record_uuid_ids(
        file_record,
        "term_base_ids",
        legacy_field_name="term_base_id",
    )


def _load_file_record_term_base_write_ids(file_record: FileRecord) -> list[UUID]:
    return _load_file_record_uuid_ids(file_record, "term_base_write_ids")


def _load_file_record_qa_term_base_ids(file_record: FileRecord) -> list[UUID]:
    return _load_file_record_uuid_ids(file_record, "qa_term_base_ids")


def _load_file_record_glossary_base_ids(file_record: FileRecord) -> list[UUID]:
    return _load_file_record_uuid_ids(file_record, "glossary_base_ids")


def _store_file_record_term_base_ids(file_record: FileRecord, term_base_ids: list[UUID]) -> None:
    normalized_ids = list(dict.fromkeys(term_base_ids))
    _store_file_record_uuid_ids(
        file_record,
        "term_base_ids",
        normalized_ids,
        legacy_field_name="term_base_id",
    )
    enabled_set = set(normalized_ids)
    _store_file_record_term_base_write_ids(
        file_record,
        [term_base_id for term_base_id in _load_file_record_term_base_write_ids(file_record) if term_base_id in enabled_set],
    )
    _store_file_record_qa_term_base_ids(
        file_record,
        [term_base_id for term_base_id in _load_file_record_qa_term_base_ids(file_record) if term_base_id in enabled_set],
    )


def _store_file_record_term_base_write_ids(file_record: FileRecord, term_base_ids: list[UUID]) -> None:
    _store_file_record_uuid_ids(file_record, "term_base_write_ids", term_base_ids)


def _store_file_record_qa_term_base_ids(file_record: FileRecord, term_base_ids: list[UUID]) -> None:
    _store_file_record_uuid_ids(file_record, "qa_term_base_ids", term_base_ids)


def _store_file_record_glossary_base_ids(file_record: FileRecord, glossary_base_ids: list[UUID]) -> None:
    _store_file_record_uuid_ids(file_record, "glossary_base_ids", glossary_base_ids)


def _serialize_file_record_upload_result(file_record: FileRecord) -> dict[str, Any]:
    return {
        "id": str(file_record.id),
        "filename": file_record.filename,
        "status": file_record.status,
        "document_parse_mode": file_record.document_parse_mode,
        "document_parse_options": _get_file_record_document_parse_options(file_record),
        "document_statistics": get_file_record_document_statistics(file_record),
        "created_at": file_record.created_at.isoformat(),
    }


def _json_ready_project_file_payload(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("collection_id", "term_base_id"):
        if payload.get(key) is not None:
            payload[key] = str(payload[key])
    return payload


def _get_file_record_document_parse_options(file_record: FileRecord) -> dict[str, object]:
    return normalize_document_parse_options(
        getattr(file_record, "document_parse_options", None),
        getattr(file_record, "document_parse_mode", DOCUMENT_PARSE_MODE_FULL),
    )


def _process_file_record_import(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    file_payload = payload["file"]
    raw_bytes = read_import_file_bytes(file_payload)
    filename = file_payload["filename"] or "untitled.txt"
    selected_collection_ids = _uuid_list(payload.get("collection_ids"))
    term_base_id = UUID(payload["term_base_id"]) if payload.get("term_base_id") else None
    document_parse_mode = payload.get("document_parse_mode") or DOCUMENT_PARSE_MODE_FULL
    document_parse_options = payload.get("document_parse_options")
    threshold = float(payload.get("threshold", 0.6))

    primary_collection = _get_collection_or_404(
        db,
        selected_collection_ids[0] if selected_collection_ids else None,
    )
    resolved_source_language, resolved_target_language = _resolve_upload_language_pair(
        payload.get("source_language"),
        payload.get("target_language"),
        primary_collection,
    )

    term_base = None
    if term_base_id is not None:
        term_base = db.query(TermBase).filter(TermBase.id == term_base_id).first()
        if term_base is None:
            raise HTTPException(status_code=404, detail="术语库不存在。")
        _ensure_resource_language_pair_matches(
            term_base,
            resolved_source_language,
            resolved_target_language,
            "术语库",
        )

    workspace_data = build_task_workspace(
        db=db,
        raw_bytes=raw_bytes,
        filename=filename,
        similarity_threshold=threshold,
        collection_ids=selected_collection_ids,
        document_parse_mode=document_parse_mode,
        document_parse_options=document_parse_options,
    )
    file_record = create_file_record_with_segments(
        db=db,
        raw_bytes=raw_bytes,
        filename=filename,
        similarity_threshold=threshold,
        workspace_data=workspace_data,
        collection_ids=selected_collection_ids,
        document_parse_mode=document_parse_mode,
        document_parse_options=document_parse_options,
    )
    if selected_collection_ids:
        file_record.collection_id = selected_collection_ids[0]
        file_record.collection_ids_json = json.dumps([str(cid) for cid in selected_collection_ids])
        _apply_collection_language_pair(file_record, primary_collection)
    file_record.source_language = resolved_source_language
    file_record.target_language = resolved_target_language
    if payload.get("creator_id"):
        file_record.creator_id = UUID(payload["creator_id"])
    if term_base is not None:
        _store_file_record_term_base_ids(file_record, [term_base_id])

    db.commit()
    db.refresh(file_record)
    return _serialize_file_record_upload_result(file_record)


def _expand_archive_payloads(file_payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """展开压缩包文件为独立文件列表。

    如果上传的文件是 .zip 或 .rar，则解压其中支持的文件，
    每个文件作为独立的 file_payload 返回。非压缩包文件原样保留。
    """
    import zipfile
    from io import BytesIO

    expanded: list[dict[str, Any]] = []

    for file_payload in file_payloads:
        filename = file_payload.get("filename") or ""
        ext = Path(filename).suffix.lower()
        raw_bytes = read_import_file_bytes(file_payload)

        if ext == ".zip":
            extracted = _extract_zip_files(raw_bytes, filename)
            if extracted:
                expanded.extend(extracted)
            else:
                # 如果解压后没有支持的文件，保留原始 zip 处理方式
                expanded.append(file_payload)
        elif ext == ".rar":
            extracted = _extract_rar_files(raw_bytes, filename)
            if extracted:
                expanded.extend(extracted)
            else:
                expanded.append(file_payload)
        else:
            expanded.append(file_payload)

    return expanded


def _extract_zip_files(raw_bytes: bytes, archive_name: str) -> list[dict[str, Any]]:
    """从 ZIP 文件中提取支持的文件。"""
    import zipfile
    from io import BytesIO

    try:
        zf = zipfile.ZipFile(BytesIO(raw_bytes), 'r')
    except zipfile.BadZipFile:
        return []

    extracted: list[dict[str, Any]] = []

    for info in zf.infolist():
        # 跳过目录
        if info.is_dir():
            continue

        # 获取文件名，处理编码问题
        raw_filename = info.filename
        decoded_filename = _decode_zip_filename(raw_filename, info)

        # 跳过隐藏文件和系统文件
        basename = Path(decoded_filename).name
        if basename.startswith('__') or basename.startswith('.'):
            continue

        # 检查是否是支持的格式
        if not supports_task_file(basename):
            continue

        try:
            file_bytes = zf.read(info.filename)
            if file_bytes:
                extracted.append({
                    "filename": basename,
                    "content": file_bytes,
                })
        except Exception:
            continue

    zf.close()
    return extracted


def _extract_rar_files(raw_bytes: bytes, archive_name: str) -> list[dict[str, Any]]:
    """从 RAR 文件中提取支持的文件。"""
    try:
        import rarfile
        from io import BytesIO

        rf = rarfile.RarFile(BytesIO(raw_bytes))
    except Exception:
        return []

    extracted: list[dict[str, Any]] = []

    try:
        for info in rf.infolist():
            if info.is_dir():
                continue

            decoded_filename = info.filename
            basename = Path(decoded_filename).name

            if basename.startswith('__') or basename.startswith('.'):
                continue

            if not supports_task_file(basename):
                continue

            try:
                file_bytes = rf.read(info.filename)
                if file_bytes:
                    extracted.append({
                        "filename": basename,
                        "content": file_bytes,
                    })
            except Exception:
                continue
    finally:
        rf.close()

    return extracted


def _decode_zip_filename(raw_filename: str, info: Any) -> str:
    """解码 ZIP 文件名，处理中文等非 ASCII 编码问题。

    Python zipfile 模块对非 UTF-8 文件名使用 CP437 解码，
    这会导致中文文件名乱码。此函数尝试修复。
    """
    # 如果 flag_bits 的 bit 11 设置了，说明文件名是 UTF-8 编码
    if info.flag_bits & 0x800:
        return raw_filename

    # 尝试将 CP437 解码的字符串重新编码回字节，再用正确编码解码
    try:
        raw_bytes = raw_filename.encode('cp437')
        # 尝试 UTF-8
        try:
            return raw_bytes.decode('utf-8')
        except UnicodeDecodeError:
            pass
        # 尝试 GBK（中文 Windows 常用）
        try:
            return raw_bytes.decode('gbk')
        except UnicodeDecodeError:
            pass
        # 尝试 Shift-JIS（日文）
        try:
            return raw_bytes.decode('shift_jis')
        except UnicodeDecodeError:
            pass
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass

    return raw_filename


def _process_project_source_import(db: Session, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = UUID(payload["project_id"])
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在。")

    selected_collection_ids = _uuid_list(payload.get("collection_ids"))
    term_base_id = UUID(payload["term_base_id"]) if payload.get("term_base_id") else None
    document_parse_mode = payload.get("document_parse_mode") or DOCUMENT_PARSE_MODE_FULL
    document_parse_options = payload.get("document_parse_options")
    threshold = float(payload.get("threshold", 0.6))
    primary_collection = _get_collection_or_404(
        db,
        selected_collection_ids[0] if selected_collection_ids else None,
    )
    resolved_source_language, resolved_target_language = _resolve_upload_language_pair(
        payload.get("source_language"),
        payload.get("target_language"),
        primary_collection,
    )

    term_base = None
    if term_base_id is not None:
        term_base = db.query(TermBase).filter(TermBase.id == term_base_id).first()
        if term_base is None:
            raise HTTPException(status_code=404, detail="术语库不存在。")
        _ensure_resource_language_pair_matches(
            term_base,
            resolved_source_language,
            resolved_target_language,
            "术语库",
        )

    file_payloads = payload["files"]

    # 展开压缩包：将 zip/rar 文件解压为独立文件
    expanded_payloads = _expand_archive_payloads(file_payloads)
    validate_expanded_upload_batch(expanded_payloads)

    created_files: list[FileRecord] = []
    for index, file_payload in enumerate(expanded_payloads, start=1):
        raise_if_import_task_canceled(task_id)
        filename = file_payload["filename"] or "source.txt"
        raw_bytes = read_import_file_bytes(file_payload)
        _set_import_task_status(
            task_id,
            "running",
            progress=10 + int((index - 1) / max(len(expanded_payloads), 1) * 80),
            message=f"正在解析 {filename}",
        )
        workspace_data = build_task_workspace(
            db=db,
            raw_bytes=raw_bytes,
            filename=filename,
            similarity_threshold=threshold,
            collection_ids=selected_collection_ids,
            document_parse_mode=document_parse_mode,
            document_parse_options=document_parse_options,
        )
        file_record = create_file_record_with_segments(
            db=db,
            raw_bytes=raw_bytes,
            filename=filename,
            similarity_threshold=threshold,
            workspace_data=workspace_data,
            collection_ids=selected_collection_ids,
            document_parse_mode=document_parse_mode,
            document_parse_options=document_parse_options,
        )
        file_record.project_id = project.id
        file_record.creator_id = project.creator_id
        file_record.deadline = project.deadline
        file_record.access_level = project.access_level
        file_record.source_language = resolved_source_language
        file_record.target_language = resolved_target_language
        if selected_collection_ids:
            file_record.collection_id = selected_collection_ids[0]
            file_record.collection_ids_json = json.dumps([str(cid) for cid in selected_collection_ids])
        if term_base is not None:
            _store_file_record_term_base_ids(file_record, [term_base_id])
        db.flush()
        _assign_file_segments_to_first_workflow_step(db, file_record)
        sync_project_repeated_segments_from_file(
            db,
            file_record=file_record,
            current_user=None,
        )
        created_files.append(file_record)

    project.status = "in_progress"
    db.commit()
    for file_record in created_files:
        db.refresh(file_record)

    file_stats = _get_file_segment_stats(db, [file_record.id for file_record in created_files])
    return {
        "id": str(project.id),
        "status": project.status,
        "filename": project.name,
        "uploaded_count": len(created_files),
        "files": [
            _json_ready_project_file_payload(
                _build_project_file_payload(
                    file_record=file_record,
                    total_segments=file_stats.get(file_record.id, {"total": 0})["total"],
                    translated_segments=file_stats.get(file_record.id, {"filled": 0})["filled"],
                    pretranslated_segments=file_stats.get(file_record.id, {}).get("pretranslated", 0),
                )
            )
            for file_record in created_files
        ],
    }


def _run_import_task(task_id: str, payload: dict[str, Any]) -> None:
    staging_task_id = str(payload.get("staging_task_id") or task_id)
    _set_import_task_status(task_id, "running", progress=5, message="导入任务开始处理。")
    try:
        with SessionLocal() as db:
            try:
                raise_if_import_task_canceled(task_id)
                if payload.get("kind") == "project_source_document":
                    result = _process_project_source_import(db, task_id, payload)
                else:
                    result = _process_file_record_import(db, payload)
                raise_if_import_task_canceled(task_id)
                _set_import_task_status(
                    task_id,
                    "completed",
                    progress=100,
                    message="导入完成。",
                    result=result,
                )
            except ImportTaskCanceled:
                db.rollback()
                _set_import_task_status(
                    task_id,
                    "canceled",
                    progress=100,
                    message="导入已取消。",
                    error=None,
                )
            except HTTPException as exc:
                db.rollback()
                _set_import_task_status(
                    task_id,
                    "failed",
                    progress=100,
                    message="导入失败。",
                    error=str(exc.detail),
                )
            except UploadLimitError as exc:
                db.rollback()
                _set_import_task_status(
                    task_id,
                    "failed",
                    progress=100,
                    message="导入失败。",
                    error=exc.detail,
                )
            except Exception as exc:
                db.rollback()
                logger.exception("import task failed task_id=%s", task_id)
                _set_import_task_status(
                    task_id,
                    "failed",
                    progress=100,
                    message="导入失败。",
                    error=str(exc),
                )
    finally:
        cleanup_import_task_staging(staging_task_id)


async def process_import_task_job(ctx, task_id: str, payload: dict[str, Any]) -> None:
    await asyncio.to_thread(_run_import_task, task_id, payload)


async def tm_resource_import_job(ctx, task_id: str, payload: dict[str, Any]) -> None:
    await asyncio.to_thread(_run_tm_resource_import_task, task_id, payload)


async def term_resource_import_job(ctx, task_id: str, payload: dict[str, Any]) -> None:
    from app.routers.term_base import _run_term_resource_import_task

    await asyncio.to_thread(_run_term_resource_import_task, task_id, payload)


async def spelling_grammar_qa_segments_job(
    ctx, file_record_id: str, segment_ids: list[str]
) -> None:
    await asyncio.to_thread(
        run_spelling_grammar_qa_for_segment_ids,
        UUID(file_record_id),
        [UUID(segment_id) for segment_id in segment_ids],
    )


async def spelling_grammar_qa_project_job(ctx, project_id: str) -> None:
    await asyncio.to_thread(run_spelling_grammar_qa_for_project, UUID(project_id))


async def auto_tm_background_job(ctx) -> None:
    await asyncio.to_thread(run_auto_tm_background_once)


async def auto_tm_rematch_background_job(ctx) -> None:
    await asyncio.to_thread(run_auto_tm_rematch_background_once)


async def _dispatch_pretranslation_run(run_id: UUID) -> None:
    if await _enqueue_arq_job(
        "pretranslation_run_job",
        str(run_id),
        queue_name=ARQ_PRETRANSLATION_QUEUE_NAME,
    ):
        return
    await asyncio.to_thread(_run_pretranslation_run, run_id)


async def pretranslation_run_job(ctx, run_id: str) -> None:
    await asyncio.to_thread(_run_pretranslation_run, UUID(run_id))


class MaintenanceWorkerSettings:
    queue_name = ARQ_MAINTENANCE_QUEUE_NAME
    max_jobs = max(
        int(getattr(get_settings(), "arq_maintenance_max_jobs", None) or get_settings().arq_max_jobs),
        1,
    )
    functions = [
        process_import_task_job,
        tm_resource_import_job,
        term_resource_import_job,
        spelling_grammar_qa_segments_job,
        spelling_grammar_qa_project_job,
        auto_tm_background_job,
        auto_tm_rematch_background_job,
    ]
    redis_settings = _build_arq_redis_settings(get_settings().redis_url or "redis://localhost:6379/0")


class PretranslationWorkerSettings:
    queue_name = ARQ_PRETRANSLATION_QUEUE_NAME
    max_jobs = max(int(get_settings().arq_pretranslation_max_jobs), 1)
    functions = [
        pretranslation_run_job,
    ]
    redis_settings = _build_arq_redis_settings(get_settings().redis_url or "redis://localhost:6379/0")


class WorkerSettings(MaintenanceWorkerSettings):
    pass


class SegmentUpdate(BaseModel):
    sentence_id: str
    target_text: str
    target_html: str | None = None
    source: str = "manual"
    track_revision: bool = True
    base_version: int | None = None
    confirm: bool = False


class SegmentSourceUpdate(BaseModel):
    source_text: str


class SegmentProjectSyncUpdate(BaseModel):
    disabled: bool


class ProjectSyncDisableResponse(BaseModel):
    updated_count: int
    disabled_count: int
    cleared_count: int


class BatchSegmentUpdate(BaseModel):
    updates: list[SegmentUpdate]


class SegmentConfirmationBatchUpdate(BaseModel):
    action: Literal["confirm", "cancel"]
    range_start: int | None = Field(default=None, ge=1, description="可选：句段编号范围起始值")
    range_end: int | None = Field(default=None, ge=1, description="可选：句段编号范围结束值")


class SegmentSplitRequest(BaseModel):
    """拆分句段请求"""
    split_offset: int = Field(..., ge=1, description="在 source_text 中的拆分位置（字符偏移）")


class SegmentMergeRequest(BaseModel):
    """合并句段请求"""
    target_sentence_id: str = Field(..., description="要合并的下一个句段的 sentence_id")


class SegmentReplaceRequest(BaseModel):
    scope: str = "all"
    source_query: str = ""
    target_query: str
    source_exclude: str = ""
    target_exclude: str = ""
    replace_text: str = ""
    search_fuzzy: bool = False
    case_sensitive: bool = False
    replace_all: bool = True
    status_filters: list[str] = Field(default_factory=list)
    match_filters: list[str] = Field(default_factory=list)
    source_filters: list[str] = Field(default_factory=list)
    workflow_step_ids: list[str] = Field(default_factory=list)


class RevisionResolvePayload(BaseModel):
    status: Literal["accepted", "rejected"]


class RevisionAuthorColorPayload(BaseModel):
    insert: str = Field(default=DEFAULT_INSERT_COLOR, max_length=20)
    delete: str = Field(default=DEFAULT_DELETE_COLOR, max_length=20)


class RevisionDisplaySettingsPayload(BaseModel):
    show_author_time: bool = True
    show_others_revisions: bool = True
    default_insert_color: str = Field(default=DEFAULT_INSERT_COLOR, max_length=20)
    default_delete_color: str = Field(default=DEFAULT_DELETE_COLOR, max_length=20)
    author_colors: dict[str, RevisionAuthorColorPayload] = Field(default_factory=dict)


class FileOperationLockRequest(BaseModel):
    operation: Literal["pre_translate"] = PRE_TRANSLATE_OPERATION


class LLMTranslateRequest(BaseModel):
    scope: Literal["current_segment", "fuzzy_only", "none_only", "empty_target_only", "all", "all_with_exact"] = "all"
    provider: Literal["auto", "deepseek", "openrouter"] = "openrouter"
    model: str | None = Field(default=None, max_length=120)
    sentence_id: str | None = None
    translation_unit: Literal["paragraph", "sentence"] = "paragraph"
    translation_guidelines: str = ""
    guideline_template_id: str | None = None
    temporary_prompt: str = ""
    glossary_base_ids: list[UUID] | None = None


class TermExtractionRequest(BaseModel):
    term_base_id: UUID | None = None
    max_terms: int = Field(default=150, ge=1, le=300)
    models: list[str] = Field(default_factory=lambda: [TERM_EXTRACTION_MODEL])
    extraction_prompt: str = Field(default="", max_length=4000)


class TermExtractionSaveEntry(BaseModel):
    source_text: str
    target_text: str
    action: Literal["add", "replace", "skip"] = "add"


class TermExtractionSaveRequest(BaseModel):
    term_base_id: UUID
    entries: list[TermExtractionSaveEntry]


class GuidelineTemplateUpdateRequest(BaseModel):
    content: str


class RematchRequest(BaseModel):
    collection_ids: list[UUID]
    threshold: float = 0.75
    skip_confirmed: bool = True
    overwrite_fuzzy: bool = True
    auto_confirm_exact: bool = True


class FileRecordBindingsRequest(BaseModel):
    term_base_id: UUID | None = None
    term_base_ids: list[UUID] | None = None
    term_base_write_ids: list[UUID] | None = None
    qa_term_base_ids: list[UUID] | None = None
    glossary_base_ids: list[UUID] | None = None
    collection_id: UUID | None = None
    collection_ids: list[UUID] | None = None
    tm_match_threshold: float | None = None


class PretranslationRunRequest(BaseModel):
    file_ids: list[UUID] = Field(default_factory=list)
    use_tm: bool = True
    tm_collection_ids: list[UUID] = Field(default_factory=list)
    tm_threshold: float = 0.75
    tm_skip_confirmed: bool = True
    tm_overwrite_fuzzy: bool = True
    tm_auto_confirm_exact: bool = True
    use_glossary: bool = False
    glossary_base_ids: list[UUID] = Field(default_factory=list)
    use_term_base: bool = False
    term_base_ids: list[UUID] = Field(default_factory=list)
    use_llm: bool = False
    llm_scope: Literal["fuzzy_only", "none_only", "empty_target_only", "all", "all_with_exact"] = "all"
    llm_provider: Literal["auto", "deepseek", "openrouter"] = "openrouter"
    llm_model: str | None = Field(default=None, max_length=120)
    llm_translation_unit: Literal["paragraph", "sentence"] = "paragraph"
    guideline_template_id: str | None = None
    temporary_prompt: str = Field(default="", max_length=8000)


PRETRANSLATION_ACTIVE_STATUSES = {"queued", "running", "canceling"}
PRETRANSLATION_TERMINAL_STATUSES = {"completed", "failed", "canceled"}


class PretranslationCanceled(Exception):
    pass


class _BackgroundLLMRequest:
    def __init__(self, task_id: UUID):
        self.task_id = task_id

    async def is_disconnected(self) -> bool:
        return _pretranslation_task_cancel_requested(self.task_id)

    def is_cancel_requested(self) -> bool:
        return _pretranslation_task_cancel_requested(self.task_id)


def _pretranslation_now() -> datetime:
    return datetime.now()


def _load_pretranslation_options(run: PretranslationRun | None) -> dict[str, Any]:
    if run is None:
        return {}
    try:
        value = json.loads(run.options_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _serialize_pretranslation_task(task: PretranslationTask) -> dict[str, Any]:
    file_record = task.file_record
    return {
        "id": str(task.id),
        "run_id": str(task.run_id),
        "file_record_id": str(task.file_record_id),
        "project_id": str(file_record.project_id) if file_record and file_record.project_id else None,
        "filename": file_record.filename if file_record else None,
        "status": task.status,
        "stage": task.stage,
        "progress": int(task.progress or 0),
        "message": task.message or "",
        "provider": task.provider,
        "model": task.model,
        "scope": task.scope,
        "total_segments": int(task.total_segments or 0),
        "unique_segments": int(task.unique_segments or 0),
        "deduplicated_segments": int(task.deduplicated_segments or 0),
        "processed_segments": int(task.processed_segments or 0),
        "updated_segments": int(task.updated_segments or 0),
        "error_segments": int(task.error_segments or 0),
        "current_action": task.current_action,
        "cancel_requested": bool(task.cancel_requested),
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "last_heartbeat_at": task.last_heartbeat_at.isoformat() if task.last_heartbeat_at else None,
    }


def _refresh_pretranslation_run_status(db: Session, run: PretranslationRun) -> None:
    tasks = (
        db.query(PretranslationTask)
        .filter(PretranslationTask.run_id == run.id)
        .all()
    )
    total = len(tasks)
    completed = sum(1 for task in tasks if task.status == "completed")
    failed = sum(1 for task in tasks if task.status == "failed")
    canceled = sum(1 for task in tasks if task.status == "canceled")
    active = sum(1 for task in tasks if task.status in PRETRANSLATION_ACTIVE_STATUSES)
    run.total_files = total
    run.completed_files = completed
    run.failed_files = failed
    run.canceled_files = canceled
    run.progress = int(sum(int(task.progress or 0) for task in tasks) / total) if total else 100
    run.updated_at = _pretranslation_now()
    if active:
        run.status = "running"
        if run.started_at is None:
            run.started_at = _pretranslation_now()
        run.completed_at = None
        run.message = "预翻译正在后台执行"
    else:
        if failed:
            run.status = "failed"
            run.message = "预翻译完成，但存在失败文件"
        elif canceled and canceled == total:
            run.status = "canceled"
            run.message = "预翻译已取消"
        else:
            run.status = "completed"
            run.message = "预翻译已完成"
        if run.started_at is None:
            run.started_at = _pretranslation_now()
        if run.completed_at is None:
            run.completed_at = _pretranslation_now()


def _serialize_pretranslation_run(
    run: PretranslationRun,
    *,
    extra_tasks: list[PretranslationTask] | None = None,
) -> dict[str, Any]:
    tasks_by_id: dict[UUID, PretranslationTask] = {}
    for task in list(run.tasks or []):
        tasks_by_id[task.id] = task
    for task in extra_tasks or []:
        tasks_by_id[task.id] = task
    tasks = sorted(tasks_by_id.values(), key=lambda item: (item.created_at or datetime.min, str(item.id)))
    return {
        "id": str(run.id),
        "project_id": str(run.project_id),
        "status": run.status,
        "progress": int(run.progress or 0),
        "message": run.message or "",
        "total_files": int(run.total_files or 0),
        "completed_files": int(run.completed_files or 0),
        "failed_files": int(run.failed_files or 0),
        "canceled_files": int(run.canceled_files or 0),
        "created_by_id": str(run.created_by_id) if run.created_by_id else None,
        "created_by": serialize_user(run.created_by) if run.created_by else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "tasks": [_serialize_pretranslation_task(task) for task in tasks],
    }


def _update_pretranslation_task(task_id: UUID, **values: Any) -> None:
    with SessionLocal() as db:
        task = db.query(PretranslationTask).filter(PretranslationTask.id == task_id).first()
        if task is None:
            return
        now = _pretranslation_now()
        for key, value in values.items():
            setattr(task, key, value)
        task.updated_at = now
        if task.status in {"running", "canceling"} and task.started_at is None:
            task.started_at = now
        if task.status in PRETRANSLATION_TERMINAL_STATUSES and task.completed_at is None:
            task.completed_at = now
            task.progress = 100
        run = db.query(PretranslationRun).filter(PretranslationRun.id == task.run_id).first()
        if run is not None:
            _refresh_pretranslation_run_status(db, run)
        db.commit()


def _pretranslation_task_cancel_requested(task_id: UUID) -> bool:
    with SessionLocal() as db:
        task = db.query(PretranslationTask).filter(PretranslationTask.id == task_id).first()
        return task is None or bool(task.cancel_requested) or task.status == "canceled"


def _touch_pretranslation_task_heartbeat(task_id: UUID) -> None:
    with SessionLocal() as db:
        task = db.query(PretranslationTask).filter(PretranslationTask.id == task_id).first()
        if task is None or task.file_record is None:
            return
        task.last_heartbeat_at = _pretranslation_now()
        if task.operation_token:
            heartbeat_file_operation_lock(
                db,
                task.file_record,
                operation_token=task.operation_token,
            )
        else:
            db.commit()


def _release_pretranslation_task_lock(task_id: UUID) -> None:
    with SessionLocal() as db:
        task = db.query(PretranslationTask).filter(PretranslationTask.id == task_id).first()
        if task is None or task.file_record is None or not task.operation_token:
            return
        try:
            release_file_operation_lock(
                db,
                task.file_record,
                operation_token=task.operation_token,
            )
        except HTTPException:
            logger.warning("release pretranslation lock failed task_id=%s", task_id, exc_info=True)


def _pretranslation_options_for_run(task_id: UUID) -> dict[str, Any]:
    with SessionLocal() as db:
        task = db.query(PretranslationTask).filter(PretranslationTask.id == task_id).first()
        return _load_pretranslation_options(task.run if task else None)


def _run_pretranslation_binding_stage(task_id: UUID, options: dict[str, Any]) -> None:
    binding_payload: dict[str, Any] = {}
    if options.get("use_tm") and options.get("tm_collection_ids"):
        collection_ids = [UUID(str(item)) for item in options.get("tm_collection_ids") or []]
        binding_payload["collection_ids"] = collection_ids
        binding_payload["collection_id"] = collection_ids[0] if collection_ids else None
        binding_payload["tm_match_threshold"] = options.get("tm_threshold", 0.75)
    if options.get("use_glossary"):
        binding_payload["glossary_base_ids"] = [
            UUID(str(item)) for item in options.get("glossary_base_ids") or []
        ]
    if options.get("use_term_base"):
        binding_payload["term_base_ids"] = [
            UUID(str(item)) for item in options.get("term_base_ids") or []
        ]
    if not binding_payload:
        return

    _update_pretranslation_task(
        task_id,
        status="running",
        stage="bindings",
        progress=8,
        message="正在绑定预翻译资源",
        current_action="bindings",
    )
    _touch_pretranslation_task_heartbeat(task_id)
    with SessionLocal() as db:
        task = db.query(PretranslationTask).filter(PretranslationTask.id == task_id).first()
        if task is None or task.file_record is None or task.run is None:
            raise RuntimeError("预翻译任务不存在")
        user = db.query(User).filter(User.id == task.run.created_by_id).first()
        if user is None:
            raise RuntimeError("预翻译发起用户不存在")
        patch_file_record_bindings(
            task.file_record_id,
            FileRecordBindingsRequest(**binding_payload),
            db,
            user,
            operation_token=task.operation_token,
            refresh_tm_matches=False,
        )


def _run_pretranslation_tm_stage(task_id: UUID, options: dict[str, Any]) -> None:
    if not options.get("use_tm"):
        return
    collection_ids = [UUID(str(item)) for item in options.get("tm_collection_ids") or []]
    if not collection_ids:
        return

    _update_pretranslation_task(
        task_id,
        status="running",
        stage="tm",
        progress=18,
        message="正在执行 TM 预匹配",
        current_action="tm",
    )
    _touch_pretranslation_task_heartbeat(task_id)
    with SessionLocal() as db:
        task = db.query(PretranslationTask).filter(PretranslationTask.id == task_id).first()
        if task is None or task.file_record is None or task.run is None:
            raise RuntimeError("预翻译任务不存在")
        user = db.query(User).filter(User.id == task.run.created_by_id).first()
        if user is None:
            raise RuntimeError("预翻译发起用户不存在")
        signature = build_tm_match_signature(
            db,
            file_record_id=task.file_record_id,
            collection_ids=collection_ids,
            threshold=float(options.get("tm_threshold") or 0.75),
            skip_confirmed=bool(options.get("tm_skip_confirmed", True)),
            overwrite_fuzzy=bool(options.get("tm_overwrite_fuzzy", True)),
            auto_confirm_exact=bool(options.get("tm_auto_confirm_exact", True)),
        )
        if is_tm_match_signature_current(task.file_record, signature):
            _update_pretranslation_task(
                task_id,
                progress=35,
                message="TM 已是最新，跳过重复匹配",
                current_action="tm_skipped",
            )
            return
        result = rematch_file_record(
            task.file_record_id,
            RematchRequest(
                collection_ids=collection_ids,
                threshold=float(options.get("tm_threshold") or 0.75),
                skip_confirmed=bool(options.get("tm_skip_confirmed", True)),
                overwrite_fuzzy=bool(options.get("tm_overwrite_fuzzy", True)),
                auto_confirm_exact=bool(options.get("tm_auto_confirm_exact", True)),
            ),
            db,
            user,
            operation_token=task.operation_token,
        )
        db.refresh(task.file_record)
        mark_tm_match_signature_current(task.file_record, signature)
        db.commit()
    _update_pretranslation_task(
        task_id,
        progress=35,
        message=f"TM 预匹配完成，更新 {int(result.get('updated', 0))} 条",
        current_action="tm_done",
    )


def _parse_sse_events(chunk: str | bytes) -> list[tuple[str, dict[str, Any]]]:
    text = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        event_name = ""
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        if not event_name or not data_lines:
            continue
        try:
            payload = json.loads("\n".join(data_lines))
        except ValueError:
            payload = {}
        events.append((event_name, payload if isinstance(payload, dict) else {}))
    return events


async def _run_pretranslation_llm_stage(task_id: UUID, options: dict[str, Any]) -> None:
    if not options.get("use_llm"):
        return

    requested_model = normalize_text(options.get("llm_model") or "") or None
    body = LLMTranslateRequest(
        scope=options.get("llm_scope") or "all",
        provider=options.get("llm_provider") or "openrouter",
        model=requested_model,
        translation_unit=options.get("llm_translation_unit") or "paragraph",
        guideline_template_id=options.get("guideline_template_id") or None,
        temporary_prompt=options.get("temporary_prompt") or "",
        glossary_base_ids=[
            UUID(str(item)) for item in options.get("glossary_base_ids") or []
        ] if options.get("use_glossary") else None,
    )

    _update_pretranslation_task(
        task_id,
        status="running",
        stage="llm",
        progress=42,
        message="正在等待模型返回结果",
        current_action="waiting_model",
        provider=body.provider,
        model=requested_model,
        scope=body.scope,
    )
    _touch_pretranslation_task_heartbeat(task_id)

    stream_db = SessionLocal()
    try:
        task = stream_db.query(PretranslationTask).filter(PretranslationTask.id == task_id).first()
        if task is None or task.file_record is None or task.run is None:
            raise RuntimeError("预翻译任务不存在")
        user = stream_db.query(User).filter(User.id == task.run.created_by_id).first()
        if user is None:
            raise RuntimeError("预翻译发起用户不存在")
        response = await llm_translate_file_record(
            task.file_record_id,
            _BackgroundLLMRequest(task_id),
            body,
            stream_db,
            user,
            operation_token=task.operation_token,
        )
        total = 0
        processed = 0
        updated = 0
        errors = 0
        last_heartbeat = datetime.min
        async for chunk in response.body_iterator:
            for event_name, payload in _parse_sse_events(chunk):
                if event_name == "start":
                    total = int(payload.get("total") or 0)
                    _update_pretranslation_task(
                        task_id,
                        total_segments=total,
                        unique_segments=int(payload.get("unique_total") or 0),
                        deduplicated_segments=int(payload.get("deduplicated_count") or 0),
                        progress=45 if total else 92,
                        message=(
                            f"LLM 预翻译进行中：0/{total}"
                            if total
                            else "LLM 没有需要处理的句段"
                        ),
                        current_action="waiting_model" if total else "llm_skipped",
                    )
                elif event_name == "segment":
                    processed += 1
                    updated += 1
                    progress = 45 + int(min(processed, max(total, 1)) / max(total, 1) * 45)
                    _update_pretranslation_task(
                        task_id,
                        processed_segments=processed,
                        updated_segments=updated,
                        error_segments=errors,
                        progress=min(progress, 95),
                        message=f"LLM 预翻译进行中：{processed}/{total or processed}",
                        current_action="writing_result",
                    )
                elif event_name == "error":
                    processed += 1
                    errors += 1
                    progress = 45 + int(min(processed, max(total, 1)) / max(total, 1) * 45)
                    _update_pretranslation_task(
                        task_id,
                        processed_segments=processed,
                        updated_segments=updated,
                        error_segments=errors,
                        progress=min(progress, 95),
                        message=f"LLM 预翻译部分失败：{processed}/{total or processed}",
                        current_action="writing_result",
                        error=payload.get("message") or None,
                    )
                elif event_name == "complete":
                    total = int(payload.get("total") or total)
                    updated = int(payload.get("updated_count") or updated)
                    errors = int(payload.get("error_count") or errors)
                    processed = max(processed, updated + errors)
                    _update_pretranslation_task(
                        task_id,
                        processed_segments=processed,
                        updated_segments=updated,
                        error_segments=errors,
                        progress=95,
                        message="LLM 预翻译写入完成",
                        current_action="llm_done",
                    )

            if _pretranslation_task_cancel_requested(task_id):
                raise PretranslationCanceled()
            now = _pretranslation_now()
            if (now - last_heartbeat).total_seconds() >= 15:
                _touch_pretranslation_task_heartbeat(task_id)
                last_heartbeat = now
    finally:
        stream_db.close()


def _run_pretranslation_task(task_id: UUID) -> None:
    options = _pretranslation_options_for_run(task_id)
    try:
        with SessionLocal() as db:
            task = db.query(PretranslationTask).filter(PretranslationTask.id == task_id).first()
            if task is None:
                return
            if task.cancel_requested or task.status == "canceled":
                raise PretranslationCanceled()
            segment_count = (
                db.query(Segment)
                .filter(Segment.file_record_id == task.file_record_id)
                .count()
            )
        _update_pretranslation_task(
            task_id,
            status="running",
            stage="starting",
            progress=2,
            total_segments=segment_count,
            message="预翻译任务已开始",
            current_action="starting",
        )
        _touch_pretranslation_task_heartbeat(task_id)

        if _pretranslation_task_cancel_requested(task_id):
            raise PretranslationCanceled()
        _run_pretranslation_binding_stage(task_id, options)

        if _pretranslation_task_cancel_requested(task_id):
            raise PretranslationCanceled()
        _run_pretranslation_tm_stage(task_id, options)

        if _pretranslation_task_cancel_requested(task_id):
            raise PretranslationCanceled()
        asyncio.run(_run_pretranslation_llm_stage(task_id, options))

        if _pretranslation_task_cancel_requested(task_id):
            raise PretranslationCanceled()
        _update_pretranslation_task(
            task_id,
            status="completed",
            stage="completed",
            progress=100,
            message="预翻译已完成",
            current_action="completed",
        )
    except PretranslationCanceled:
        _update_pretranslation_task(
            task_id,
            status="canceled",
            stage="canceled",
            progress=100,
            message="预翻译已取消",
            current_action="canceled",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("pretranslation task failed task_id=%s", task_id)
        _update_pretranslation_task(
            task_id,
            status="failed",
            stage="failed",
            progress=100,
            message="预翻译失败",
            error=str(exc),
            current_action="failed",
        )
    finally:
        _release_pretranslation_task_lock(task_id)


def _run_pretranslation_run(run_id: UUID) -> None:
    with SessionLocal() as db:
        run = db.query(PretranslationRun).filter(PretranslationRun.id == run_id).first()
        if run is None:
            return
        run.status = "running"
        run.started_at = run.started_at or _pretranslation_now()
        run.message = "预翻译正在后台执行"
        _refresh_pretranslation_run_status(db, run)
        task_ids = [
            task.id
            for task in (
                db.query(PretranslationTask)
                .filter(PretranslationTask.run_id == run_id)
                .order_by(PretranslationTask.created_at.asc(), PretranslationTask.id.asc())
                .all()
            )
        ]
        db.commit()

    for task_id in task_ids:
        _run_pretranslation_task(task_id)

    with SessionLocal() as db:
        run = db.query(PretranslationRun).filter(PretranslationRun.id == run_id).first()
        if run is not None:
            _refresh_pretranslation_run_status(db, run)
            db.commit()


@router.post("/projects/{project_id}/pretranslation-runs")
async def create_pretranslation_run(
    project_id: UUID,
    payload: PretranslationRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_project_or_404(db, project_id)
    _require_project_read_access(project, current_user, db)
    file_ids = list(dict.fromkeys(payload.file_ids))
    if not file_ids:
        raise HTTPException(status_code=400, detail="请至少选择一个文件进行预翻译。")
    if not (payload.use_tm or payload.use_glossary or payload.use_term_base or payload.use_llm):
        raise HTTPException(status_code=400, detail="请至少选择一种预翻译操作。")
    if payload.use_tm and not payload.tm_collection_ids:
        raise HTTPException(status_code=400, detail="请选择用于预匹配的记忆库。")
    if payload.use_glossary and not payload.glossary_base_ids:
        raise HTTPException(status_code=400, detail="请选择用于 LLM 的词汇表。")
    if payload.use_term_base and not payload.term_base_ids:
        raise HTTPException(status_code=400, detail="请选择需要绑定的术语库。")

    files = (
        db.query(FileRecord)
        .filter(FileRecord.project_id == project_id, FileRecord.id.in_(file_ids))
        .all()
    )
    files_by_id = {file_record.id: file_record for file_record in files}
    missing_ids = [file_id for file_id in file_ids if file_id not in files_by_id]
    if missing_ids:
        raise HTTPException(status_code=404, detail="选择的文件不存在或不属于当前项目。")
    ordered_files = [files_by_id[file_id] for file_id in file_ids]
    for file_record in ordered_files:
        _require_file_record_work_access(file_record, current_user)

    active_tasks = (
        db.query(PretranslationTask)
        .filter(
            PretranslationTask.file_record_id.in_(file_ids),
            PretranslationTask.status.in_(PRETRANSLATION_ACTIVE_STATUSES),
        )
        .all()
    )
    active_file_ids = {task.file_record_id for task in active_tasks}
    files_to_start = [file_record for file_record in ordered_files if file_record.id not in active_file_ids]
    if not files_to_start and active_tasks:
        active_run = active_tasks[0].run
        return JSONResponse(
            status_code=202,
            content=_serialize_pretranslation_run(active_run, extra_tasks=active_tasks),
        )

    run = PretranslationRun(
        project_id=project_id,
        status="queued",
        progress=0,
        message="预翻译任务已创建",
        total_files=len(files_to_start),
        options_json=json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
        created_by_id=current_user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    created_tasks: list[PretranslationTask] = []
    for file_record in files_to_start:
        _, operation_token = acquire_file_operation_lock(
            db,
            file_record.id,
            operation=PRE_TRANSLATE_OPERATION,
            current_user=current_user,
        )
        task = PretranslationTask(
            run_id=run.id,
            file_record_id=file_record.id,
            status="queued",
            stage="queued",
            progress=0,
            message="等待后台执行",
            provider=payload.llm_provider if payload.use_llm else None,
            model=payload.llm_model if payload.use_llm else None,
            scope=payload.llm_scope if payload.use_llm else None,
            operation_token=operation_token,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        created_tasks.append(task)

    _refresh_pretranslation_run_status(db, run)
    db.commit()
    db.refresh(run)
    if created_tasks:
        background_tasks.add_task(_dispatch_pretranslation_run, run.id)
    return JSONResponse(
        status_code=202,
        content=_serialize_pretranslation_run(run, extra_tasks=active_tasks),
    )


@router.get("/pretranslation-runs/{run_id}")
def get_pretranslation_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(PretranslationRun).filter(PretranslationRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="预翻译任务不存在。")
    project = db.query(Project).filter(Project.id == run.project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在。")
    _require_project_read_access(project, current_user, db)
    _refresh_pretranslation_run_status(db, run)
    db.commit()
    db.refresh(run)
    return _serialize_pretranslation_run(run)


@router.get("/projects/{project_id}/pretranslation-tasks/active")
def list_active_pretranslation_tasks(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_project_or_404(db, project_id)
    _require_project_read_access(project, current_user, db)
    tasks = (
        db.query(PretranslationTask)
        .join(FileRecord, PretranslationTask.file_record_id == FileRecord.id)
        .filter(
            FileRecord.project_id == project_id,
            PretranslationTask.status.in_(PRETRANSLATION_ACTIVE_STATUSES),
        )
        .order_by(PretranslationTask.updated_at.desc(), PretranslationTask.created_at.desc())
        .all()
    )
    visible_tasks: list[PretranslationTask] = []
    for task in tasks:
        try:
            _require_file_record_read_access(task.file_record, current_user)
        except HTTPException:
            continue
        visible_tasks.append(task)
    return {"tasks": [_serialize_pretranslation_task(task) for task in visible_tasks]}


@router.post("/pretranslation-tasks/{task_id}/cancel")
def cancel_pretranslation_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = db.query(PretranslationTask).filter(PretranslationTask.id == task_id).first()
    if task is None or task.file_record is None:
        raise HTTPException(status_code=404, detail="预翻译任务不存在。")
    _require_file_record_work_access(task.file_record, current_user)
    if task.status in PRETRANSLATION_TERMINAL_STATUSES:
        return _serialize_pretranslation_task(task)

    task.cancel_requested = True
    task.message = "正在取消预翻译"
    if task.status == "queued":
        task.status = "canceled"
        task.stage = "canceled"
        task.progress = 100
        task.completed_at = _pretranslation_now()
        db.commit()
        _release_pretranslation_task_lock(task.id)
    else:
        task.status = "canceling"
        task.stage = "canceling"
        db.commit()
    db.refresh(task)
    if task.run:
        _refresh_pretranslation_run_status(db, task.run)
        db.commit()
        db.refresh(task)
    return _serialize_pretranslation_task(task)


class ProjectTranslationMemoryFileSettingPayload(BaseModel):
    file_record_id: UUID
    collection_ids: list[UUID] = Field(default_factory=list)
    primary_collection_id: UUID | None = None
    tm_match_threshold: float | None = None


class ProjectTranslationMemorySettingPayload(BaseModel):
    source_language: str
    target_language: str
    collection_ids: list[UUID] = Field(default_factory=list)
    primary_collection_id: UUID | None = None
    tm_match_threshold: float | None = None
    files: list[ProjectTranslationMemoryFileSettingPayload] = Field(default_factory=list)


class ProjectTranslationMemorySettingsRequest(BaseModel):
    auto_tm_enabled: bool | None = None
    settings: list[ProjectTranslationMemorySettingPayload] = Field(default_factory=list)


class ProjectTermBaseSettingPayload(BaseModel):
    source_language: str
    target_language: str
    enabled_term_base_ids: list[UUID] = Field(default_factory=list)
    writable_term_base_ids: list[UUID] = Field(default_factory=list)
    qa_term_base_ids: list[UUID] = Field(default_factory=list)


class ProjectTermBaseSettingsRequest(BaseModel):
    settings: list[ProjectTermBaseSettingPayload] = Field(default_factory=list)


class ProjectDuplicatePayload(BaseModel):
    name: str
    deadline: str | None = None
    access_level: Literal["team", "private", "public"] | None = None
    translation_guidelines: str | None = None


class TermQAReportCreateRequest(BaseModel):
    file_ids: list[UUID] = Field(default_factory=list)


class TermQAReportItemIgnoreRequest(BaseModel):
    ignored: bool = True


class TermQAReportItemsIgnoreRequest(BaseModel):
    item_ids: list[UUID] = Field(default_factory=list)
    ignored: bool = True


class WorkbenchQAResultItemsIgnoreRequest(BaseModel):
    item_ids: list[str] = Field(default_factory=list)
    ignored: bool = True


class SpellingGrammarQASettingsRequest(BaseModel):
    enabled: bool = True
    severity: Literal["low", "medium", "high"] = "medium"


class QualityQASettingsRequest(BaseModel):
    spelling_grammar: SpellingGrammarQASettingsRequest = Field(
        default_factory=SpellingGrammarQASettingsRequest
    )
    rules: dict[str, Any] = Field(default_factory=dict)


class SegmentQAIssueIgnoreRequest(BaseModel):
    ignored: bool = True


class FileRecordDuplicateRequest(BaseModel):
    filename: str | None = None


class FileRecordAssignmentRequest(BaseModel):
    assignee_id: UUID | None = None


class ProjectAssignmentFileRangeRequest(BaseModel):
    file_record_id: UUID
    range_start: int | None = Field(default=None, ge=1)
    range_end: int | None = Field(default=None, ge=1)


class ProjectAssignmentEntryRequest(BaseModel):
    assignee_id: UUID
    workflow_step_id: UUID | None = None
    file_record_ids: list[UUID] = Field(default_factory=list)
    file_ranges: list[ProjectAssignmentFileRangeRequest] = Field(default_factory=list)
    merge_view_ids: list[UUID] = Field(default_factory=list)


class ProjectAssignmentsRequest(BaseModel):
    assignments: list[ProjectAssignmentEntryRequest] = Field(default_factory=list)


class WorkflowStepRequest(BaseModel):
    id: UUID | None = None
    step_key: str | None = None
    name: str
    step_type: str | None = None


class WorkflowTransitionPreviewRequest(BaseModel):
    from_step_id: UUID
    target_step_id: UUID
    range_start: int = Field(default=1, ge=1)
    range_end: int | None = Field(default=None, ge=1)
    all_segments: bool = True
    source_status: Literal["all", "confirmed", "unconfirmed"] = "all"
    source_statuses: list[Literal["none", "exact", "fuzzy", "confirmed"]] = Field(default_factory=list)
    target_status: Literal["confirmed", "unconfirmed"] = "unconfirmed"


class WorkflowTransitionRequest(WorkflowTransitionPreviewRequest):
    pass


class SaveToTMRequest(BaseModel):
    collection_id: UUID | None = None
    collection_mode: Literal["existing", "new"] = "existing"
    collection_name: str | None = None
    scope: Literal["confirmed", "translated", "all"] = "translated"


class MemoryBasePayload(BaseModel):
    name: str
    description: str | None = None
    source_language: str
    target_language: str


class TMCollectionMergePayload(BaseModel):
    source_collection_ids: list[UUID]
    name: str
    description: str | None = None


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


class IssueMarkerCreateRequest(BaseModel):
    file_record_id: UUID | None = None
    title: str | None = None
    description: str
    category: Literal["bug", "translation", "format", "performance", "data", "other"] = "other"
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    page_url: str | None = None
    user_agent: str | None = None


class IssueMarkerUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    category: Literal["bug", "translation", "format", "performance", "data", "other"] | None = None
    severity: Literal["low", "medium", "high", "critical"] | None = None
    status: Literal["open", "resolved"] | None = None


class ProjectCreatePayload(BaseModel):
    name: str
    source_language: str | None = None
    target_language: str | None = None
    deadline: str | None = None
    access_level: Literal["team", "private", "public"] = "team"
    collection_id: UUID | None = None
    term_base_id: UUID | None = None
    workflow_template_id: str | None = None
    workflow_steps: list[WorkflowStepRequest] = Field(default_factory=list)


class ProjectUpdatePayload(BaseModel):
    name: str | None = None
    source_language: str | None = None
    target_language: str | None = None
    deadline: str | None = None
    access_level: Literal["team", "private", "public"] | None = None
    translation_guidelines: str | None = None


class ProjectDocumentStatisticsPayload(BaseModel):
    file_ids: list[UUID] = Field(default_factory=list)


class ProjectFileZipExportPayload(BaseModel):
    file_ids: list[UUID] = Field(default_factory=list)


def _build_unavailable_document_statistics() -> dict[str, Any]:
    statistics = {
        "source": "unavailable",
        "engine": None,
        "engine_version": None,
        "license_status": None,
        "include_textboxes_footnotes_endnotes": None,
        "match_analysis": None,
    }
    for key in STATISTIC_NUMBER_KEYS:
        statistics[key] = None
    return statistics


def _create_empty_document_statistics_totals() -> dict[str, Any]:
    totals: dict[str, Any] = {key: None for key in STATISTIC_NUMBER_KEYS}
    totals["match_analysis"] = None
    return totals


def _has_any_document_statistic(statistics: dict[str, Any] | None) -> bool:
    if not statistics:
        return False
    return any(isinstance(statistics.get(key), int) for key in STATISTIC_NUMBER_KEYS)


def _sum_document_statistics(statistics_list: list[dict[str, Any]]) -> dict[str, Any]:
    totals = _create_empty_document_statistics_totals()
    match_analyses: list[Any] = []
    for raw_statistics in statistics_list:
        statistics = normalize_document_statistics(raw_statistics)
        match_analyses.append(statistics.get("match_analysis"))
        for key in STATISTIC_NUMBER_KEYS:
            value = statistics.get(key)
            if not isinstance(value, int):
                continue
            totals[key] = (totals[key] or 0) + value
    totals["match_analysis"] = merge_document_match_analyses(match_analyses)
    return totals


def _load_document_statistics_totals(raw_value: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw_value or "{}")
    except (TypeError, ValueError):
        value = {}
    totals = _create_empty_document_statistics_totals()
    if isinstance(value, dict):
        for key in STATISTIC_NUMBER_KEYS:
            raw_number = value.get(key)
            if isinstance(raw_number, int):
                totals[key] = raw_number
            elif isinstance(raw_number, str) and raw_number.strip().isdigit():
                totals[key] = int(raw_number)
        totals["match_analysis"] = normalize_document_match_analysis(value.get("match_analysis"))
    return totals


def _serialize_document_statistics_report_item(
    item: DocumentStatisticsReportItem,
) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "report_id": str(item.report_id),
        "project_id": str(item.project_id),
        "file_record_id": str(item.file_record_id) if item.file_record_id else None,
        "file_name": item.file_name,
        "source_language": item.source_language,
        "target_language": item.target_language,
        "file_size_bytes": item.file_size_bytes,
        "statistics": normalize_document_statistics(item.statistics),
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _serialize_document_statistics_report(
    report: DocumentStatisticsReport,
    items: list[DocumentStatisticsReportItem] | None = None,
) -> dict[str, Any]:
    report_items = list(items if items is not None else report.items)
    return {
        "id": str(report.id),
        "project_id": str(report.project_id),
        "created_by_id": str(report.created_by_id) if report.created_by_id else None,
        "created_by_name": get_user_display_name(report.created_by) if report.created_by else None,
        "file_ids": [str(value) for value in _load_json_list(report.file_ids)],
        "total_files": report.total_files,
        "available_files": report.available_files,
        "totals": _load_document_statistics_totals(report.totals),
        "status": report.status,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "items": [_serialize_document_statistics_report_item(item) for item in report_items],
    }


def _load_document_statistics_report_items_for_response(
    db: Session,
    report_ids: list[UUID],
) -> dict[UUID, list[DocumentStatisticsReportItem]]:
    items_by_report_id: dict[UUID, list[DocumentStatisticsReportItem]] = {
        report_id: [] for report_id in report_ids
    }
    if not report_ids:
        return items_by_report_id
    items = (
        db.query(DocumentStatisticsReportItem)
        .filter(DocumentStatisticsReportItem.report_id.in_(report_ids))
        .order_by(
            DocumentStatisticsReportItem.file_name.asc(),
            DocumentStatisticsReportItem.created_at.asc(),
            DocumentStatisticsReportItem.id.asc(),
        )
        .all()
    )
    for item in items:
        items_by_report_id.setdefault(item.report_id, []).append(item)
    return items_by_report_id


def _load_document_repetition_statistics_for_files(
    db: Session,
    file_ids: list[UUID],
) -> dict[UUID, dict[str, int]]:
    file_segments: dict[UUID, list[str]] = {file_id: [] for file_id in file_ids}
    if not file_segments:
        return {}

    segments = (
        db.query(Segment.file_record_id, Segment.source_text)
        .filter(Segment.file_record_id.in_(file_ids))
        .order_by(
            Segment.file_record_id.asc(),
            *SEGMENT_ORDERING,
            Segment.id.asc(),
        )
        .all()
    )
    for file_record_id, source_text in segments:
        file_segments.setdefault(file_record_id, []).append(source_text or "")

    return compute_document_repetition_statistics(file_segments)


def _load_document_match_analysis_for_files(
    db: Session,
    files: list[FileRecord],
) -> dict[UUID, dict[str, Any]]:
    file_segments: dict[UUID, list[DocumentMatchSegment]] = {
        file_record.id: [] for file_record in files
    }
    if not file_segments:
        return {}

    collection_ids_by_file_id = {
        file_record.id: tuple(_load_file_record_collection_ids(file_record))
        for file_record in files
    }

    segments = (
        db.query(
            Segment.file_record_id,
            Segment.source_text,
            Segment.display_text,
            Segment.source_word_count,
        )
        .filter(Segment.file_record_id.in_(list(file_segments.keys())))
        .order_by(
            Segment.file_record_id.asc(),
            *SEGMENT_ORDERING,
            Segment.id.asc(),
        )
        .all()
    )
    for file_record_id, source_text, display_text, source_word_count in segments:
        file_segments.setdefault(file_record_id, []).append(
            DocumentMatchSegment(
                file_id=file_record_id,
                source_text=source_text or "",
                display_text=display_text or "",
                source_word_count=int(source_word_count or 0),
                collection_ids=collection_ids_by_file_id.get(file_record_id, ()),
            )
        )

    return compute_document_match_analysis(db, file_segments)


def _can_manage_workflow(current_user: User | None) -> bool:
    return is_admin_role(getattr(current_user, "role", None))


WORKFLOW_TEMPLATE_DEFINITIONS: list[dict[str, Any]] = [
    {
        "id": "translate",
        "name": "翻译",
        "steps": [
            {"step_key": "translate", "name": "翻译", "step_type": "translation"},
        ],
    },
    {
        "id": "translate_review",
        "name": "翻译-审校",
        "steps": [
            {"step_key": "translate", "name": "翻译", "step_type": "translation"},
            {"step_key": "review", "name": "审校", "step_type": "review"},
        ],
    },
    {
        "id": "translate_review_proofread",
        "name": "翻译-审校-校对",
        "steps": [
            {"step_key": "translate", "name": "翻译", "step_type": "translation"},
            {"step_key": "review", "name": "审校", "step_type": "review"},
            {"step_key": "proofread", "name": "校对", "step_type": "proofread"},
        ],
    },
]
WORKFLOW_TEMPLATE_BY_ID = {
    str(template["id"]): template
    for template in WORKFLOW_TEMPLATE_DEFINITIONS
}


def _serialize_workflow_template(template: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": template["id"],
        "name": template["name"],
        "steps": [
            {
                "step_key": step["step_key"],
                "name": step["name"],
                "step_type": step["step_type"],
                "sort_order": index,
            }
            for index, step in enumerate(template["steps"])
        ],
    }


def _serialize_workflow_step(step: ProjectWorkflowStep) -> dict[str, Any]:
    return {
        "id": str(step.id),
        "step_key": step.step_key,
        "name": step.name,
        "step_type": step.step_type,
        "sort_order": int(step.sort_order or 0),
    }


def _load_project_workflow_steps(db: Session, project_id: UUID | None) -> list[ProjectWorkflowStep]:
    if project_id is None:
        return []
    return (
        db.query(ProjectWorkflowStep)
        .filter(ProjectWorkflowStep.project_id == project_id)
        .order_by(ProjectWorkflowStep.sort_order.asc(), ProjectWorkflowStep.id.asc())
        .all()
    )


def _load_workflow_steps_by_project(
    db: Session,
    project_ids: list[UUID],
) -> dict[UUID, list[ProjectWorkflowStep]]:
    if not project_ids:
        return {}
    rows = (
        db.query(ProjectWorkflowStep)
        .filter(ProjectWorkflowStep.project_id.in_(project_ids))
        .order_by(ProjectWorkflowStep.project_id.asc(), ProjectWorkflowStep.sort_order.asc(), ProjectWorkflowStep.id.asc())
        .all()
    )
    result: dict[UUID, list[ProjectWorkflowStep]] = {}
    for step in rows:
        result.setdefault(step.project_id, []).append(step)
    return result


def _create_project_workflow_steps(
    db: Session,
    project: Project,
    requested_steps: list[WorkflowStepRequest],
    template_id: str | None,
) -> list[ProjectWorkflowStep]:
    template = WORKFLOW_TEMPLATE_BY_ID.get((template_id or "").strip())
    if template is None:
        raise HTTPException(status_code=400, detail="请选择有效的工作流模板。")

    source_steps = requested_steps or [
        WorkflowStepRequest(
            step_key=str(step["step_key"]),
            name=str(step["name"]),
            step_type=str(step["step_type"]),
        )
        for step in template["steps"]
    ]
    if not source_steps:
        raise HTTPException(status_code=400, detail="工作流至少需要一个阶段。")
    if len(source_steps) > 8:
        raise HTTPException(status_code=400, detail="工作流阶段最多支持 8 个。")

    normalized: list[ProjectWorkflowStep] = []
    used_keys: set[str] = set()
    for index, item in enumerate(source_steps):
        name = (item.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="工作流阶段名称不能为空。")
        if index == 0 and name != "翻译":
            raise HTTPException(status_code=400, detail="工作流第一个阶段必须是“翻译”。")

        raw_key = (item.step_key or "").strip().lower()
        step_key = raw_key or ("translate" if index == 0 else f"step_{index + 1}")
        step_key = re.sub(r"[^a-z0-9_]+", "_", step_key).strip("_") or f"step_{index + 1}"
        if index == 0:
            step_key = "translate"
        base_key = step_key
        suffix = 2
        while step_key in used_keys:
            step_key = f"{base_key}_{suffix}"
            suffix += 1
        used_keys.add(step_key)

        step_type = (item.step_type or ("translation" if index == 0 else "custom")).strip().lower()
        if index == 0:
            step_type = "translation"

        normalized.append(
            ProjectWorkflowStep(
                project_id=project.id,
                step_key=step_key,
                name=name,
                step_type=step_type[:20] or "custom",
                sort_order=index,
            )
        )

    for step in normalized:
        db.add(step)
    db.flush()
    return normalized


def _copy_project_workflow_steps(
    db: Session,
    source_project_id: UUID,
    target_project: Project,
) -> list[ProjectWorkflowStep]:
    source_steps = _load_project_workflow_steps(db, source_project_id)
    if not source_steps:
        source_steps = [
            ProjectWorkflowStep(
                project_id=source_project_id,
                step_key="translate",
                name="翻译",
                step_type="translation",
                sort_order=0,
            )
        ]
    copied_steps = [
        ProjectWorkflowStep(
            project_id=target_project.id,
            step_key=step.step_key,
            name=step.name,
            step_type=step.step_type,
            sort_order=int(step.sort_order or 0),
        )
        for step in source_steps
    ]
    for step in copied_steps:
        db.add(step)
    db.flush()
    return copied_steps


def _get_first_workflow_step_id(db: Session, project_id: UUID | None) -> UUID | None:
    steps = _load_project_workflow_steps(db, project_id)
    return steps[0].id if steps else None


def _assign_file_segments_to_first_workflow_step(db: Session, file_record: FileRecord) -> UUID | None:
    first_step_id = _get_first_workflow_step_id(db, file_record.project_id)
    if first_step_id is None:
        return None
    db.query(Segment).filter(
        Segment.file_record_id == file_record.id,
        Segment.workflow_step_id.is_(None),
    ).update(
        {"workflow_step_id": first_step_id},
        synchronize_session=False,
    )
    return first_step_id


def _build_workflow_progress_payload(
    steps: list[ProjectWorkflowStep],
    total_segments: int,
    grouped_counts: list[tuple[UUID | None, str | None, int]],
) -> list[dict[str, Any]]:
    if not steps:
        return []

    order_by_step_id = {step.id: int(step.sort_order or 0) for step in steps}
    first_order = int(steps[0].sort_order or 0)
    payload: list[dict[str, Any]] = []
    for step in steps:
        step_order = int(step.sort_order or 0)
        completed = 0
        for current_step_id, status, count in grouped_counts:
            current_order = order_by_step_id.get(current_step_id, first_order)
            if current_order > step_order or (current_order == step_order and status == "confirmed"):
                completed += int(count or 0)
        payload.append({
            **_serialize_workflow_step(step),
            "total_segments": int(total_segments or 0),
            "completed_segments": completed,
            "progress": calculate_file_record_progress(int(total_segments or 0), completed),
        })
    return payload


def _clamp_progress_value(value: Any) -> int:
    try:
        progress = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, progress))


def _calculate_workflow_overall_progress(
    workflow_progress: list[dict[str, Any]] | None,
    fallback_progress: int,
) -> int:
    fallback = _clamp_progress_value(fallback_progress)
    if not workflow_progress:
        return fallback

    completed_units = 0
    total_units = 0
    progress_values: list[int] = []
    for item in workflow_progress:
        if not isinstance(item, dict):
            continue
        progress_values.append(_clamp_progress_value(item.get("progress", 0)))
        try:
            total_segments = int(item.get("total_segments") or 0)
            completed_segments = int(item.get("completed_segments") or 0)
        except (TypeError, ValueError):
            continue
        if total_segments <= 0:
            continue
        total_units += total_segments
        completed_units += max(0, min(completed_segments, total_segments))

    if total_units > 0:
        return calculate_file_record_progress(total_units, completed_units)
    if progress_values:
        return _clamp_progress_value(sum(progress_values) / len(progress_values))
    return fallback


def _get_file_workflow_progress(db: Session, file_record_ids: list[UUID]) -> dict[UUID, list[dict[str, Any]]]:
    if not file_record_ids:
        return {}
    file_rows = (
        db.query(FileRecord.id, FileRecord.project_id)
        .filter(FileRecord.id.in_(file_record_ids))
        .all()
    )
    project_ids = sorted({row.project_id for row in file_rows if row.project_id}, key=str)
    steps_by_project = _load_workflow_steps_by_project(db, project_ids)
    segment_rows = (
        db.query(
            Segment.file_record_id,
            Segment.workflow_step_id,
            Segment.status,
            func.count(Segment.id).label("count"),
        )
        .filter(Segment.file_record_id.in_(file_record_ids))
        .group_by(Segment.file_record_id, Segment.workflow_step_id, Segment.status)
        .all()
    )
    grouped_by_file: dict[UUID, list[tuple[UUID | None, str | None, int]]] = {}
    total_by_file: dict[UUID, int] = {}
    for row in segment_rows:
        count = int(row.count or 0)
        grouped_by_file.setdefault(row.file_record_id, []).append((row.workflow_step_id, row.status, count))
        total_by_file[row.file_record_id] = total_by_file.get(row.file_record_id, 0) + count

    result: dict[UUID, list[dict[str, Any]]] = {}
    for row in file_rows:
        steps = steps_by_project.get(row.project_id, []) if row.project_id else []
        result[row.id] = _build_workflow_progress_payload(
            steps,
            total_by_file.get(row.id, 0),
            grouped_by_file.get(row.id, []),
        )
    return result


def _get_project_workflow_progress(db: Session, project_ids: list[UUID]) -> dict[UUID, list[dict[str, Any]]]:
    if not project_ids:
        return {}
    steps_by_project = _load_workflow_steps_by_project(db, project_ids)
    segment_rows = (
        db.query(
            FileRecord.project_id,
            Segment.workflow_step_id,
            Segment.status,
            func.count(Segment.id).label("count"),
        )
        .join(FileRecord, FileRecord.id == Segment.file_record_id)
        .filter(FileRecord.project_id.in_(project_ids))
        .group_by(FileRecord.project_id, Segment.workflow_step_id, Segment.status)
        .all()
    )
    grouped_by_project: dict[UUID, list[tuple[UUID | None, str | None, int]]] = {}
    total_by_project: dict[UUID, int] = {}
    for row in segment_rows:
        count = int(row.count or 0)
        grouped_by_project.setdefault(row.project_id, []).append((row.workflow_step_id, row.status, count))
        total_by_project[row.project_id] = total_by_project.get(row.project_id, 0) + count

    return {
        project_id: _build_workflow_progress_payload(
            steps_by_project.get(project_id, []),
            total_by_project.get(project_id, 0),
            grouped_by_project.get(project_id, []),
        )
        for project_id in project_ids
    }


ASSIGNMENT_STATUS_ACTIVE = "active"
ASSIGNMENT_STATUS_REVOKED = "revoked"
ASSIGNMENT_EVENT_PROJECT_ASSIGNED = "project_assigned"
ASSIGNMENT_EVENT_PROJECT_UNASSIGNED = "project_unassigned"
ASSIGNMENT_EVENT_FILE_GRANTED = "file_permission_granted"
ASSIGNMENT_EVENT_FILE_REVOKED = "file_permission_revoked"


AssignmentTarget = tuple[UUID, int | None, int | None]


@dataclass(frozen=True)
class SegmentWritableAssignment:
    workflow_step_id: UUID
    range_start: int | None = None
    range_end: int | None = None


def _get_record_session(record: Any) -> Session | None:
    try:
        return object_session(record)
    except Exception:
        return None


def _has_active_project_assignment(db: Session | None, project_id: UUID | None, user_id: UUID | None) -> bool:
    if db is None or project_id is None or user_id is None:
        return False
    return (
        db.query(ProjectAssignment.id)
        .filter(
            ProjectAssignment.project_id == project_id,
            ProjectAssignment.assignee_id == user_id,
            ProjectAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
        )
        .first()
        is not None
    )


def _has_active_file_assignment(db: Session | None, file_record: FileRecord, user_id: UUID | None) -> bool:
    if db is None or user_id is None:
        return False
    if not _has_active_project_assignment(db, file_record.project_id, user_id):
        return False
    return (
        db.query(FileAssignment.id)
        .filter(
            FileAssignment.file_record_id == file_record.id,
            FileAssignment.assignee_id == user_id,
            FileAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
        )
        .first()
        is not None
    )


def _get_active_file_assignment_step_ids(
    db: Session | None,
    file_record: FileRecord,
    user_id: UUID | None,
) -> set[UUID]:
    if db is None or user_id is None:
        return set()
    if not _has_active_project_assignment(db, file_record.project_id, user_id):
        return set()
    rows = (
        db.query(FileAssignment.workflow_step_id)
        .filter(
            FileAssignment.file_record_id == file_record.id,
            FileAssignment.assignee_id == user_id,
            FileAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
            FileAssignment.workflow_step_id.isnot(None),
        )
        .all()
    )
    return {row.workflow_step_id for row in rows if row.workflow_step_id is not None}


def _get_user_writable_assignments(
    db: Session | None,
    file_record: FileRecord,
    user_id: UUID | None,
) -> list[SegmentWritableAssignment]:
    if db is None or user_id is None:
        return []
    if not _has_active_project_assignment(db, file_record.project_id, user_id):
        return []
    rows = (
        db.query(
            FileAssignment.workflow_step_id,
            FileAssignment.segment_range_start,
            FileAssignment.segment_range_end,
        )
        .filter(
            FileAssignment.file_record_id == file_record.id,
            FileAssignment.assignee_id == user_id,
            FileAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
        )
        .all()
    )
    first_step_id = _get_first_workflow_step_id(db, file_record.project_id)
    assignments: list[SegmentWritableAssignment] = []
    for row in rows:
        workflow_step_id = row.workflow_step_id or first_step_id
        if workflow_step_id is None:
            continue
        assignments.append(
            SegmentWritableAssignment(
                workflow_step_id=workflow_step_id,
                range_start=row.segment_range_start,
                range_end=row.segment_range_end,
            )
        )
    return assignments


def _display_index_in_assignment_range(
    zero_based_display_index: int | None,
    range_start: int | None,
    range_end: int | None,
) -> bool:
    if range_start is None and range_end is None:
        return True
    if zero_based_display_index is None or range_start is None or range_end is None:
        return False
    one_based_display_index = zero_based_display_index + 1
    return range_start <= one_based_display_index <= range_end


def _can_write_segment_with_assignments(
    workflow_step_id: UUID | None,
    zero_based_display_index: int | None,
    writable_assignments: Iterable[SegmentWritableAssignment],
) -> bool:
    if workflow_step_id is None:
        return False
    return any(
        assignment.workflow_step_id == workflow_step_id
        and _display_index_in_assignment_range(
            zero_based_display_index,
            assignment.range_start,
            assignment.range_end,
        )
        for assignment in writable_assignments
    )


def _apply_segment_assignment_visibility_filter(
    db: Session,
    query,
    file_record: FileRecord,
    current_user: User,
):
    if can_access_all_projects(current_user):
        return query
    visible_assignments = _get_user_writable_assignments(db, file_record, current_user.id)
    if not visible_assignments:
        return query.filter(literal(False))

    ordered_segments = (
        db.query(
            Segment.id.label("id"),
            Segment.workflow_step_id.label("workflow_step_id"),
            func.row_number()
            .over(
                order_by=get_segment_ordering_for_file_record(file_record)
            )
            .label("display_index"),
        )
        .filter(Segment.file_record_id == file_record.id)
        .subquery()
    )

    visibility_conditions = []
    for assignment in visible_assignments:
        condition = ordered_segments.c.workflow_step_id == assignment.workflow_step_id
        if assignment.range_start is not None and assignment.range_end is not None:
            condition = and_(
                condition,
                ordered_segments.c.display_index >= assignment.range_start,
                ordered_segments.c.display_index <= assignment.range_end,
            )
        elif assignment.range_start is not None or assignment.range_end is not None:
            continue
        visibility_conditions.append(condition)

    if not visibility_conditions:
        return query.filter(literal(False))
    visible_segment_ids = db.query(ordered_segments.c.id).filter(or_(*visibility_conditions))
    return query.filter(Segment.id.in_(visible_segment_ids))


def _has_active_file_assignment_for_step(
    db: Session | None,
    file_record: FileRecord,
    user_id: UUID | None,
    workflow_step_id: UUID | None,
) -> bool:
    if db is None or user_id is None or workflow_step_id is None:
        return False
    if not _has_active_project_assignment(db, file_record.project_id, user_id):
        return False
    return (
        db.query(FileAssignment.id)
        .filter(
            FileAssignment.file_record_id == file_record.id,
            FileAssignment.assignee_id == user_id,
            FileAssignment.workflow_step_id == workflow_step_id,
            FileAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
        )
        .first()
        is not None
    )


def _ensure_segment_workflow_step(db: Session, file_record: FileRecord, segment: Segment) -> UUID | None:
    if segment.workflow_step_id is not None:
        return segment.workflow_step_id
    first_step_id = _get_first_workflow_step_id(db, file_record.project_id)
    if first_step_id is not None:
        segment.workflow_step_id = first_step_id
        db.flush()
    return segment.workflow_step_id


def _get_file_record_writable_workflow_step_ids(
    db: Session,
    file_record: FileRecord,
    current_user: User | None,
) -> set[UUID]:
    if current_user is None:
        return set()
    if can_access_all_projects(current_user):
        return {step.id for step in _load_project_workflow_steps(db, file_record.project_id)}
    return _get_active_file_assignment_step_ids(db, file_record, current_user.id)


def _can_write_workflow_step(
    db: Session,
    file_record: FileRecord,
    current_user: User | None,
    workflow_step_id: UUID | None,
) -> bool:
    if current_user is None:
        return False
    if can_access_all_projects(current_user):
        return True
    return _has_active_file_assignment_for_step(db, file_record, current_user.id, workflow_step_id)


def _require_segment_work_access(
    db: Session,
    file_record: FileRecord,
    segment: Segment,
    current_user: User,
) -> None:
    workflow_step_id = _ensure_segment_workflow_step(db, file_record, segment)
    if can_access_all_projects(current_user):
        return
    display_index_map = _get_segment_display_index_map(db, file_record.id, [segment])
    writable_assignments = _get_user_writable_assignments(db, file_record, current_user.id)
    if not _can_write_segment_with_assignments(
        workflow_step_id,
        display_index_map.get(segment.id),
        writable_assignments,
    ):
        raise HTTPException(status_code=403, detail="当前账号没有编辑该流程阶段句段的权限。")


def _filter_writable_segments(
    db: Session,
    file_record: FileRecord,
    current_user: User,
    segments: list[Segment],
) -> list[Segment]:
    if can_access_all_projects(current_user):
        for segment in segments:
            _ensure_segment_workflow_step(db, file_record, segment)
        return segments
    writable_assignments = _get_user_writable_assignments(db, file_record, current_user.id)
    display_index_map = _get_segment_display_index_map(db, file_record.id, segments)
    result: list[Segment] = []
    for segment in segments:
        workflow_step_id = _ensure_segment_workflow_step(db, file_record, segment)
        if _can_write_segment_with_assignments(
            workflow_step_id,
            display_index_map.get(segment.id),
            writable_assignments,
        ):
            result.append(segment)
    return result


def _can_read_project(project: Project, current_user: User | None, db: Session | None = None) -> bool:
    if current_user is None:
        return False
    if can_access_all_projects(current_user):
        return True
    session = db or _get_record_session(project)
    return _has_active_project_assignment(session, project.id, current_user.id)


def _require_project_read_access(project: Project, current_user: User, db: Session | None = None) -> None:
    if not _can_read_project(project, current_user, db):
        raise HTTPException(status_code=404, detail="项目不存在或未指派给当前用户。")


def _can_read_file_record(file_record: FileRecord, current_user: User | None, db: Session | None = None) -> bool:
    if current_user is None:
        return False
    if not hasattr(current_user, "id"):
        return True
    if can_access_all_projects(current_user):
        return True
    session = db or _get_record_session(file_record)
    if session is not None:
        return _has_active_file_assignment(session, file_record, current_user.id)
    assignee_id = getattr(file_record, "assignee_id", None)
    return assignee_id is None or assignee_id == current_user.id


def _can_write_file_record(file_record: FileRecord, current_user: User | None, db: Session | None = None) -> bool:
    if current_user is None:
        return False
    if can_access_all_projects(current_user):
        return True
    session = db or _get_record_session(file_record)
    if session is not None:
        return _has_active_file_assignment(session, file_record, current_user.id)
    assignee_id = getattr(file_record, "assignee_id", None)
    return assignee_id is None or assignee_id == current_user.id


def _require_file_record_read_access(file_record: FileRecord, current_user: User) -> None:
    if not _can_read_file_record(file_record, current_user):
        raise HTTPException(status_code=404, detail="任务不存在或未分配给当前用户。")


def _require_file_record_work_access(file_record: FileRecord, current_user: User) -> None:
    if not _can_write_file_record(file_record, current_user):
        raise HTTPException(status_code=403, detail="当前账号没有处理该任务的权限。")


def _apply_project_visibility_filter(query, db: Session, current_user: User):
    if can_access_all_projects(current_user):
        return query
    assigned_project_ids = (
        db.query(ProjectAssignment.project_id)
        .filter(
            ProjectAssignment.assignee_id == current_user.id,
            ProjectAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
        )
        .distinct()
    )
    return query.filter(Project.id.in_(assigned_project_ids))


def _visible_project_files(files: list[FileRecord], current_user: User, db: Session | None = None) -> list[FileRecord]:
    if can_access_all_projects(current_user):
        return files
    if not files:
        return []
    session = db or _get_record_session(files[0])
    if session is None:
        return [file_record for file_record in files if file_record.assignee_id == current_user.id]
    file_ids = [file_record.id for file_record in files]
    authorized_ids = {
        row.file_record_id
        for row in (
            session.query(FileAssignment.file_record_id)
            .filter(
                FileAssignment.assignee_id == current_user.id,
                FileAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
                FileAssignment.file_record_id.in_(file_ids),
            )
            .all()
        )
    }
    return [file_record for file_record in files if file_record.id in authorized_ids]


def _serialize_assignee(user: User | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return serialize_user(user)


def _get_active_project_assignees(db: Session, project_ids: list[UUID]) -> dict[UUID, list[User]]:
    if not project_ids:
        return {}
    rows = (
        db.query(ProjectAssignment.project_id, User)
        .join(User, User.id == ProjectAssignment.assignee_id)
        .filter(
            ProjectAssignment.project_id.in_(project_ids),
            ProjectAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
        )
        .order_by(ProjectAssignment.assigned_at.asc(), User.username.asc())
        .all()
    )
    result: dict[UUID, list[User]] = {}
    for project_id, user in rows:
        result.setdefault(project_id, []).append(user)
    return result


def _get_active_file_assignees(db: Session, file_record_ids: list[UUID]) -> dict[UUID, list[User]]:
    if not file_record_ids:
        return {}
    rows = (
        db.query(FileAssignment.file_record_id, User)
        .join(User, User.id == FileAssignment.assignee_id)
        .filter(
            FileAssignment.file_record_id.in_(file_record_ids),
            FileAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
        )
        .order_by(FileAssignment.assigned_at.asc(), User.username.asc())
        .all()
    )
    result: dict[UUID, list[User]] = {}
    seen: set[tuple[UUID, UUID]] = set()
    for file_record_id, user in rows:
        key = (file_record_id, user.id)
        if key in seen:
            continue
        seen.add(key)
        result.setdefault(file_record_id, []).append(user)
    return result


def _serialize_user_list(users: list[User] | None) -> list[dict[str, Any]]:
    return [serialize_user(user) for user in (users or [])]


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _serialize_assignment_event(event: AssignmentEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "project_id": str(event.project_id),
        "project_name": event.project.name if event.project else None,
        "file_record_id": str(event.file_record_id) if event.file_record_id else None,
        "file_record_name": event.file_record.filename if event.file_record else None,
        "assignee_id": str(event.assignee_id),
        "assignee": _serialize_assignee(event.assignee),
        "actor_id": str(event.actor_id) if event.actor_id else None,
        "actor": _serialize_assignee(event.actor),
        "action": event.action,
        "before_payload": json.loads(event.before_payload or "{}"),
        "after_payload": json.loads(event.after_payload or "{}"),
        "created_at": event.created_at.isoformat(),
    }


def _serialize_notification(notification: Notification) -> dict[str, Any]:
    return {
        "id": str(notification.id),
        "type": notification.type,
        "title": notification.title,
        "body": notification.body,
        "project_id": str(notification.project_id) if notification.project_id else None,
        "project_name": notification.project.name if notification.project else None,
        "file_record_id": str(notification.file_record_id) if notification.file_record_id else None,
        "file_record_name": notification.file_record.filename if notification.file_record else None,
        "related_event_id": str(notification.related_event_id) if notification.related_event_id else None,
        "read_at": notification.read_at.isoformat() if notification.read_at else None,
        "created_at": notification.created_at.isoformat(),
    }


def _record_assignment_event(
    db: Session,
    *,
    project_id: UUID,
    assignee_id: UUID,
    actor_id: UUID | None,
    action: str,
    file_record_id: UUID | None = None,
    before_payload: dict[str, Any] | None = None,
    after_payload: dict[str, Any] | None = None,
) -> AssignmentEvent:
    event = AssignmentEvent(
        project_id=project_id,
        file_record_id=file_record_id,
        assignee_id=assignee_id,
        actor_id=actor_id,
        action=action,
        before_payload=_json_dumps(before_payload or {}),
        after_payload=_json_dumps(after_payload or {}),
    )
    db.add(event)
    db.flush()
    return event


def _create_assignment_notification(
    db: Session,
    *,
    user_id: UUID,
    notification_type: str,
    title: str,
    body: str,
    project_id: UUID | None = None,
    file_record_id: UUID | None = None,
    related_event_id: UUID | None = None,
) -> Notification:
    notification = Notification(
        user_id=user_id,
        type=notification_type,
        title=title,
        body=body,
        project_id=project_id,
        file_record_id=file_record_id,
        related_event_id=related_event_id,
    )
    db.add(notification)
    db.flush()
    return notification


def _get_project_or_404(db: Session, project_id: UUID) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在。")
    return project


def _assignment_target_sort_key(target: AssignmentTarget) -> tuple[str, int, int]:
    file_record_id, range_start, range_end = target
    return (str(file_record_id), range_start or 0, range_end or 0)


def _serialize_assignment_target_payload(target: AssignmentTarget) -> dict[str, Any]:
    file_record_id, range_start, range_end = target
    return {
        "file_record_id": str(file_record_id),
        "range_start": range_start,
        "range_end": range_end,
    }


def _assignment_ranges_overlap(
    left_start: int | None,
    left_end: int | None,
    right_start: int | None,
    right_end: int | None,
) -> bool:
    if left_start is None and left_end is None:
        return True
    if right_start is None and right_end is None:
        return True
    if left_start is None or left_end is None or right_start is None or right_end is None:
        return True
    return max(left_start, right_start) <= min(left_end, right_end)


def _segment_merge_block_key(segment: Any) -> tuple[int, int | None, int | None]:
    return (
        int(getattr(segment, "block_index", 0) or 0),
        getattr(segment, "row_index", None),
        getattr(segment, "cell_index", None),
    )


def _segments_in_same_merge_block(left: Any, right: Any) -> bool:
    return _segment_merge_block_key(left) == _segment_merge_block_key(right)


def _validate_assignment_range_merge_block_boundary(
    db: Session,
    *,
    file_record_id: UUID,
    file_label: str,
    range_start: int,
    range_end: int,
) -> None:
    file_record = get_file_record_model(db, file_record_id)
    boundary_positions = {range_start, range_end}
    if range_start > 1:
        boundary_positions.add(range_start - 1)
    boundary_positions.add(range_end + 1)

    ordered_segments = (
        db.query(
            Segment.id.label("id"),
            Segment.block_index.label("block_index"),
            Segment.row_index.label("row_index"),
            Segment.cell_index.label("cell_index"),
            func.row_number()
            .over(
                order_by=get_segment_ordering_for_file_record(file_record)
            )
            .label("display_index"),
        )
        .filter(Segment.file_record_id == file_record_id)
        .subquery()
    )
    rows = (
        db.query(
            ordered_segments.c.display_index,
            ordered_segments.c.block_index,
            ordered_segments.c.row_index,
            ordered_segments.c.cell_index,
        )
        .filter(ordered_segments.c.display_index.in_(boundary_positions))
        .all()
    )
    row_by_position = {int(row.display_index): row for row in rows}
    start_segment = row_by_position.get(range_start)
    end_segment = row_by_position.get(range_end)
    if start_segment is None or end_segment is None:
        raise HTTPException(status_code=400, detail=f"{file_label} 的句段范围不存在。")

    previous_segment = row_by_position.get(range_start - 1)
    if previous_segment is not None and _segments_in_same_merge_block(previous_segment, start_segment):
        raise HTTPException(
            status_code=400,
            detail=f"{file_label} 的句段范围不能从同一段落中间开始，请将起始段调整到段落边界。",
        )

    next_segment = row_by_position.get(range_end + 1)
    if next_segment is not None and _segments_in_same_merge_block(end_segment, next_segment):
        raise HTTPException(
            status_code=400,
            detail=f"{file_label} 的句段范围不能在同一段落中间结束，请将结束段调整到段落边界。",
        )


def _validate_assignment_payload(
    db: Session,
    project: Project,
    payload: ProjectAssignmentsRequest,
) -> tuple[dict[tuple[UUID, UUID], set[AssignmentTarget]], set[UUID]]:
    project_file_rows = (
        db.query(FileRecord.id, FileRecord.filename)
        .filter(FileRecord.project_id == project.id)
        .all()
    )
    project_file_ids = {row.id for row in project_file_rows}
    project_file_labels = {row.id: row.filename or str(row.id) for row in project_file_rows}
    workflow_steps = _load_project_workflow_steps(db, project.id)
    if not workflow_steps:
        workflow_steps = _create_project_workflow_steps(
            db,
            project,
            requested_steps=[],
            template_id="translate",
        )
    workflow_step_ids = {step.id for step in workflow_steps}
    first_step_id = workflow_steps[0].id

    ranged_file_ids = {
        item.file_record_id
        for entry in payload.assignments
        for item in (entry.file_ranges or [])
        if item.range_start is not None or item.range_end is not None
    }
    segment_counts_by_file_id: dict[UUID, int] = {}
    if ranged_file_ids:
        segment_count_rows = (
            db.query(Segment.file_record_id, func.count(Segment.id).label("segment_count"))
            .filter(Segment.file_record_id.in_(ranged_file_ids))
            .group_by(Segment.file_record_id)
            .all()
        )
        segment_counts_by_file_id = {
            row.file_record_id: int(row.segment_count or 0)
            for row in segment_count_rows
        }

    desired: dict[tuple[UUID, UUID], set[AssignmentTarget]] = {}
    desired_user_ids: set[UUID] = set()
    validated_boundary_ranges: set[tuple[UUID, int, int]] = set()
    for entry in payload.assignments:
        assignee = get_user_by_id(db, entry.assignee_id)
        if assignee is None or not assignee.is_active or assignee.role != USER_ROLE:
            raise HTTPException(status_code=400, detail="只能指派给启用中的普通译者账号。")
        workflow_step_id = entry.workflow_step_id or first_step_id
        if workflow_step_id not in workflow_step_ids:
            raise HTTPException(status_code=400, detail="存在不属于当前项目的流程阶段授权。")
        file_ids = set(entry.file_record_ids or [])
        merge_view_ids = set(entry.merge_view_ids or [])
        if merge_view_ids:
            merge_views = (
                db.query(ProjectMergeView)
                .filter(
                    ProjectMergeView.project_id == project.id,
                    ProjectMergeView.id.in_(merge_view_ids),
                )
                .all()
            )
            found_view_ids = {view.id for view in merge_views}
            if found_view_ids != merge_view_ids:
                raise HTTPException(status_code=400, detail="存在不属于当前项目的合并视图授权。")
            for view in merge_views:
                file_ids.update(parse_file_ids(view.file_ids))
        invalid_file_ids = file_ids - project_file_ids
        if invalid_file_ids:
            raise HTTPException(status_code=400, detail="存在不属于当前项目的文件授权。")
        file_range_targets: set[AssignmentTarget] = set()
        for file_range in entry.file_ranges or []:
            if file_range.file_record_id not in project_file_ids:
                raise HTTPException(status_code=400, detail="存在不属于当前项目的文件范围授权。")
            range_start = file_range.range_start
            range_end = file_range.range_end
            if (range_start is None) != (range_end is None):
                raise HTTPException(status_code=400, detail="句段范围必须同时填写起始段和结束段。")
            if range_start is not None and range_end is not None:
                if range_start > range_end:
                    raise HTTPException(status_code=400, detail="句段范围起始段不能大于结束段。")
                segment_count = segment_counts_by_file_id.get(file_range.file_record_id, 0)
                if segment_count <= 0:
                    raise HTTPException(status_code=400, detail="设置句段范围前，文件必须已有句段。")
                if range_end > segment_count:
                    raise HTTPException(status_code=400, detail="句段范围不能超过文件句段总数。")
                range_key = (file_range.file_record_id, range_start, range_end)
                if range_key not in validated_boundary_ranges:
                    _validate_assignment_range_merge_block_boundary(
                        db,
                        file_record_id=file_range.file_record_id,
                        file_label=project_file_labels.get(
                            file_range.file_record_id,
                            str(file_range.file_record_id),
                        ),
                        range_start=range_start,
                        range_end=range_end,
                    )
                    validated_boundary_ranges.add(range_key)
            file_range_targets.add((file_range.file_record_id, range_start, range_end))
        desired_user_ids.add(assignee.id)
        targets = desired.setdefault((assignee.id, workflow_step_id), set())
        targets.update((file_record_id, None, None) for file_record_id in file_ids)
        targets.update(file_range_targets)

    ranges_by_file_step: dict[tuple[UUID, UUID], list[tuple[UUID, int | None, int | None]]] = {}
    for (assignee_id, workflow_step_id), targets in desired.items():
        for file_record_id, range_start, range_end in targets:
            ranges_by_file_step.setdefault((workflow_step_id, file_record_id), []).append(
                (assignee_id, range_start, range_end)
            )
    for range_items in ranges_by_file_step.values():
        sorted_items = sorted(range_items, key=lambda item: (str(item[0]), item[1] or 0, item[2] or 0))
        for index, current in enumerate(sorted_items):
            for other in sorted_items[index + 1:]:
                if current[0] == other[0]:
                    raise HTTPException(
                        status_code=400,
                        detail="同一译者在同一文件同一流程阶段只能设置一个句段范围。",
                    )
                if _assignment_ranges_overlap(current[1], current[2], other[1], other[2]):
                    raise HTTPException(
                        status_code=400,
                        detail="同一文件同一流程阶段的句段范围不能重叠。",
                    )
    return desired, desired_user_ids


def _sync_legacy_file_assignees(db: Session, project_id: UUID) -> None:
    file_records = (
        db.query(FileRecord)
        .filter(FileRecord.project_id == project_id)
        .order_by(FileRecord.created_at.asc(), FileRecord.id.asc())
        .all()
    )
    for file_record in file_records:
        first_assignment = (
            db.query(FileAssignment)
            .filter(
                FileAssignment.file_record_id == file_record.id,
                FileAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
            )
            .order_by(FileAssignment.assigned_at.asc(), FileAssignment.id.asc())
            .first()
        )
        file_record.assignee_id = first_assignment.assignee_id if first_assignment else None
        file_record.assigned_by_id = first_assignment.assigned_by_id if first_assignment else None
        file_record.assigned_at = first_assignment.assigned_at if first_assignment else None


def _serialize_project_assignments(db: Session, project_id: UUID) -> dict[str, Any]:
    workflow_steps = _load_project_workflow_steps(db, project_id)
    workflow_step_by_id = {step.id: step for step in workflow_steps}
    first_step_id = workflow_steps[0].id if workflow_steps else None
    assignments = (
        db.query(ProjectAssignment)
        .join(User, User.id == ProjectAssignment.assignee_id)
        .filter(
            ProjectAssignment.project_id == project_id,
            ProjectAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
        )
        .order_by(ProjectAssignment.assigned_at.asc(), User.username.asc())
        .all()
    )
    file_rows = (
        db.query(FileAssignment)
        .filter(
            FileAssignment.project_id == project_id,
            FileAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
        )
        .order_by(FileAssignment.assigned_at.asc(), FileAssignment.id.asc())
        .all()
    )
    files_by_user_step: dict[tuple[UUID, UUID], list[str]] = {}
    file_ranges_by_user_step: dict[tuple[UUID, UUID], list[dict[str, Any]]] = {}
    first_file_assignment_by_user_step: dict[tuple[UUID, UUID], FileAssignment] = {}
    for file_assignment in file_rows:
        workflow_step_id = file_assignment.workflow_step_id or first_step_id
        if workflow_step_id is None:
            continue
        key = (file_assignment.assignee_id, workflow_step_id)
        files_by_user_step.setdefault(key, []).append(str(file_assignment.file_record_id))
        file_ranges_by_user_step.setdefault(key, []).append({
            "file_record_id": str(file_assignment.file_record_id),
            "range_start": file_assignment.segment_range_start,
            "range_end": file_assignment.segment_range_end,
        })
        first_file_assignment_by_user_step.setdefault(key, file_assignment)

    assignment_items: list[dict[str, Any]] = []
    for assignment in assignments:
        keys = [
            key
            for key in files_by_user_step
            if key[0] == assignment.assignee_id
        ]
        if not keys and first_step_id is not None:
            keys = [(assignment.assignee_id, first_step_id)]
        for _, workflow_step_id in sorted(
            keys,
            key=lambda key: int(workflow_step_by_id[key[1]].sort_order or 0)
            if key[1] in workflow_step_by_id
            else 0,
        ):
            workflow_step = workflow_step_by_id.get(workflow_step_id)
            file_assignment = first_file_assignment_by_user_step.get((assignment.assignee_id, workflow_step_id))
            assigned_by_id = (
                file_assignment.assigned_by_id
                if file_assignment is not None and file_assignment.assigned_by_id
                else assignment.assigned_by_id
            )
            assigned_at = (
                file_assignment.assigned_at
                if file_assignment is not None
                else assignment.assigned_at
            )
            assignment_items.append({
                "id": str(file_assignment.id if file_assignment is not None else assignment.id),
                "project_assignment_id": str(assignment.id),
                "assignee_id": str(assignment.assignee_id),
                "assignee": serialize_user(assignment.assignee),
                "workflow_step_id": str(workflow_step_id),
                "workflow_step": _serialize_workflow_step(workflow_step) if workflow_step is not None else None,
                "file_record_ids": files_by_user_step.get((assignment.assignee_id, workflow_step_id), []),
                "file_ranges": file_ranges_by_user_step.get((assignment.assignee_id, workflow_step_id), []),
                "assigned_by_id": str(assigned_by_id) if assigned_by_id else None,
                "assigned_at": assigned_at.isoformat(),
            })
    return {
        "project_id": str(project_id),
        "workflow_steps": [_serialize_workflow_step(step) for step in workflow_steps],
        "assignments": assignment_items,
    }


def _update_project_assignments_by_workflow(
    db: Session,
    *,
    project_id: UUID,
    project: Project,
    payload: ProjectAssignmentsRequest,
    current_user: User,
) -> dict[str, Any]:
    desired, desired_user_ids = _validate_assignment_payload(db, project, payload)
    now = datetime.now()
    workflow_steps = _load_project_workflow_steps(db, project_id)
    first_step_id = workflow_steps[0].id if workflow_steps else None

    current_project_assignments = {
        assignment.assignee_id: assignment
        for assignment in (
            db.query(ProjectAssignment)
            .filter(
                ProjectAssignment.project_id == project_id,
                ProjectAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
            )
            .all()
        )
    }

    current_file_assignments: dict[tuple[UUID, UUID], dict[AssignmentTarget, FileAssignment]] = {}
    for assignment in (
        db.query(FileAssignment)
        .filter(
            FileAssignment.project_id == project_id,
            FileAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
        )
        .all()
    ):
        workflow_step_id = assignment.workflow_step_id or first_step_id
        if workflow_step_id is None:
            continue
        current_file_assignments.setdefault(
            (assignment.assignee_id, workflow_step_id),
            {},
        )[
            (
                assignment.file_record_id,
                assignment.segment_range_start,
                assignment.segment_range_end,
            )
        ] = assignment

    for assignee_id, assignment in list(current_project_assignments.items()):
        if assignee_id in desired_user_ids:
            continue
        assignment.status = ASSIGNMENT_STATUS_REVOKED
        assignment.revoked_by_id = current_user.id
        assignment.revoked_at = now
        _record_assignment_event(
            db,
            project_id=project_id,
            assignee_id=assignee_id,
            actor_id=current_user.id,
            action=ASSIGNMENT_EVENT_PROJECT_UNASSIGNED,
            before_payload={"project_id": str(project_id), "status": ASSIGNMENT_STATUS_ACTIVE},
            after_payload={"project_id": str(project_id), "status": ASSIGNMENT_STATUS_REVOKED},
        )

    for assignee_id in sorted(desired_user_ids, key=str):
        project_assignment = current_project_assignments.get(assignee_id)
        if project_assignment is not None:
            continue
        project_assignment = ProjectAssignment(
            project_id=project_id,
            assignee_id=assignee_id,
            assigned_by_id=current_user.id,
            assigned_at=now,
            status=ASSIGNMENT_STATUS_ACTIVE,
        )
        db.add(project_assignment)
        current_project_assignments[assignee_id] = project_assignment
        _record_assignment_event(
            db,
            project_id=project_id,
            assignee_id=assignee_id,
            actor_id=current_user.id,
            action=ASSIGNMENT_EVENT_PROJECT_ASSIGNED,
            before_payload={"project_id": str(project_id), "status": None},
            after_payload={"project_id": str(project_id), "status": ASSIGNMENT_STATUS_ACTIVE},
        )

    for key, active_by_target in current_file_assignments.items():
        assignee_id, workflow_step_id = key
        desired_targets = desired.get(key, set())
        for target, file_assignment in active_by_target.items():
            file_record_id, range_start, range_end = target
            if assignee_id in desired_user_ids and target in desired_targets:
                continue
            file_assignment.status = ASSIGNMENT_STATUS_REVOKED
            file_assignment.revoked_by_id = current_user.id
            file_assignment.revoked_at = now
            range_payload = _serialize_assignment_target_payload(target)
            _record_assignment_event(
                db,
                project_id=project_id,
                file_record_id=file_record_id,
                assignee_id=assignee_id,
                actor_id=current_user.id,
                action=ASSIGNMENT_EVENT_FILE_REVOKED,
                before_payload={
                    "file_record_id": str(file_record_id),
                    "workflow_step_id": str(workflow_step_id),
                    "range_start": range_start,
                    "range_end": range_end,
                    "range": range_payload,
                    "status": ASSIGNMENT_STATUS_ACTIVE,
                },
                after_payload={
                    "file_record_id": str(file_record_id),
                    "workflow_step_id": str(workflow_step_id),
                    "range_start": range_start,
                    "range_end": range_end,
                    "range": range_payload,
                    "status": ASSIGNMENT_STATUS_REVOKED,
                },
            )

    db.flush()

    for (assignee_id, workflow_step_id), desired_targets in desired.items():
        active_file_assignments = current_file_assignments.get((assignee_id, workflow_step_id), {})
        active_targets = set(active_file_assignments)
        for target in sorted(desired_targets - active_targets, key=_assignment_target_sort_key):
            file_record_id, range_start, range_end = target
            file_assignment = FileAssignment(
                project_id=project_id,
                file_record_id=file_record_id,
                workflow_step_id=workflow_step_id,
                assignee_id=assignee_id,
                assigned_by_id=current_user.id,
                assigned_at=now,
                status=ASSIGNMENT_STATUS_ACTIVE,
                segment_range_start=range_start,
                segment_range_end=range_end,
            )
            db.add(file_assignment)
            range_payload = _serialize_assignment_target_payload(target)
            _record_assignment_event(
                db,
                project_id=project_id,
                file_record_id=file_record_id,
                assignee_id=assignee_id,
                actor_id=current_user.id,
                action=ASSIGNMENT_EVENT_FILE_GRANTED,
                before_payload={
                    "file_record_id": str(file_record_id),
                    "workflow_step_id": str(workflow_step_id),
                    "range_start": range_start,
                    "range_end": range_end,
                    "range": range_payload,
                    "status": None,
                },
                after_payload={
                    "file_record_id": str(file_record_id),
                    "workflow_step_id": str(workflow_step_id),
                    "range_start": range_start,
                    "range_end": range_end,
                    "range": range_payload,
                    "status": ASSIGNMENT_STATUS_ACTIVE,
                },
            )

    _sync_legacy_file_assignees(db, project_id)
    db.commit()
    return _serialize_project_assignments(db, project_id)


@router.get("/analytics/dashboard")
def get_analytics_dashboard(
    background_tasks: BackgroundTasks,
    granularity: Literal["day", "month"] = "day",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not can_access_all_projects(current_user):
        raise HTTPException(status_code=403, detail="当前账号无权查看全局数据看板。")
    background_tasks.add_task(run_analytics_backfill_once)
    return get_dashboard_payload(db, granularity=granularity)


@router.get("/notifications")
def list_notifications(
    unread_only: bool = False,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from sqlalchemy.orm import joinedload

    safe_limit = min(max(limit, 1), 100)
    query = (
        db.query(Notification)
        .options(
            joinedload(Notification.project),
            joinedload(Notification.file_record),
        )
        .filter(Notification.user_id == current_user.id)
    )
    if unread_only:
        query = query.filter(Notification.read_at.is_(None))
    notifications = (
        query.order_by(Notification.created_at.desc())
        .limit(safe_limit)
        .all()
    )
    unread_count = (
        db.query(Notification.id)
        .filter(Notification.user_id == current_user.id, Notification.read_at.is_(None))
        .count()
    )
    return {
        "items": [_serialize_notification(notification) for notification in notifications],
        "unread_count": unread_count,
    }


@router.patch("/notifications/read-all")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    now = datetime.now()
    (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id, Notification.read_at.is_(None))
        .update({Notification.read_at: now}, synchronize_session=False)
    )
    db.commit()
    return {"read_at": now.isoformat()}


@router.patch("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notification = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == current_user.id)
        .first()
    )
    if notification is None:
        raise HTTPException(status_code=404, detail="消息不存在。")
    if notification.read_at is None:
        notification.read_at = datetime.now()
        db.commit()
        db.refresh(notification)
    return _serialize_notification(notification)


@router.get("/assignment-events")
def list_assignment_events(
    project_id: UUID | None = None,
    assignee_id: UUID | None = None,
    action: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    from sqlalchemy.orm import joinedload

    safe_limit = min(max(limit, 1), 300)
    query = db.query(AssignmentEvent).options(
        joinedload(AssignmentEvent.project),
        joinedload(AssignmentEvent.file_record),
        joinedload(AssignmentEvent.assignee),
        joinedload(AssignmentEvent.actor),
    )
    if project_id is not None:
        query = query.filter(AssignmentEvent.project_id == project_id)
    if assignee_id is not None:
        query = query.filter(AssignmentEvent.assignee_id == assignee_id)
    if action:
        query = query.filter(AssignmentEvent.action == action)
    events = query.order_by(AssignmentEvent.created_at.desc()).limit(safe_limit).all()
    return {"items": [_serialize_assignment_event(event) for event in events]}


@router.get("/projects/{project_id}/assignment-events")
def list_project_assignment_events(
    project_id: UUID,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    _get_project_or_404(db, project_id)
    return list_assignment_events(project_id=project_id, limit=limit, db=db)


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


def _serialize_guideline_template(template: GuidelineTemplate, include_content: bool = False) -> dict:
    payload = {
        "id": template.id,
        "name": template.name,
        "filename": template.filename,
        "size_bytes": template.size_bytes,
        "updated_at": template.updated_at.isoformat(),
        "content_preview": template.content[:180],
    }
    if include_content:
        payload["content"] = template.content
    return payload


def _resolve_llm_guidelines(
    db: Session,
    file_record: FileRecord,
    body: LLMTranslateRequest,
) -> str:
    project_guidelines = ""
    if file_record.project_id:
        project = db.query(Project).filter(Project.id == file_record.project_id).first()
        if project:
            project_guidelines = (project.translation_guidelines or "").strip()

    parts: list[str] = []
    if project_guidelines:
        parts.append(project_guidelines)

    if body.guideline_template_id:
        try:
            template = read_guideline_template(db, body.guideline_template_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="选择的翻译细则模板不存在。") from exc
        parts.append(f"【可复用细则：{template.name}】\n{template.content.strip()}")

    temporary_prompt = (body.temporary_prompt or "").strip()
    legacy_guidelines = (body.translation_guidelines or "").strip()
    if legacy_guidelines and not temporary_prompt:
        temporary_prompt = legacy_guidelines

    if temporary_prompt and temporary_prompt != project_guidelines:
        parts.append(f"【本次临时提示词】\n{temporary_prompt}")

    return "\n\n".join(part for part in parts if part.strip())


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _build_llm_translation_tasks(
    db: Session,
    file_record_id: UUID,
    scope: Literal["current_segment", "fuzzy_only", "none_only", "empty_target_only", "all", "all_with_exact"],
    source_language: str | None = None,
    target_language: str | None = None,
    collection_id: UUID | None = None,
    glossary_base_ids: list[UUID] | None = None,
    include_context: bool = False,
    sentence_id: str | None = None,
    source_filename: str | None = None,
) -> list[LLMTranslationTask]:
    statuses_by_scope = {
        "fuzzy_only": {"fuzzy"},
        "none_only": {"none"},
        "all": {"fuzzy", "none"},
        "all_with_exact": {"exact", "fuzzy", "none"},
    }
    target_statuses: set[str] | None = None
    current_sentence_id = normalize_text(sentence_id or "")
    if scope == "current_segment":
        if not current_sentence_id:
            raise ValueError("当前句段范围需要提供 sentence_id。")
    elif scope != "empty_target_only":
        target_statuses = statuses_by_scope.get(scope)
        if target_statuses is None:
            raise ValueError(f"不支持的 scope: {scope}")
    segments = list_segments_for_file_record(db, file_record_id)
    tm_target_text_map = get_tm_target_text_map(
        db,
        [segment.matched_source_text for segment in segments if segment.matched_source_text],
        collection_id=collection_id,
        source_language=source_language,
        target_language=target_language,
    )
    glossary_matches_by_source = build_glossary_matches_by_text(
        db,
        [segment.source_text for segment in segments if segment.source_text],
        glossary_base_ids or [],
        source_language=source_language,
        target_language=target_language,
    )

    clean_numbering = is_word_document_filename(source_filename)
    tasks: list[LLMTranslationTask] = []
    for segment in segments:
        should_translate = True
        if scope == "current_segment":
            should_translate = segment.sentence_id == current_sentence_id
        elif scope == "empty_target_only":
            if normalize_text(segment.target_text or ""):
                should_translate = False
        elif target_statuses is None or segment.status not in target_statuses:
            should_translate = False

        if not should_translate and not include_context:
            continue

        segment_source = getattr(segment, "source", "none")
        matched_source_text = getattr(segment, "matched_source_text", None)
        segment_tm_target_text = segment.target_text if segment_source == "tm" and normalize_text(segment.target_text) else ""
        tm_target_text = segment_tm_target_text or tm_target_text_map.get(matched_source_text or "", "")
        if clean_numbering:
            raw_matched_source_text = matched_source_text
            matched_source_text = strip_automatic_numbering_prefix(
                matched_source_text or "",
                source_text=segment.source_text,
                display_text=getattr(segment, "display_text", "") or "",
                reference_texts=[raw_matched_source_text],
            ) or None
            tm_target_text = strip_automatic_numbering_prefix(
                tm_target_text,
                source_text=segment.source_text,
                display_text=getattr(segment, "display_text", "") or "",
                reference_texts=[raw_matched_source_text],
            )

        tasks.append(
            LLMTranslationTask(
                sentence_id=segment.sentence_id,
                status=segment.status,
                source_text=segment.source_text,
                source_language=source_language,
                target_language=target_language,
                block_type=getattr(segment, "block_type", "paragraph") or "paragraph",
                block_index=int(getattr(segment, "block_index", 0) or 0),
                row_index=getattr(segment, "row_index", None),
                cell_index=getattr(segment, "cell_index", None),
                matched_source_text=matched_source_text,
                tm_target_text=tm_target_text,
                glossary_matches=glossary_matches_by_source.get(segment.source_text, []),
                should_translate=should_translate,
                project_sync_disabled=bool(getattr(segment, "project_sync_disabled", False)),
            )
        )

    return tasks


@dataclass(frozen=True)
class LLMDeduplicationResult:
    tasks: list[LLMTranslationTask]
    result_sentence_ids_by_representative: dict[str, list[str]]
    unique_total: int
    deduplicated_count: int


def _llm_task_has_tm_context(task: LLMTranslationTask) -> bool:
    return task.status in {"exact", "fuzzy"} and bool(normalize_text(task.tm_target_text or ""))


def _select_llm_representative(
    grouped_tasks: list[tuple[int, LLMTranslationTask]],
) -> LLMTranslationTask:
    _, task = min(
        grouped_tasks,
        key=lambda item: (
            0 if _llm_task_has_tm_context(item[1]) else 1,
            item[0],
        ),
    )
    return task


def _deduplicate_llm_translation_tasks(
    tasks: list[LLMTranslationTask],
) -> LLMDeduplicationResult:
    grouped_by_source_hash: dict[str, list[tuple[int, LLMTranslationTask]]] = {}
    target_total = 0
    for index, task in enumerate(tasks):
        if not task.should_translate:
            continue
        target_total += 1
        if task.project_sync_disabled:
            continue
        grouped_by_source_hash.setdefault(build_source_hash(task.source_text), []).append((index, task))

    result_sentence_ids_by_representative: dict[str, list[str]] = {}
    suppressed_sentence_ids: set[str] = set()
    for grouped_tasks in grouped_by_source_hash.values():
        representative = _select_llm_representative(grouped_tasks)
        sentence_ids = [task.sentence_id for _, task in grouped_tasks]
        result_sentence_ids_by_representative[representative.sentence_id] = sentence_ids
        suppressed_sentence_ids.update(
            sentence_id
            for sentence_id in sentence_ids
            if sentence_id != representative.sentence_id
        )

    deduplicated_tasks: list[LLMTranslationTask] = []
    for task in tasks:
        if task.should_translate and task.sentence_id in suppressed_sentence_ids:
            deduplicated_tasks.append(replace(task, should_translate=False))
            continue
        deduplicated_tasks.append(task)
        if task.should_translate:
            result_sentence_ids_by_representative.setdefault(task.sentence_id, [task.sentence_id])

    unique_total = sum(1 for task in deduplicated_tasks if task.should_translate)
    return LLMDeduplicationResult(
        tasks=deduplicated_tasks,
        result_sentence_ids_by_representative=result_sentence_ids_by_representative,
        unique_total=unique_total,
        deduplicated_count=target_total - unique_total,
    )


@router.get("/guideline-templates")
def list_translation_guideline_templates(db: Session = Depends(get_db)):
    """列出仓库中可复用的 Markdown 翻译细则模板。"""
    return [
        _serialize_guideline_template(template)
        for template in list_guideline_templates(db)
    ]


@router.post("/guideline-templates/import")
async def import_translation_guideline_template(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """导入 .md/.txt 翻译细则，并统一保存为仓库内 UTF-8 Markdown。"""
    raw_bytes = await _read_upload_bytes_with_limit(file)
    try:
        template = save_guideline_template(db, file.filename or "", raw_bytes, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_guideline_template(template, include_content=True)


@router.get("/guideline-templates/{template_id}")
def get_translation_guideline_template(template_id: str, db: Session = Depends(get_db)):
    try:
        template = read_guideline_template(db, template_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="翻译细则模板不存在。") from exc
    return _serialize_guideline_template(template, include_content=True)


@router.put("/guideline-templates/{template_id}")
def update_translation_guideline_template(
    template_id: str,
    payload: GuidelineTemplateUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        template = update_guideline_template(db, template_id, payload.content, user_id=current_user.id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="翻译细则模板不存在。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_guideline_template(template, include_content=True)


@router.delete("/guideline-templates/{template_id}", status_code=204)
def delete_translation_guideline_template(template_id: str, db: Session = Depends(get_db)):
    try:
        delete_guideline_template(db, template_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="翻译细则模板不存在。") from exc
    return Response(status_code=204)


# 支持的文件扩展名（30种格式）
SUPPORTED_EXTENSIONS = {
    # 办公文档
    ".doc", ".docx", ".txt", ".dat", ".pdf", ".pptx", ".xlsx",
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


def _raise_upload_limit_error(exc: UploadLimitError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _raise_upload_file_too_large(filename: str, max_bytes: int) -> None:
    max_mb = round(max_bytes / (1024 * 1024), 2)
    raise HTTPException(
        status_code=413,
        detail=f"文件 {filename or 'uploaded'} 超过大小限制（{max_mb} MB）。",
    )


def _read_upload_file_bytes_with_limit(file: UploadFile) -> bytes:
    filename = file.filename or "uploaded"
    max_bytes = get_max_upload_size_bytes(filename)
    chunks: list[bytes] = []
    total_size = 0
    try:
        file.file.seek(0)
    except (AttributeError, OSError):
        pass
    while True:
        chunk = file.file.read(UPLOAD_READ_CHUNK_SIZE)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > max_bytes:
            _raise_upload_file_too_large(filename, max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_upload_bytes_with_limit(file: UploadFile) -> bytes:
    filename = file.filename or "uploaded"
    max_bytes = get_max_upload_size_bytes(filename)
    chunks: list[bytes] = []
    total_size = 0
    while True:
        chunk = await file.read(UPLOAD_READ_CHUNK_SIZE)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > max_bytes:
            _raise_upload_file_too_large(filename, max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def _normalize_upload_document_parse_mode(document_parse_mode: str | None) -> str:
    try:
        return normalize_document_parse_mode(document_parse_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _normalize_upload_document_parse_options(
    document_parse_options: str | None,
    document_parse_mode: str,
) -> dict[str, object]:
    try:
        return normalize_document_parse_options(document_parse_options, document_parse_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _normalize_collection_name(name: str) -> str:
    return " ".join(name.strip().split())


def _build_default_save_to_tm_collection_name(file_record: FileRecord) -> str:
    filename = (file_record.filename or "").strip()
    stem = Path(filename).stem.strip() if filename else ""
    base_name = stem or filename or "任务记忆库"
    return _normalize_collection_name(f"{base_name} 记忆库")


def _build_unique_memory_base_name(db: Session, name: str) -> str:
    base_name = _normalize_collection_name(name) or "任务记忆库"
    if len(base_name) > 255:
        base_name = base_name[:255].rstrip() or "任务记忆库"

    candidate = base_name
    suffix_index = 2
    while db.query(MemoryBase.id).filter(MemoryBase.name == candidate).first() is not None:
        suffix = f" ({suffix_index})"
        candidate_base = base_name[: 255 - len(suffix)].rstrip() or "任务记忆库"
        candidate = f"{candidate_base}{suffix}"
        suffix_index += 1

    return candidate


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


def _validate_term_base_ids(
    db: Session,
    term_base_ids: list[UUID] | None,
) -> list[TermBase]:
    if not term_base_ids:
        return []

    normalized_ids = list(dict.fromkeys(term_base_ids))
    term_bases = (
        db.query(TermBase)
        .filter(TermBase.id.in_(normalized_ids))
        .all()
    )
    term_base_by_id = {term_base.id: term_base for term_base in term_bases}
    missing_ids = [term_base_id for term_base_id in normalized_ids if term_base_id not in term_base_by_id]
    if missing_ids:
        raise HTTPException(status_code=404, detail="选择的术语库不存在。")

    return [term_base_by_id[term_base_id] for term_base_id in normalized_ids]


def _validate_glossary_base_ids(
    db: Session,
    glossary_base_ids: list[UUID] | None,
) -> list[GlossaryBase]:
    if not glossary_base_ids:
        return []

    normalized_ids = list(dict.fromkeys(glossary_base_ids))
    glossary_bases = (
        db.query(GlossaryBase)
        .filter(GlossaryBase.id.in_(normalized_ids))
        .all()
    )
    glossary_base_by_id = {glossary_base.id: glossary_base for glossary_base in glossary_bases}
    missing_ids = [
        glossary_base_id
        for glossary_base_id in normalized_ids
        if glossary_base_id not in glossary_base_by_id
    ]
    if missing_ids:
        raise HTTPException(status_code=404, detail="选择的词汇表不存在。")

    return [glossary_base_by_id[glossary_base_id] for glossary_base_id in normalized_ids]


def _require_selected_collection_ids(
    collection_ids: list[UUID] | None,
) -> list[UUID]:
    if not collection_ids:
        raise HTTPException(
            status_code=400,
            detail="请至少选择一个 TM 记忆库，避免全库模糊匹配拖慢处理进程。",
        )
    return collection_ids


def _resolve_upload_language_pair(
    source_language: str | None,
    target_language: str | None,
    primary_collection: TMCollection | None = None,
) -> tuple[str, str]:
    if source_language or target_language:
        resolved_source_language, resolved_target_language = _require_tm_language_pair(
            source_language,
            target_language,
        )
        _ensure_resource_language_pair_matches(
            primary_collection,
            resolved_source_language,
            resolved_target_language,
            "记忆库",
        )
        return resolved_source_language, resolved_target_language

    if primary_collection and primary_collection.source_language and primary_collection.target_language:
        return primary_collection.source_language, primary_collection.target_language

    raise HTTPException(status_code=400, detail="上传文件前请选择源语言和目标语言。")


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


def _require_same_tm_collection_language_pair(
    collections: list[MemoryBase],
) -> tuple[str, str]:
    try:
        source_language, target_language = require_language_pair(
            collections[0].source_language,
            collections[0].target_language,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="选中的记忆库缺少语言对，无法合并。") from exc

    for collection in collections[1:]:
        try:
            candidate_source_language, candidate_target_language = require_language_pair(
                collection.source_language,
                collection.target_language,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="选中的记忆库缺少语言对，无法合并。") from exc
        if (
            candidate_source_language != source_language
            or candidate_target_language != target_language
        ):
            raise HTTPException(status_code=400, detail="只能合并语言对完全一致的记忆库。")

    return source_language, target_language


@router.post("/parser/slate")
def upload_for_slate(
    file: UploadFile = File(...),
    threshold: float = Form(default=0.6),
    collection_ids: list[UUID] | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """上传文件并解析为 Slate 编辑器格式

    目前仅支持 DOCX 格式。

    定义为同步 def，由 FastAPI 调度到线程池执行，避免解析/数据库等阻塞操作卡住事件循环。
    """
    _validate_docx_upload(file)

    raw_bytes = _read_upload_file_bytes_with_limit(file)
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

    raw_bytes = await _read_upload_bytes_with_limit(file)
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")

    selected_collection_ids = _validate_collection_ids(db, collection_ids)
    required_collection_ids = _require_selected_collection_ids(selected_collection_ids)
    try:
        return await _build_task_workspace_async(
            raw_bytes=raw_bytes,
            filename=file.filename or "untitled.txt",
            similarity_threshold=threshold,
            collection_ids=required_collection_ids,
            document_parse_mode=DOCUMENT_PARSE_MODE_FULL,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/parser/parse")
def parse_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """通用文档解析接口

    使用适配器系统解析多种格式的文档。

    定义为同步 def，由 FastAPI 调度到线程池执行，避免 CPU 密集的解析阻塞事件循环。
    """
    ext = _validate_file_upload(file)

    raw_bytes = _read_upload_file_bytes_with_limit(file)
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


@router.get("/import-tasks/{task_id}")
def get_import_task(task_id: str):
    status = _get_import_task_status(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="导入任务不存在或已过期。")
    return status


@router.post("/import-tasks/{task_id}/cancel")
def cancel_import_task(task_id: str):
    status = request_import_task_cancel(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="导入任务不存在或已过期。")
    return status


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
    raw_bytes = await _read_upload_bytes_with_limit(file)
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
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    threshold: float = Form(default=0.6),
    collection_ids: list[UUID] | None = Form(default=None),
    term_base_id: UUID | None = Form(default=None),
    source_language: str | None = Form(default=None),
    target_language: str | None = Form(default=None),
    document_parse_mode: str = Form(default=DOCUMENT_PARSE_MODE_FULL),
    document_parse_options: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """上传文档并创建持久化记录"""
    _validate_task_upload(file)
    document_parse_mode = _normalize_upload_document_parse_mode(document_parse_mode)
    normalized_parse_options = _normalize_upload_document_parse_options(document_parse_options, document_parse_mode)

    selected_collection_ids = _validate_collection_ids(db, collection_ids) or []
    primary_collection = _get_collection_or_404(
        db,
        selected_collection_ids[0] if selected_collection_ids else None,
    )
    resolved_source_language, resolved_target_language = _resolve_upload_language_pair(
        source_language,
        target_language,
        primary_collection,
    )

    # 验证术语库是否存在
    term_base = None
    if term_base_id is not None:
        term_base = db.query(TermBase).filter(TermBase.id == term_base_id).first()
        if term_base is None:
            raise HTTPException(status_code=404, detail="术语库不存在。")
        _ensure_resource_language_pair_matches(
            term_base,
            resolved_source_language,
            resolved_target_language,
            "术语库",
        )

    payload = {
        "kind": "file_record",
        "threshold": threshold,
        "collection_ids": [str(collection_id) for collection_id in selected_collection_ids],
        "term_base_id": str(term_base_id) if term_base_id is not None else None,
        "source_language": resolved_source_language,
        "target_language": resolved_target_language,
        "document_parse_mode": document_parse_mode,
        "document_parse_options": normalized_parse_options,
        "creator_id": str(current_user.id),
    }
    return await _queue_import_task(
        background_tasks,
        payload,
        staging_upload_files=[(file.filename or "untitled.txt", file.file)],
    )


@router.get("/workflow-templates")
def list_workflow_templates(_: User = Depends(get_current_user)):
    return {
        "items": [
            _serialize_workflow_template(template)
            for template in WORKFLOW_TEMPLATE_DEFINITIONS
        ]
    }


@router.post("/projects")
def create_project(
    payload: ProjectCreatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """仅填写基础信息创建项目，文档导入在项目详情页完成"""
    from datetime import datetime as _dt

    deadline_dt = None
    if payload.deadline:
        try:
            deadline_dt = _dt.fromisoformat(payload.deadline)
        except ValueError:
            raise HTTPException(status_code=400, detail="截止期限格式不正确，请使用 ISO 格式。")

    project_name = payload.name.strip()
    if not project_name:
        raise HTTPException(status_code=400, detail="项目名称不能为空。")

    source_language = None
    target_language = None
    if payload.source_language or payload.target_language:
        source_language, target_language = _require_tm_language_pair(
            payload.source_language,
            payload.target_language,
        )

    project = Project(
        name=project_name,
        status="draft",
        source_language=source_language,
        target_language=target_language,
        creator_id=current_user.id,
        deadline=deadline_dt,
        access_level=payload.access_level,
    )
    db.add(project)
    db.flush()
    _create_project_workflow_steps(
        db,
        project,
        requested_steps=payload.workflow_steps,
        template_id=payload.workflow_template_id,
    )
    db.commit()
    db.refresh(project)

    return {
        "id": str(project.id),
        "name": project.name,
        "filename": project.name,
        "status": project.status,
        "source_language": project.source_language,
        "target_language": project.target_language,
        "creator": get_user_display_name(current_user),
        "deadline": project.deadline.isoformat() if project.deadline else None,
        "access_level": project.access_level,
        "created_at": project.created_at.isoformat(),
    }


@router.post("/projects/{project_id}/duplicate")
def duplicate_project(
    project_id: UUID,
    payload: ProjectDuplicatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    source_project = (
        db.query(Project)
        .filter(Project.id == project_id)
        .first()
    )
    if not source_project:
        raise HTTPException(status_code=404, detail="项目不存在。")

    project_name = payload.name.strip()
    if not project_name:
        raise HTTPException(status_code=400, detail="项目名称不能为空。")

    if "deadline" in payload.model_fields_set:
        deadline_dt = _parse_optional_datetime(payload.deadline)
        if payload.deadline and deadline_dt is None:
            raise HTTPException(status_code=400, detail="截止期限格式不正确，请使用 ISO 格式。")
    else:
        deadline_dt = source_project.deadline

    source_language = source_project.source_language
    target_language = source_project.target_language
    source_files = (
        db.query(FileRecord)
        .filter(FileRecord.project_id == source_project.id)
        .order_by(FileRecord.created_at.asc(), FileRecord.id.asc())
        .all()
    )
    if not source_language or not target_language:
        pair_map = _project_file_language_pair_map(source_files)
        if len(pair_map) == 1:
            source_language, target_language = next(iter(pair_map))

    duplicate = Project(
        name=project_name,
        status="draft",
        document_parse_mode=source_project.document_parse_mode,
        source_language=source_language,
        target_language=target_language,
        creator_id=current_user.id,
        deadline=deadline_dt,
        access_level=payload.access_level or source_project.access_level or "team",
        translation_guidelines=(
            payload.translation_guidelines
            if payload.translation_guidelines is not None
            else source_project.translation_guidelines
        ) or "",
    )

    db.add(duplicate)
    db.flush()
    _copy_project_workflow_steps(db, source_project.id, duplicate)
    db.commit()
    db.refresh(duplicate)

    return _build_project_detail_payload(
        db,
        duplicate,
        [],
        {},
        current_user=current_user,
    )


def _build_project_summary_payload(
    project: Project,
    total_segments: int,
    translated_segments: int,
    file_count: int,
    pretranslated_segments: int = 0,
    creator_name: str | None = None,
    issue_stats: dict[str, int] | None = None,
    current_user: User | None = None,
    assigned_users: list[User] | None = None,
    workflow_steps: list[ProjectWorkflowStep] | None = None,
    workflow_progress: list[dict[str, Any]] | None = None,
) -> dict:
    progress = calculate_file_record_progress(total_segments, translated_segments)
    if workflow_progress:
        progress = _calculate_workflow_overall_progress(workflow_progress, progress)
    pretranslation_progress = calculate_file_record_progress(total_segments, pretranslated_segments)
    effective_status = (
        resolve_file_record_status("in_progress", total_segments, translated_segments)
        if total_segments > 0
        else project.status
    )
    issue_stats = issue_stats or {"issue_count": 0, "open_issue_count": 0}
    return {
        "id": str(project.id),
        "name": project.name,
        "filename": project.name,
        "status": effective_status,
        "progress": progress,
        "total_segments": total_segments,
        "translated_segments": translated_segments,
        "confirmed_segments": translated_segments,
        "pretranslated_segments": pretranslated_segments,
        "pretranslation_progress": pretranslation_progress,
        "source_language": project.source_language,
        "target_language": project.target_language,
        "creator": creator_name,
        "deadline": project.deadline.isoformat() if project.deadline else None,
        "access_level": project.access_level,
        "translation_guidelines": project.translation_guidelines or "",
        "file_count": file_count,
        "issue_count": issue_stats.get("issue_count", 0),
        "open_issue_count": issue_stats.get("open_issue_count", 0),
        "assigned_users": _serialize_user_list(assigned_users),
        "workflow_steps": [_serialize_workflow_step(step) for step in (workflow_steps or [])],
        "workflow_progress": workflow_progress or [],
        "can_manage": _can_manage_workflow(current_user),
        "can_write": bool(current_user) and (can_access_all_projects(current_user) or file_count > 0),
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


def _build_project_file_payload(
    file_record: FileRecord,
    total_segments: int,
    translated_segments: int,
    pretranslated_segments: int = 0,
    issue_stats: dict[str, int] | None = None,
    current_user: User | None = None,
    assignees: list[User] | None = None,
    workflow_steps: list[ProjectWorkflowStep] | None = None,
    workflow_progress: list[dict[str, Any]] | None = None,
) -> dict:
    source_bytes = load_file_record_source(file_record)
    operation_state = (
        serialize_file_operation_state(file_record)
        if hasattr(file_record, "active_operation")
        else {
            "active_operation": None,
            "active_operation_message": "",
            "is_edit_locked": False,
        }
    )
    progress = calculate_file_record_progress(total_segments, translated_segments)
    if workflow_progress:
        progress = _calculate_workflow_overall_progress(workflow_progress, progress)
    pretranslation_progress = calculate_file_record_progress(total_segments, pretranslated_segments)
    effective_status = resolve_file_record_status(
        file_record.status,
        total_segments=total_segments,
        translated_segments=translated_segments,
    )
    issue_stats = issue_stats or {"issue_count": 0, "open_issue_count": 0}
    assignee_id = getattr(file_record, "assignee_id", None)
    assignee = getattr(file_record, "assignee", None)
    assigned_at = getattr(file_record, "assigned_at", None)
    deadline = getattr(file_record, "deadline", None)
    collection_ids = _load_file_record_collection_ids(file_record)
    term_base_ids = _load_file_record_term_base_ids(file_record)
    term_base_write_ids = _load_file_record_term_base_write_ids(file_record)
    qa_term_base_ids = _load_file_record_qa_term_base_ids(file_record)
    glossary_base_ids = _load_file_record_glossary_base_ids(file_record)

    return {
        "id": str(file_record.id),
        "project_id": str(file_record.project_id) if file_record.project_id else None,
        "filename": file_record.filename,
        "status": effective_status,
        "document_parse_mode": getattr(file_record, "document_parse_mode", DOCUMENT_PARSE_MODE_FULL),
        "document_parse_options": _get_file_record_document_parse_options(file_record),
        "document_statistics": get_file_record_document_statistics(file_record),
        "progress": progress,
        "total_segments": total_segments,
        "translated_segments": translated_segments,
        "confirmed_segments": translated_segments,
        "pretranslated_segments": pretranslated_segments,
        "pretranslation_progress": pretranslation_progress,
        "source_language": file_record.source_language,
        "target_language": file_record.target_language,
        "creator": get_user_display_name(file_record.creator) if file_record.creator else None,
        "assignee_id": str(assignee_id) if assignee_id else None,
        "assignee": _serialize_assignee(assignee),
        "assignees": _serialize_user_list(assignees),
        "assigned_at": assigned_at.isoformat() if assigned_at else None,
        "workflow_steps": [_serialize_workflow_step(step) for step in (workflow_steps or [])],
        "workflow_progress": workflow_progress or [],
        "deadline": deadline.isoformat() if deadline else None,
        "access_level": file_record.access_level,
        "created_at": file_record.created_at.isoformat(),
        "updated_at": file_record.updated_at.isoformat(),
        "has_source_document": source_bytes is not None,
        "file_size_bytes": len(source_bytes) if source_bytes is not None else None,
        "collection_id": str(file_record.collection_id) if file_record.collection_id else None,
        "collection_ids": [str(collection_id) for collection_id in collection_ids],
        "tm_match_threshold": _normalize_tm_match_threshold(getattr(file_record, "tm_match_threshold", None)),
        "term_base_id": file_record.term_base_id,
        "term_base_ids": [str(term_base_id) for term_base_id in term_base_ids],
        "term_base_write_ids": [str(term_base_id) for term_base_id in term_base_write_ids],
        "qa_term_base_ids": [str(term_base_id) for term_base_id in qa_term_base_ids],
        "glossary_base_ids": [str(glossary_base_id) for glossary_base_id in glossary_base_ids],
        "issue_count": issue_stats.get("issue_count", 0),
        "open_issue_count": issue_stats.get("open_issue_count", 0),
        "can_manage": _can_manage_workflow(current_user),
        "can_write": _can_write_file_record(file_record, current_user),
        **operation_state,
    }


def _get_file_segment_stats(db: Session, file_record_ids: list[UUID]) -> dict[UUID, dict]:
    if not file_record_ids:
        return {}

    from sqlalchemy import case as sql_case

    stats_rows = (
        db.query(
            Segment.file_record_id,
            func.count(Segment.id).label("total"),
            func.count(sql_case((Segment.status == "confirmed", 1))).label("confirmed"),
            func.count(sql_case((Segment.target_text != "", 1))).label("pretranslated"),
        )
        .filter(Segment.file_record_id.in_(file_record_ids))
        .group_by(Segment.file_record_id)
        .all()
    )
    return {
        row.file_record_id: {
            "total": row.total,
            "filled": row.confirmed,
            "confirmed": row.confirmed,
            "pretranslated": row.pretranslated,
        }
        for row in stats_rows
    }


def _get_project_stats(
    db: Session,
    project_ids: list[UUID],
    current_user: User | None = None,
) -> dict[UUID, dict]:
    if not project_ids:
        return {}

    from sqlalchemy import case as sql_case

    query = (
        db.query(
            FileRecord.project_id,
            func.count(func.distinct(FileRecord.id)).label("file_count"),
            func.count(Segment.id).label("total"),
            func.count(sql_case((Segment.status == "confirmed", 1))).label("confirmed"),
            func.count(sql_case((Segment.target_text != "", 1))).label("pretranslated"),
        )
        .outerjoin(Segment, Segment.file_record_id == FileRecord.id)
        .filter(FileRecord.project_id.in_(project_ids))
    )
    if current_user is not None and is_external_translator(current_user):
        query = query.join(
            FileAssignment,
            FileAssignment.file_record_id == FileRecord.id,
        ).filter(
            FileAssignment.assignee_id == current_user.id,
            FileAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
        )
    stats_rows = query.group_by(FileRecord.project_id).all()
    return {
        row.project_id: {
            "file_count": row.file_count,
            "total": row.total,
            "filled": row.confirmed,
            "confirmed": row.confirmed,
            "pretranslated": row.pretranslated,
        }
        for row in stats_rows
    }


def _get_project_issue_stats(
    db: Session,
    project_ids: list[UUID],
    current_user: User | None = None,
) -> dict[UUID, dict[str, int]]:
    if not project_ids:
        return {}

    from sqlalchemy import case as sql_case

    query = (
        db.query(
            IssueMarker.project_id,
            func.count(IssueMarker.id).label("issue_count"),
            func.count(sql_case((IssueMarker.status == "open", 1))).label("open_issue_count"),
        )
        .filter(IssueMarker.project_id.in_(project_ids))
    )
    if current_user is not None and is_external_translator(current_user):
        assigned_file_ids = (
            db.query(FileAssignment.file_record_id)
            .join(FileRecord, FileRecord.id == FileAssignment.file_record_id)
            .filter(
                FileRecord.project_id.in_(project_ids),
                FileAssignment.assignee_id == current_user.id,
                FileAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
            )
        )
        query = query.filter(IssueMarker.file_record_id.in_(assigned_file_ids))
    stats_rows = query.group_by(IssueMarker.project_id).all()
    return {
        row.project_id: {
            "issue_count": int(row.issue_count or 0),
            "open_issue_count": int(row.open_issue_count or 0),
        }
        for row in stats_rows
    }


def _get_file_issue_stats(db: Session, file_record_ids: list[UUID]) -> dict[UUID, dict[str, int]]:
    if not file_record_ids:
        return {}

    from sqlalchemy import case as sql_case

    stats_rows = (
        db.query(
            IssueMarker.file_record_id,
            func.count(IssueMarker.id).label("issue_count"),
            func.count(sql_case((IssueMarker.status == "open", 1))).label("open_issue_count"),
        )
        .filter(IssueMarker.file_record_id.in_(file_record_ids))
        .group_by(IssueMarker.file_record_id)
        .all()
    )
    return {
        row.file_record_id: {
            "issue_count": int(row.issue_count or 0),
            "open_issue_count": int(row.open_issue_count or 0),
        }
        for row in stats_rows
        if row.file_record_id is not None
    }


def _project_file_language_pair_map(files: list[FileRecord]) -> dict[tuple[str, str], list[FileRecord]]:
    pair_map: dict[tuple[str, str], list[FileRecord]] = {}
    for file_record in files:
        try:
            source_language, target_language = require_language_pair(
                file_record.source_language,
                file_record.target_language,
            )
        except ValueError:
            continue
        pair_map.setdefault((source_language, target_language), []).append(file_record)
    return pair_map


def _serialize_project_term_base_settings(
    db: Session,
    project: Project,
    files: list[FileRecord],
) -> dict[str, Any]:
    pair_map = _project_file_language_pair_map(files)
    term_bases = db.query(TermBase).order_by(TermBase.name.asc(), TermBase.created_at.desc()).all()
    term_base_ids = [term_base.id for term_base in term_bases]
    entry_counts: dict[UUID, int] = {}
    if term_base_ids:
        entry_counts = {
            term_base_id: int(entry_count or 0)
            for term_base_id, entry_count in (
                db.query(TermEntry.term_base_id, func.count(TermEntry.id))
                .filter(TermEntry.term_base_id.in_(term_base_ids))
                .group_by(TermEntry.term_base_id)
                .all()
            )
        }

    groups: list[dict[str, Any]] = []
    for source_language, target_language in sorted(pair_map):
        group_files = pair_map[(source_language, target_language)]
        enabled_ids: list[UUID] = []
        writable_ids: list[UUID] = []
        qa_ids: list[UUID] = []
        for file_record in group_files:
            enabled_ids.extend(_load_file_record_term_base_ids(file_record))
            writable_ids.extend(_load_file_record_term_base_write_ids(file_record))
            qa_ids.extend(_load_file_record_qa_term_base_ids(file_record))
        enabled_ids = list(dict.fromkeys(enabled_ids))
        writable_ids = [term_base_id for term_base_id in dict.fromkeys(writable_ids) if term_base_id in set(enabled_ids)]
        qa_ids = [term_base_id for term_base_id in dict.fromkeys(qa_ids) if term_base_id in set(enabled_ids)]
        enabled_set = set(enabled_ids)
        writable_set = set(writable_ids)
        qa_set = set(qa_ids)
        qa_priority_by_id = {
            term_base_id: index + 1
            for index, term_base_id in enumerate(qa_ids)
        }
        group_term_bases = [
            term_base
            for term_base in term_bases
            if term_base.source_language == source_language and term_base.target_language == target_language
        ]
        group_term_bases.sort(
            key=lambda term_base: (
                0 if term_base.id in qa_priority_by_id else 1,
                qa_priority_by_id.get(term_base.id, 9999),
                term_base.name.casefold(),
            )
        )
        groups.append({
            "source_language": source_language,
            "target_language": target_language,
            "file_count": len(group_files),
            "enabled_term_base_ids": [str(term_base_id) for term_base_id in enabled_ids],
            "writable_term_base_ids": [str(term_base_id) for term_base_id in writable_ids],
            "qa_term_base_ids": [str(term_base_id) for term_base_id in qa_ids],
            "term_bases": [
                {
                    "id": str(term_base.id),
                    "name": term_base.name,
                    "description": term_base.description,
                    "source_language": term_base.source_language,
                    "target_language": term_base.target_language,
                    "entry_count": entry_counts.get(term_base.id, 0),
                    "enabled": term_base.id in enabled_set,
                    "writable": term_base.id in writable_set,
                    "qa": term_base.id in qa_set,
                    "qa_priority": qa_priority_by_id.get(term_base.id),
                }
                for term_base in group_term_bases
            ],
        })

    return {
        "project_id": str(project.id),
        "groups": groups,
    }


def _serialize_project_translation_memory_settings(
    db: Session,
    project: Project,
    files: list[FileRecord],
) -> dict[str, Any]:
    pair_map = _project_file_language_pair_map(files)
    collections = db.query(MemoryBase).order_by(MemoryBase.name.asc(), MemoryBase.created_at.desc()).all()
    collection_ids = [collection.id for collection in collections]
    entry_counts: dict[UUID, int] = {}
    if collection_ids:
        entry_counts = {
            collection_id: int(entry_count or 0)
            for collection_id, entry_count in (
                db.query(MemoryEntry.collection_id, func.count(MemoryEntry.id))
                .filter(MemoryEntry.collection_id.in_(collection_ids))
                .group_by(MemoryEntry.collection_id)
                .all()
            )
        }

    groups: list[dict[str, Any]] = []
    for source_language, target_language in sorted(pair_map):
        group_files = pair_map[(source_language, target_language)]
        group_collections = [
            collection
            for collection in collections
            if collection.source_language == source_language and collection.target_language == target_language
        ]
        groups.append({
            "source_language": source_language,
            "target_language": target_language,
            "file_count": len(group_files),
            "collections": [
                {
                    "id": str(collection.id),
                    "name": collection.name,
                    "description": collection.description,
                    "source_language": collection.source_language,
                    "target_language": collection.target_language,
                    "entry_count": entry_counts.get(collection.id, 0),
                }
                for collection in group_collections
            ],
            "files": [
                {
                    "id": str(file_record.id),
                    "filename": file_record.filename,
                    "collection_id": str(file_record.collection_id) if file_record.collection_id else None,
                    "collection_ids": [
                        str(collection_id)
                        for collection_id in _load_file_record_collection_ids(file_record)
                    ],
                    "tm_match_threshold": _normalize_tm_match_threshold(
                        getattr(file_record, "tm_match_threshold", None),
                    ),
                }
                for file_record in group_files
            ],
        })

    return {
        "project_id": str(project.id),
        "auto_tm_enabled": getattr(project, "auto_tm_enabled", True) is not False,
        "groups": groups,
    }


def _normalize_tm_match_threshold(value: float | None) -> float:
    if value is None:
        value = get_settings().default_similarity_threshold
    threshold = round(float(value), 2)
    if threshold < 0.5 or threshold > 1:
        raise HTTPException(status_code=400, detail="TM 匹配阈值必须在 0.50 到 1.00 之间。")
    return threshold


def _validate_tm_setting_collection_ids(
    db: Session,
    ids: list[UUID],
    source_language: str,
    target_language: str,
) -> list[MemoryBase]:
    collections = _validate_collection_ids(db, ids) or []
    collection_rows = (
        db.query(MemoryBase)
        .filter(MemoryBase.id.in_(collections))
        .all()
        if collections
        else []
    )
    collection_by_id = {collection.id: collection for collection in collection_rows}
    for collection_id in collections:
        collection = collection_by_id.get(collection_id)
        if collection is None:
            raise HTTPException(status_code=404, detail="选择的记忆库不存在。")
        _ensure_resource_language_pair_matches(collection, source_language, target_language, "记忆库")
    return [collection_by_id[collection_id] for collection_id in collections]


def _validate_term_base_setting_ids(
    db: Session,
    ids: list[UUID],
    source_language: str,
    target_language: str,
) -> list[TermBase]:
    term_bases = _validate_term_base_ids(db, ids)
    for term_base in term_bases:
        _ensure_resource_language_pair_matches(term_base, source_language, target_language, "术语库")
    return term_bases


def _text_contains_case_insensitive(text: str | None, needle: str | None) -> bool:
    return text_contains_term(text, needle)


def _sort_term_qa_entries_for_matching(
    entries: list[TermEntry],
    priority_by_term_base_id: dict[UUID, int],
) -> list[TermEntry]:
    return sorted(
        entries,
        key=lambda entry: (
            -len((entry.source_text or "").strip()),
            priority_by_term_base_id.get(entry.term_base_id, 9999),
            str(entry.id),
        ),
    )


def _load_json_list(raw_value: str | None) -> list[Any]:
    try:
        value = json.loads(raw_value or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _serialize_term_qa_report_item(item: TermQAReportItem) -> dict[str, Any]:
    ignored_by_name = None
    if item.ignored_by_id:
        try:
            ignored_by = getattr(item, "ignored_by", None)
            ignored_by_name = get_user_display_name(ignored_by) if ignored_by else None
        except Exception:
            ignored_by_name = None
    return {
        "id": str(item.id),
        "report_id": str(item.report_id),
        "project_id": str(item.project_id) if item.project_id else None,
        "file_record_id": str(item.file_record_id),
        "segment_id": str(item.segment_id) if item.segment_id else None,
        "term_base_id": str(item.term_base_id) if item.term_base_id else None,
        "sentence_id": item.sentence_id,
        "file_name": item.file_name,
        "term_base_name": item.term_base_name,
        "source_term": item.source_term,
        "expected_target_term": item.expected_target_term,
        "source_text": item.source_text,
        "target_text": item.target_text,
        "block_index": item.block_index,
        "row_index": item.row_index,
        "cell_index": item.cell_index,
        "ignored": item.ignored_at is not None,
        "ignored_at": item.ignored_at.isoformat() if item.ignored_at else None,
        "ignored_by_id": str(item.ignored_by_id) if item.ignored_by_id else None,
        "ignored_by_name": ignored_by_name,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _serialize_term_qa_report(
    report: TermQAReport,
    items: list[TermQAReportItem] | None = None,
) -> dict[str, Any]:
    report_items = list(items if items is not None else report.items)
    ignored_count = sum(1 for item in report_items if item.ignored_at is not None)
    active_issue_count = max(int(report.issue_count or 0) - ignored_count, 0)
    return {
        "id": str(report.id),
        "project_id": str(report.project_id) if report.project_id else None,
        "file_record_id": str(report.file_record_id) if report.file_record_id else None,
        "created_by_id": str(report.created_by_id) if report.created_by_id else None,
        "scope": report.scope,
        "file_ids": [str(value) for value in _load_json_list(report.file_ids)],
        "term_base_ids": [str(value) for value in _load_json_list(report.term_base_ids)],
        "language_pairs": _load_json_list(report.language_pairs),
        "total_files": report.total_files,
        "total_segments": report.total_segments,
        "checked_segments": report.checked_segments,
        "issue_count": report.issue_count,
        "active_issue_count": active_issue_count,
        "ignored_count": ignored_count,
        "status": report.status,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "items": [_serialize_term_qa_report_item(item) for item in report_items],
    }


def _serialize_quality_qa_settings_response(db: Session, project: Project) -> dict[str, Any]:
    file_language_rows = (
        db.query(FileRecord.target_language, func.count(FileRecord.id))
        .filter(FileRecord.project_id == project.id)
        .group_by(FileRecord.target_language)
        .all()
    )
    target_languages = [
        {
            "language": row[0],
            "file_count": int(row[1] or 0),
            "supported": bool(get_languagetool_language(row[0])),
            "languagetool_code": get_languagetool_language(row[0]),
        }
        for row in file_language_rows
        if row[0]
    ]
    target_languages.sort(key=lambda item: item["language"])
    return {
        "project_id": str(project.id),
        "settings": load_quality_qa_settings(project),
        "languagetool_configured": is_languagetool_configured(),
        "supported_languages": get_supported_quality_qa_languages(),
        "target_languages": target_languages,
    }


def _create_term_qa_report(
    db: Session,
    *,
    project_id: UUID | None,
    files: list[FileRecord],
    current_user: User,
    scope: Literal["project", "file", "merge_view"],
) -> TermQAReport:
    if not files:
        raise HTTPException(status_code=400, detail="请选择要检查的文件。")

    file_ids = [file_record.id for file_record in files]
    file_by_id = {file_record.id: file_record for file_record in files}
    qa_ids_by_file_id = {
        file_record.id: _load_file_record_qa_term_base_ids(file_record)
        for file_record in files
    }
    configured_term_base_ids = list(dict.fromkeys(
        term_base_id
        for ids in qa_ids_by_file_id.values()
        for term_base_id in ids
    ))
    if not configured_term_base_ids:
        raise HTTPException(status_code=400, detail="未配置用于 QA 的术语库。")

    term_bases = (
        db.query(TermBase)
        .filter(TermBase.id.in_(configured_term_base_ids))
        .all()
    )
    term_base_by_id = {term_base.id: term_base for term_base in term_bases}
    segment_count_rows = (
        db.query(Segment.file_record_id, func.count(Segment.id))
        .filter(Segment.file_record_id.in_(file_ids))
        .group_by(Segment.file_record_id)
        .all()
    )
    segment_count_by_file_id = {
        file_record_id: int(count or 0)
        for file_record_id, count in segment_count_rows
    }
    total_segments = sum(segment_count_by_file_id.values())
    entries_cache: dict[tuple[str, str, tuple[UUID, ...]], list[TermEntry]] = {}

    language_pairs: list[dict[str, str]] = []
    language_pair_keys: set[tuple[str, str]] = set()
    report = TermQAReport(
        project_id=project_id,
        file_record_id=files[0].id if scope == "file" and len(files) == 1 else None,
        created_by_id=getattr(current_user, "id", None),
        scope=scope,
        file_ids=serialize_file_ids(file_ids),
        term_base_ids=json.dumps([str(term_base_id) for term_base_id in configured_term_base_ids]),
        language_pairs="[]",
        total_files=len(files),
        total_segments=total_segments,
        checked_segments=0,
        issue_count=0,
        status="completed",
    )
    db.add(report)
    db.flush()

    issue_count = 0
    checked_segments = 0
    for file_record in files:
        try:
            source_language, target_language = require_language_pair(
                file_record.source_language,
                file_record.target_language,
            )
        except ValueError:
            continue
        pair_key = (source_language, target_language)
        if pair_key not in language_pair_keys:
            language_pair_keys.add(pair_key)
            language_pairs.append({
                "source_language": source_language,
                "target_language": target_language,
            })
        qa_ids = [
            term_base_id
            for term_base_id in qa_ids_by_file_id.get(file_record.id, [])
            if term_base_id in term_base_by_id
        ]
        if not qa_ids:
            continue
        qa_priority_by_id = {
            term_base_id: index
            for index, term_base_id in enumerate(qa_ids)
        }
        entries_cache_key = (source_language, target_language, tuple(qa_ids))
        if entries_cache_key not in entries_cache:
            entries_cache[entries_cache_key] = [
                entry
                for entry in (
                    db.query(TermEntry)
                    .filter(
                        TermEntry.term_base_id.in_(qa_ids),
                        TermEntry.source_language == source_language,
                        TermEntry.target_language == target_language,
                    )
                    .all()
                )
                if (entry.source_text or "").strip() and (entry.target_text or "").strip()
            ]
        applicable_entries = _sort_term_qa_entries_for_matching(
            entries_cache[entries_cache_key],
            qa_priority_by_id,
        )
        if not applicable_entries:
            continue
        file_segments = (
            db.query(Segment)
            .filter(Segment.file_record_id == file_record.id)
            .order_by(*get_segment_ordering_for_file_record(file_record))
            .all()
        )
        checked_segments += len(file_segments)
        for segment in file_segments:
            source_text = segment.source_text or ""
            target_text = segment.target_text or ""
            source_matches = find_non_overlapping_term_text_matches(
                source_text,
                applicable_entries,
                lambda entry: entry.source_text,
            )
            reported_entry_ids: set[UUID] = set()
            for source_match in source_matches:
                entry = source_match.item
                if entry.id in reported_entry_ids:
                    continue
                reported_entry_ids.add(entry.id)
                source_term = (entry.source_text or "").strip()
                expected_target_term = (entry.target_text or "").strip()
                if _text_contains_case_insensitive(target_text, expected_target_term):
                    continue
                term_base = term_base_by_id.get(entry.term_base_id)
                db.add(TermQAReportItem(
                    report_id=report.id,
                    project_id=project_id,
                    file_record_id=file_record.id,
                    segment_id=segment.id,
                    term_base_id=entry.term_base_id,
                    sentence_id=segment.sentence_id,
                    file_name=file_record.filename,
                    term_base_name=term_base.name if term_base else "",
                    source_term=source_term,
                    expected_target_term=expected_target_term,
                    source_text=source_text,
                    target_text=target_text,
                    block_index=int(segment.block_index or 0),
                    row_index=segment.row_index,
                    cell_index=segment.cell_index,
                ))
                issue_count += 1
                if issue_count % 1000 == 0:
                    db.flush()

    report.language_pairs = json.dumps(language_pairs)
    report.checked_segments = checked_segments
    report.issue_count = issue_count
    db.commit()
    db.refresh(report)
    return report


def _get_term_qa_report_or_404(db: Session, report_id: UUID) -> TermQAReport:
    report = db.query(TermQAReport).filter(TermQAReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="术语QA报告不存在。")
    return report


def _require_term_qa_report_read_access(
    report: TermQAReport,
    current_user: User,
    db: Session,
) -> None:
    if report.project_id:
        project = db.query(Project).filter(Project.id == report.project_id).first()
        if project:
            _require_project_read_access(project, current_user, db)
            return
    if report.file_record_id:
        file_record = get_file_record_model(db, report.file_record_id)
        if file_record:
            _require_file_record_read_access(file_record, current_user)
            return
    if not can_access_all_projects(current_user):
        raise HTTPException(status_code=403, detail="无权访问该术语QA报告。")


def _get_term_qa_report_item_or_404(db: Session, item_id: UUID) -> TermQAReportItem:
    item = db.query(TermQAReportItem).filter(TermQAReportItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="术语QA报告项不存在。")
    return item


def _load_term_qa_report_items_for_response(db: Session, report_id: UUID) -> list[TermQAReportItem]:
    return (
        db.query(TermQAReportItem)
        .filter(TermQAReportItem.report_id == report_id)
        .order_by(TermQAReportItem.file_name.asc(), TermQAReportItem.block_index.asc(), TermQAReportItem.sentence_id.asc())
        .all()
    )


def _load_term_qa_report_items_for_files(
    db: Session,
    report_id: UUID,
    file_ids: list[UUID],
) -> list[TermQAReportItem]:
    """按给定文件顺序返回报告项，供合并视图保持视图内文件排序。"""
    items = (
        db.query(TermQAReportItem)
        .filter(TermQAReportItem.report_id == report_id)
        .all()
    )
    file_order = {file_id: index for index, file_id in enumerate(file_ids)}
    return sorted(
        items,
        key=lambda item: (
            file_order.get(item.file_record_id, len(file_order)),
            int(item.block_index or 0),
            item.row_index if item.row_index is not None else -1,
            item.cell_index if item.cell_index is not None else -1,
            item.sentence_id,
        ),
    )


def _require_term_qa_item_write_access(
    db: Session,
    item: TermQAReportItem,
    current_user: User,
) -> None:
    file_record = get_file_record_model(db, item.file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="报告项对应文件不存在。")
    _require_file_record_work_access(file_record, current_user)


def _apply_term_qa_ignore_state(
    items: list[TermQAReportItem],
    current_user: User,
    ignored: bool,
) -> None:
    now = datetime.now()
    for item in items:
        if ignored:
            item.ignored_at = item.ignored_at or now
            item.ignored_by_id = current_user.id
        else:
            item.ignored_at = None
            item.ignored_by_id = None


WORKBENCH_QA_ITEM_PREFIX_SEGMENT = "segment_qa_issue:"
WORKBENCH_QA_ITEM_PREFIX_TERM = "term_qa_report_item:"
WORKBENCH_QA_SUPPORTED_RULES = (QA_RULE_SPELLING_GRAMMAR, QA_RULE_TERM_INCONSISTENCY)
WORKBENCH_QA_RULE_LABELS = {
    QA_RULE_SPELLING_GRAMMAR: "拼写/语法",
    QA_RULE_TERM_INCONSISTENCY: "术语不一致",
}


def _is_workbench_qa_rule_enabled(settings: dict[str, Any], rule_key: str) -> bool:
    rule = (settings.get("rules") or {}).get(rule_key)
    if isinstance(rule, dict):
        return bool(rule.get("enabled"))
    if rule_key == QA_RULE_SPELLING_GRAMMAR:
        spelling_grammar = settings.get(QA_RULE_SPELLING_GRAMMAR)
        if isinstance(spelling_grammar, dict):
            return bool(spelling_grammar.get("enabled"))
    return False


def _get_file_record_project_or_400(db: Session, file_record: FileRecord) -> Project:
    project = file_record.project or (
        db.query(Project).filter(Project.id == file_record.project_id).first()
        if file_record.project_id
        else None
    )
    if not project:
        raise HTTPException(status_code=400, detail="当前文件未归属项目，无法使用项目 QA 设置。")
    return project


def _load_latest_term_qa_report_for_file(db: Session, file_record_id: UUID) -> TermQAReport | None:
    return (
        db.query(TermQAReport)
        .filter(TermQAReport.file_record_id == file_record_id)
        .order_by(TermQAReport.created_at.desc(), TermQAReport.id.desc())
        .first()
    )


def _load_latest_term_qa_report_for_merge_view(
    db: Session,
    project_id: UUID,
    file_ids: list[UUID],
) -> TermQAReport | None:
    return (
        db.query(TermQAReport)
        .filter(
            TermQAReport.project_id == project_id,
            TermQAReport.scope == "merge_view",
            TermQAReport.file_ids == serialize_file_ids(file_ids),
        )
        .order_by(TermQAReport.created_at.desc(), TermQAReport.id.desc())
        .first()
    )


def _load_workbench_segment_qa_issue_items(
    db: Session,
    *,
    files: list[FileRecord],
    file_order: dict[UUID, int],
) -> list[dict[str, Any]]:
    file_ids = [file_record.id for file_record in files]
    if not file_ids:
        return []
    file_by_id = {file_record.id: file_record for file_record in files}
    issues = (
        db.query(SegmentQAIssue)
        .filter(
            SegmentQAIssue.file_record_id.in_(file_ids),
            SegmentQAIssue.rule_key == QA_RULE_SPELLING_GRAMMAR,
            SegmentQAIssue.status.in_([QA_ISSUE_STATUS_OPEN, QA_ISSUE_STATUS_IGNORED]),
        )
        .all()
    )
    if not issues:
        return []

    segment_by_id = {
        segment.id: segment
        for segment in (
            db.query(Segment)
            .filter(Segment.id.in_([issue.segment_id for issue in issues]))
            .all()
        )
    }
    items: list[dict[str, Any]] = []
    for issue in issues:
        segment = segment_by_id.get(issue.segment_id)
        file_record = file_by_id.get(issue.file_record_id)
        serialized = serialize_segment_qa_issue(issue)
        replacements = serialized.get("replacements") or []
        suggestion = "；".join(str(value) for value in replacements[:5])
        ignored_by_name = None
        if issue.ignored_by_id:
            ignored_by = getattr(issue, "ignored_by", None)
            ignored_by_name = get_user_display_name(ignored_by) if ignored_by else None
        items.append({
            "id": f"{WORKBENCH_QA_ITEM_PREFIX_SEGMENT}{issue.id}",
            "source_id": str(issue.id),
            "source_kind": "segment_qa_issue",
            "rule_key": QA_RULE_SPELLING_GRAMMAR,
            "rule_label": WORKBENCH_QA_RULE_LABELS[QA_RULE_SPELLING_GRAMMAR],
            "project_id": str(issue.project_id) if issue.project_id else None,
            "file_record_id": str(issue.file_record_id),
            "file_name": file_record.filename if file_record else "",
            "segment_id": str(issue.segment_id),
            "sentence_id": issue.sentence_id,
            "source_text": segment.source_text if segment else "",
            "target_text": segment.target_text if segment else "",
            "message": issue.short_message or issue.message or "译文有拼写或语法错误",
            "detail": issue.message,
            "suggestion": suggestion,
            "source_term": "",
            "expected_target_term": "",
            "term_base_name": "",
            "severity": issue.severity,
            "status": issue.status,
            "ignored": issue.status == QA_ISSUE_STATUS_IGNORED,
            "ignored_at": issue.ignored_at.isoformat() if issue.ignored_at else None,
            "ignored_by_id": str(issue.ignored_by_id) if issue.ignored_by_id else None,
            "ignored_by_name": ignored_by_name,
            "block_index": int(segment.block_index or 0) if segment else 0,
            "row_index": segment.row_index if segment else None,
            "cell_index": segment.cell_index if segment else None,
            "created_at": issue.created_at.isoformat() if issue.created_at else None,
            "_sort": (
                file_order.get(issue.file_record_id, len(file_order)),
                int(segment.block_index or 0) if segment else 0,
                segment.row_index if segment and segment.row_index is not None else -1,
                segment.cell_index if segment and segment.cell_index is not None else -1,
                issue.sentence_id,
                0,
            ),
        })
    return items


def _serialize_workbench_term_qa_item(
    item: TermQAReportItem,
    *,
    file_order: dict[UUID, int],
) -> dict[str, Any]:
    ignored_by_name = None
    if item.ignored_by_id:
        ignored_by = getattr(item, "ignored_by", None)
        ignored_by_name = get_user_display_name(ignored_by) if ignored_by else None
    message = f"术语“{item.source_term}”应译为“{item.expected_target_term}”。"
    return {
        "id": f"{WORKBENCH_QA_ITEM_PREFIX_TERM}{item.id}",
        "source_id": str(item.id),
        "source_kind": "term_qa_report_item",
        "rule_key": QA_RULE_TERM_INCONSISTENCY,
        "rule_label": WORKBENCH_QA_RULE_LABELS[QA_RULE_TERM_INCONSISTENCY],
        "project_id": str(item.project_id) if item.project_id else None,
        "file_record_id": str(item.file_record_id),
        "file_name": item.file_name,
        "segment_id": str(item.segment_id) if item.segment_id else None,
        "sentence_id": item.sentence_id,
        "source_text": item.source_text,
        "target_text": item.target_text,
        "message": message,
        "detail": message,
        "suggestion": item.expected_target_term,
        "source_term": item.source_term,
        "expected_target_term": item.expected_target_term,
        "term_base_name": item.term_base_name,
        "severity": "medium",
        "status": "ignored" if item.ignored_at else "open",
        "ignored": item.ignored_at is not None,
        "ignored_at": item.ignored_at.isoformat() if item.ignored_at else None,
        "ignored_by_id": str(item.ignored_by_id) if item.ignored_by_id else None,
        "ignored_by_name": ignored_by_name,
        "block_index": int(item.block_index or 0),
        "row_index": item.row_index,
        "cell_index": item.cell_index,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "_sort": (
            file_order.get(item.file_record_id, len(file_order)),
            int(item.block_index or 0),
            item.row_index if item.row_index is not None else -1,
            item.cell_index if item.cell_index is not None else -1,
            item.sentence_id,
            1,
        ),
    }


def _load_workbench_term_qa_items(
    db: Session,
    *,
    report: TermQAReport | None,
    files: list[FileRecord],
    file_order: dict[UUID, int],
) -> list[dict[str, Any]]:
    if report is None:
        return []
    file_ids = [file_record.id for file_record in files]
    if report.scope == "merge_view":
        items = _load_term_qa_report_items_for_files(db, report.id, file_ids)
    else:
        items = _load_term_qa_report_items_for_response(db, report.id)
    return [
        _serialize_workbench_term_qa_item(item, file_order=file_order)
        for item in items
        if item.file_record_id in set(file_ids)
    ]


def _run_spelling_grammar_for_workbench_files(
    db: Session,
    *,
    files: list[FileRecord],
    warnings: list[str],
) -> None:
    if not is_languagetool_configured():
        warnings.append("LanguageTool 未配置，已跳过拼写/语法 QA。")
        return

    for file_record in files:
        if not get_languagetool_language(file_record.target_language):
            warnings.append(f"{file_record.filename} 的目标语言暂不支持拼写/语法 QA。")
            continue
        segments = (
            db.query(Segment)
            .filter(
                Segment.file_record_id == file_record.id,
                func.trim(func.coalesce(Segment.target_text, "")) != "",
            )
            .all()
        )
        check_segments_with_languagetool(db, file_record=file_record, segments=segments)


def _maybe_create_workbench_term_qa_report(
    db: Session,
    *,
    project_id: UUID,
    files: list[FileRecord],
    current_user: User,
    scope: Literal["file", "merge_view"],
    warnings: list[str],
) -> TermQAReport | None:
    try:
        return _create_term_qa_report(
            db,
            project_id=project_id,
            files=files,
            current_user=current_user,
            scope=scope,
        )
    except HTTPException as exc:
        if exc.status_code == 400 and str(exc.detail) == "未配置用于 QA 的术语库。":
            warnings.append("未配置用于 QA 的术语库，已跳过术语不一致检查。")
            return None
        raise


def _build_workbench_qa_result(
    db: Session,
    *,
    project: Project,
    files: list[FileRecord],
    current_user: User,
    scope: Literal["file", "merge_view"],
    generate: bool,
) -> dict[str, Any]:
    file_ids = [file_record.id for file_record in files]
    file_order = {file_id: index for index, file_id in enumerate(file_ids)}
    settings = load_quality_qa_settings(project)
    enabled_rules = {
        rule_key: _is_workbench_qa_rule_enabled(settings, rule_key)
        for rule_key in WORKBENCH_QA_SUPPORTED_RULES
    }
    warnings: list[str] = []
    term_report: TermQAReport | None = None

    if not any(enabled_rules.values()):
        warnings.append("当前项目未启用已支持的 QA 规则。")

    if enabled_rules[QA_RULE_SPELLING_GRAMMAR] and generate:
        _run_spelling_grammar_for_workbench_files(db, files=files, warnings=warnings)

    if enabled_rules[QA_RULE_TERM_INCONSISTENCY]:
        if generate:
            term_report = _maybe_create_workbench_term_qa_report(
                db,
                project_id=project.id,
                files=files,
                current_user=current_user,
                scope=scope,
                warnings=warnings,
            )
        elif scope == "file" and len(files) == 1:
            term_report = _load_latest_term_qa_report_for_file(db, files[0].id)
        else:
            term_report = _load_latest_term_qa_report_for_merge_view(db, project.id, file_ids)

    total_segments = (
        db.query(func.count(Segment.id))
        .filter(Segment.file_record_id.in_(file_ids))
        .scalar()
        or 0
    )
    items: list[dict[str, Any]] = []
    if enabled_rules[QA_RULE_SPELLING_GRAMMAR]:
        items.extend(_load_workbench_segment_qa_issue_items(db, files=files, file_order=file_order))
    if enabled_rules[QA_RULE_TERM_INCONSISTENCY]:
        items.extend(_load_workbench_term_qa_items(db, report=term_report, files=files, file_order=file_order))

    items.sort(key=lambda item: item.get("_sort", ()))
    for item in items:
        item.pop("_sort", None)

    ignored_count = sum(1 for item in items if item.get("ignored"))
    active_issue_count = len(items) - ignored_count
    created_at = term_report.created_at.isoformat() if term_report and term_report.created_at else datetime.now().isoformat()
    return {
        "id": f"{scope}:{project.id}:{','.join(str(file_id) for file_id in file_ids)}",
        "project_id": str(project.id),
        "file_record_id": str(files[0].id) if scope == "file" and len(files) == 1 else None,
        "scope": scope,
        "file_ids": [str(file_id) for file_id in file_ids],
        "term_report_id": str(term_report.id) if term_report else None,
        "total_files": len(files),
        "total_segments": int(total_segments),
        "checked_segments": int(total_segments) if any(enabled_rules.values()) else 0,
        "issue_count": len(items),
        "active_issue_count": active_issue_count,
        "ignored_count": ignored_count,
        "created_at": created_at,
        "rules": [
            {
                "key": rule_key,
                "label": WORKBENCH_QA_RULE_LABELS[rule_key],
                "enabled": enabled_rules[rule_key],
                "supported": True,
            }
            for rule_key in WORKBENCH_QA_SUPPORTED_RULES
        ],
        "warnings": list(dict.fromkeys(warnings)),
        "items": items,
    }


def _parse_workbench_qa_item_ids(item_ids: list[str]) -> tuple[list[UUID], list[UUID]]:
    segment_issue_ids: list[UUID] = []
    term_item_ids: list[UUID] = []
    for raw_id in dict.fromkeys(item_ids):
        value = str(raw_id or "").strip()
        if value.startswith(WORKBENCH_QA_ITEM_PREFIX_SEGMENT):
            try:
                segment_issue_ids.append(UUID(value[len(WORKBENCH_QA_ITEM_PREFIX_SEGMENT):]))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="QA 问题 ID 无效。") from exc
            continue
        if value.startswith(WORKBENCH_QA_ITEM_PREFIX_TERM):
            try:
                term_item_ids.append(UUID(value[len(WORKBENCH_QA_ITEM_PREFIX_TERM):]))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="QA 问题 ID 无效。") from exc
            continue
        raise HTTPException(status_code=400, detail="QA 问题 ID 类型无效。")
    return segment_issue_ids, term_item_ids


def _require_segment_qa_issue_write_access(db: Session, issue: SegmentQAIssue, current_user: User) -> None:
    file_record = issue.file_record or db.query(FileRecord).filter(FileRecord.id == issue.file_record_id).first()
    if not file_record:
        raise HTTPException(status_code=404, detail="QA 问题对应文件不存在。")
    _require_file_record_work_access(file_record, current_user)


def _apply_segment_qa_ignore_state(
    issues: list[SegmentQAIssue],
    current_user: User,
    ignored: bool,
) -> None:
    now = datetime.now()
    for issue in issues:
        if ignored:
            issue.status = QA_ISSUE_STATUS_IGNORED
            issue.ignored_by_id = current_user.id
            issue.ignored_at = issue.ignored_at or now
        else:
            issue.status = QA_ISSUE_STATUS_OPEN
            issue.ignored_by_id = None
            issue.ignored_at = None
        issue.updated_at = now
        if issue.segment:
            issue.segment.updated_at = now


def _build_workbench_qa_xlsx_response(result: dict[str, Any], filename: str):
    rows = [
        [
            item.get("file_name") or "",
            item.get("sentence_id") or "",
            item.get("rule_label") or item.get("rule_key") or "",
            item.get("message") or "",
            item.get("suggestion") or "",
            item.get("source_term") or "",
            item.get("expected_target_term") or "",
            item.get("source_text") or "",
            item.get("target_text") or "",
            "已忽略" if item.get("ignored") else "待处理",
            item.get("ignored_at") or "",
            item.get("ignored_by_name") or item.get("ignored_by_id") or "",
            item.get("block_index") if item.get("block_index") is not None else "",
            item.get("row_index") if item.get("row_index") is not None else "",
            item.get("cell_index") if item.get("cell_index") is not None else "",
        ]
        for item in result.get("items", [])
    ]
    xlsx_bytes = build_tabular_xlsx(
        sheet_title="QA结果",
        headers=[
            "文件名",
            "句段ID",
            "错误类型",
            "问题描述",
            "建议/期望译文",
            "原文术语",
            "期望术语译文",
            "原文",
            "译文",
            "处理状态",
            "忽略时间",
            "忽略人",
            "块序号",
            "行序号",
            "单元格序号",
        ],
        rows=rows,
    )
    return build_xlsx_download_response(filename, xlsx_bytes)


def _get_project_sync_segment_stats(db: Session | None, project_id: UUID) -> tuple[int, int]:
    if db is None:
        return 0, 0

    total_count, disabled_count = (
        db.query(
            func.count(Segment.id),
            func.coalesce(
                func.sum(case((Segment.project_sync_disabled.is_(True), 1), else_=0)),
                0,
            ),
        )
        .join(FileRecord, Segment.file_record_id == FileRecord.id)
        .filter(FileRecord.project_id == project_id)
        .one()
    )
    return int(total_count or 0), int(disabled_count or 0)


def _build_project_detail_payload(
    db: Session | Project,
    project: Project | list[FileRecord],
    files: list[FileRecord] | dict[UUID, dict],
    file_stats: dict[UUID, dict] | None = None,
    issue_markers: list[IssueMarker] | None = None,
    project_issue_stats: dict[str, int] | None = None,
    file_issue_stats: dict[UUID, dict[str, int]] | None = None,
    current_user: User | None = None,
    assigned_users: list[User] | None = None,
    file_assignees: dict[UUID, list[User]] | None = None,
) -> dict:
    if file_stats is None:
        project_obj = db
        files_list = project
        stats = files
        db = None
        project = project_obj
        files = files_list
        file_stats = stats

    total_segments = sum(file_stats.get(file.id, {"total": 0})["total"] for file in files)
    translated_segments = sum(file_stats.get(file.id, {"filled": 0})["filled"] for file in files)
    pretranslated_segments = sum(
        file_stats.get(file.id, {}).get("pretranslated", 0) for file in files
    )
    workflow_steps = _load_project_workflow_steps(db, project.id) if db is not None else []
    workflow_progress = _get_project_workflow_progress(db, [project.id]).get(project.id, []) if db is not None else []
    file_workflow_progress = _get_file_workflow_progress(db, [file.id for file in files]) if db is not None else {}
    file_issue_stats = file_issue_stats or {}
    payload = _build_project_summary_payload(
        project=project,
        total_segments=total_segments,
        translated_segments=translated_segments,
        pretranslated_segments=pretranslated_segments,
        file_count=len(files),
        creator_name=get_user_display_name(project.creator),
        issue_stats=project_issue_stats,
        current_user=current_user,
        assigned_users=assigned_users,
        workflow_steps=workflow_steps,
        workflow_progress=workflow_progress,
    )
    file_assignees = file_assignees or {}
    payload["files"] = [
        _build_project_file_payload(
            file_record=file_record,
            total_segments=file_stats.get(file_record.id, {"total": 0})["total"],
            translated_segments=file_stats.get(file_record.id, {"filled": 0})["filled"],
            pretranslated_segments=file_stats.get(file_record.id, {}).get("pretranslated", 0),
            issue_stats=file_issue_stats.get(file_record.id),
            current_user=current_user,
            assignees=file_assignees.get(file_record.id),
            workflow_steps=workflow_steps,
            workflow_progress=file_workflow_progress.get(file_record.id, []),
        )
        for file_record in files
    ]
    project_sync_segment_count, project_sync_disabled_count = _get_project_sync_segment_stats(db, project.id)
    payload["project_sync_segment_count"] = project_sync_segment_count
    payload["project_sync_disabled_count"] = project_sync_disabled_count
    payload["issue_markers"] = [
        serialize_issue_marker(marker)
        for marker in (issue_markers or [])
    ]
    payload["has_source_document"] = any(file_item["has_source_document"] for file_item in payload["files"])
    payload["file_size_bytes"] = sum(
        file_item["file_size_bytes"] or 0
        for file_item in payload["files"]
    ) or None
    return payload


@router.get("/projects/{project_id}/assignments")
def get_project_assignments(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    _get_project_or_404(db, project_id)
    return _serialize_project_assignments(db, project_id)


@router.patch("/projects/{project_id}/assignments")
def update_project_assignments(
    project_id: UUID,
    payload: ProjectAssignmentsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    project = _get_project_or_404(db, project_id)
    return _update_project_assignments_by_workflow(
        db,
        project_id=project_id,
        project=project,
        payload=payload,
        current_user=current_user,
    )
    desired = _validate_assignment_payload(db, project, payload)
    now = datetime.now()

    current_project_assignments = {
        assignment.assignee_id: assignment
        for assignment in (
            db.query(ProjectAssignment)
            .filter(
                ProjectAssignment.project_id == project_id,
                ProjectAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
            )
            .all()
        )
    }
    current_file_assignments: dict[UUID, dict[UUID, FileAssignment]] = {}
    for assignment in (
        db.query(FileAssignment)
        .filter(
            FileAssignment.project_id == project_id,
            FileAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
        )
        .all()
    ):
        current_file_assignments.setdefault(assignment.assignee_id, {})[assignment.file_record_id] = assignment

    for assignee_id, assignment in list(current_project_assignments.items()):
        if assignee_id in desired:
            continue
        assignment.status = ASSIGNMENT_STATUS_REVOKED
        assignment.revoked_by_id = current_user.id
        assignment.revoked_at = now
        event = _record_assignment_event(
            db,
            project_id=project_id,
            assignee_id=assignee_id,
            actor_id=current_user.id,
            action=ASSIGNMENT_EVENT_PROJECT_UNASSIGNED,
            before_payload={"project_id": str(project_id), "status": ASSIGNMENT_STATUS_ACTIVE},
            after_payload={"project_id": str(project_id), "status": ASSIGNMENT_STATUS_REVOKED},
        )
        _create_assignment_notification(
            db,
            user_id=assignee_id,
            notification_type=ASSIGNMENT_EVENT_PROJECT_UNASSIGNED,
            title="项目指派已取消",
            body=f"你已不再负责项目「{project.name}」。",
            project_id=project_id,
            related_event_id=event.id,
        )
        for file_assignment in current_file_assignments.get(assignee_id, {}).values():
            file_assignment.status = ASSIGNMENT_STATUS_REVOKED
            file_assignment.revoked_by_id = current_user.id
            file_assignment.revoked_at = now
            _record_assignment_event(
                db,
                project_id=project_id,
                file_record_id=file_assignment.file_record_id,
                assignee_id=assignee_id,
                actor_id=current_user.id,
                action=ASSIGNMENT_EVENT_FILE_REVOKED,
                before_payload={"file_record_id": str(file_assignment.file_record_id), "status": ASSIGNMENT_STATUS_ACTIVE},
                after_payload={"file_record_id": str(file_assignment.file_record_id), "status": ASSIGNMENT_STATUS_REVOKED},
            )

    for assignee_id, desired_file_ids in desired.items():
        project_assignment = current_project_assignments.get(assignee_id)
        if project_assignment is None:
            project_assignment = ProjectAssignment(
                project_id=project_id,
                assignee_id=assignee_id,
                assigned_by_id=current_user.id,
                assigned_at=now,
                status=ASSIGNMENT_STATUS_ACTIVE,
            )
            db.add(project_assignment)
            event = _record_assignment_event(
                db,
                project_id=project_id,
                assignee_id=assignee_id,
                actor_id=current_user.id,
                action=ASSIGNMENT_EVENT_PROJECT_ASSIGNED,
                before_payload={"project_id": str(project_id), "status": None},
                after_payload={"project_id": str(project_id), "status": ASSIGNMENT_STATUS_ACTIVE},
            )
            _create_assignment_notification(
                db,
                user_id=assignee_id,
                notification_type=ASSIGNMENT_EVENT_PROJECT_ASSIGNED,
                title="你收到了新的项目指派",
                body=f"项目「{project.name}」已分配给你。",
                project_id=project_id,
                related_event_id=event.id,
            )

        active_file_assignments = current_file_assignments.get(assignee_id, {})
        active_file_ids = set(active_file_assignments)
        for file_record_id in sorted(desired_file_ids - active_file_ids, key=str):
            file_assignment = FileAssignment(
                project_id=project_id,
                file_record_id=file_record_id,
                assignee_id=assignee_id,
                assigned_by_id=current_user.id,
                assigned_at=now,
                status=ASSIGNMENT_STATUS_ACTIVE,
            )
            db.add(file_assignment)
            event = _record_assignment_event(
                db,
                project_id=project_id,
                file_record_id=file_record_id,
                assignee_id=assignee_id,
                actor_id=current_user.id,
                action=ASSIGNMENT_EVENT_FILE_GRANTED,
                before_payload={"file_record_id": str(file_record_id), "status": None},
                after_payload={"file_record_id": str(file_record_id), "status": ASSIGNMENT_STATUS_ACTIVE},
            )
            _create_assignment_notification(
                db,
                user_id=assignee_id,
                notification_type=ASSIGNMENT_EVENT_FILE_GRANTED,
                title="你收到了新的文件任务",
                body=f"项目「{project.name}」中有新的文件授权给你处理。",
                project_id=project_id,
                file_record_id=file_record_id,
                related_event_id=event.id,
            )

        for file_record_id in sorted(active_file_ids - desired_file_ids, key=str):
            file_assignment = active_file_assignments[file_record_id]
            file_assignment.status = ASSIGNMENT_STATUS_REVOKED
            file_assignment.revoked_by_id = current_user.id
            file_assignment.revoked_at = now
            event = _record_assignment_event(
                db,
                project_id=project_id,
                file_record_id=file_record_id,
                assignee_id=assignee_id,
                actor_id=current_user.id,
                action=ASSIGNMENT_EVENT_FILE_REVOKED,
                before_payload={"file_record_id": str(file_record_id), "status": ASSIGNMENT_STATUS_ACTIVE},
                after_payload={"file_record_id": str(file_record_id), "status": ASSIGNMENT_STATUS_REVOKED},
            )
            _create_assignment_notification(
                db,
                user_id=assignee_id,
                notification_type=ASSIGNMENT_EVENT_FILE_REVOKED,
                title="文件任务授权已取消",
                body=f"项目「{project.name}」中有文件不再授权给你处理。",
                project_id=project_id,
                file_record_id=file_record_id,
                related_event_id=event.id,
            )

    _sync_legacy_file_assignees(db, project_id)
    db.commit()
    return _serialize_project_assignments(db, project_id)


@router.get("/projects/{project_id}/term-base-settings")
def get_project_term_base_settings(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    project = _get_project_or_404(db, project_id)
    files = (
        db.query(FileRecord)
        .filter(FileRecord.project_id == project_id)
        .order_by(FileRecord.created_at.asc(), FileRecord.id.asc())
        .all()
    )
    return _serialize_project_term_base_settings(db, project, files)


@router.patch("/projects/{project_id}/term-base-settings")
def update_project_term_base_settings(
    project_id: UUID,
    payload: ProjectTermBaseSettingsRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    project = _get_project_or_404(db, project_id)
    files = (
        db.query(FileRecord)
        .filter(FileRecord.project_id == project_id)
        .order_by(FileRecord.created_at.asc(), FileRecord.id.asc())
        .all()
    )
    pair_map = _project_file_language_pair_map(files)

    for item in payload.settings:
        source_language, target_language = _require_tm_language_pair(
            item.source_language,
            item.target_language,
        )
        group_files = pair_map.get((source_language, target_language))
        if not group_files:
            raise HTTPException(status_code=400, detail="项目中不存在该语言对的文件。")

        enabled_ids = list(dict.fromkeys(item.enabled_term_base_ids))
        writable_ids = list(dict.fromkeys(item.writable_term_base_ids))
        qa_ids = list(dict.fromkeys(item.qa_term_base_ids))
        enabled_set = set(enabled_ids)
        if not set(writable_ids).issubset(enabled_set):
            raise HTTPException(status_code=400, detail="写入术语库必须先启用。")
        if not set(qa_ids).issubset(enabled_set):
            raise HTTPException(status_code=400, detail="用于 QA 的术语库必须先启用。")

        _validate_term_base_setting_ids(db, enabled_ids, source_language, target_language)
        _validate_term_base_setting_ids(db, writable_ids, source_language, target_language)
        _validate_term_base_setting_ids(db, qa_ids, source_language, target_language)

        for file_record in group_files:
            _store_file_record_term_base_ids(file_record, enabled_ids)
            _store_file_record_term_base_write_ids(file_record, writable_ids)
            _store_file_record_qa_term_base_ids(file_record, qa_ids)

    db.commit()
    files = (
        db.query(FileRecord)
        .filter(FileRecord.project_id == project_id)
        .order_by(FileRecord.created_at.asc(), FileRecord.id.asc())
        .all()
    )
    return _serialize_project_term_base_settings(db, project, files)


@router.get("/projects/{project_id}/translation-memory-settings")
def get_project_translation_memory_settings(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    project = _get_project_or_404(db, project_id)
    files = (
        db.query(FileRecord)
        .filter(FileRecord.project_id == project_id)
        .order_by(FileRecord.created_at.asc(), FileRecord.id.asc())
        .all()
    )
    return _serialize_project_translation_memory_settings(db, project, files)


@router.patch("/projects/{project_id}/translation-memory-settings")
def update_project_translation_memory_settings(
    project_id: UUID,
    payload: ProjectTranslationMemorySettingsRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    project = _get_project_or_404(db, project_id)
    if "auto_tm_enabled" in payload.model_fields_set and payload.auto_tm_enabled is not None:
        project.auto_tm_enabled = bool(payload.auto_tm_enabled)

    files = (
        db.query(FileRecord)
        .filter(FileRecord.project_id == project_id)
        .order_by(FileRecord.created_at.asc(), FileRecord.id.asc())
        .all()
    )
    pair_map = _project_file_language_pair_map(files)
    files_by_id = {file_record.id: file_record for file_record in files}
    changed_files: list[FileRecord] = []

    for item in payload.settings:
        source_language, target_language = _require_tm_language_pair(
            item.source_language,
            item.target_language,
        )
        group_files = pair_map.get((source_language, target_language))
        if not group_files:
            raise HTTPException(status_code=400, detail="项目中不存在该语言对的文件。")

        if item.files:
            file_payloads = item.files
        else:
            file_payloads = [
                ProjectTranslationMemoryFileSettingPayload(
                    file_record_id=file_record.id,
                    collection_ids=item.collection_ids,
                    primary_collection_id=item.primary_collection_id,
                    tm_match_threshold=item.tm_match_threshold,
                )
                for file_record in group_files
            ]

        group_file_ids = {file_record.id for file_record in group_files}
        for file_payload in file_payloads:
            file_record = files_by_id.get(file_payload.file_record_id)
            if file_record is None or file_record.id not in group_file_ids:
                raise HTTPException(status_code=404, detail="文件不存在或不属于当前项目语言对。")

            selected_ids = list(dict.fromkeys(file_payload.collection_ids))
            _validate_tm_setting_collection_ids(db, selected_ids, source_language, target_language)
            primary_collection_id = file_payload.primary_collection_id
            if primary_collection_id is not None and primary_collection_id not in selected_ids:
                raise HTTPException(status_code=400, detail="主写入记忆库必须包含在绑定记忆库列表中。")
            if primary_collection_id is None and selected_ids:
                primary_collection_id = selected_ids[0]
            next_threshold = _normalize_tm_match_threshold(
                file_payload.tm_match_threshold
                if file_payload.tm_match_threshold is not None
                else getattr(file_record, "tm_match_threshold", None),
            )

            before = (
                tuple(_load_file_record_collection_ids(file_record)),
                file_record.collection_id,
                _normalize_tm_match_threshold(getattr(file_record, "tm_match_threshold", None)),
            )
            _store_file_record_collection_ids(file_record, selected_ids)
            file_record.collection_id = primary_collection_id
            file_record.tm_match_threshold = next_threshold
            after = (
                tuple(_load_file_record_collection_ids(file_record)),
                file_record.collection_id,
                _normalize_tm_match_threshold(getattr(file_record, "tm_match_threshold", None)),
            )
            if before != after:
                changed_files.append(file_record)

    db.flush()
    initial_match_queued_count = 0
    for file_record in list(dict.fromkeys(changed_files)):
        initial_match_queued_count += register_file_rematch_work(
            db,
            file_record_id=file_record.id,
            collection_ids=_load_file_record_collection_ids(file_record),
        )

    db.commit()
    if initial_match_queued_count > 0:
        background_tasks.add_task(_dispatch_auto_tm_rematch_background)
    files = (
        db.query(FileRecord)
        .filter(FileRecord.project_id == project_id)
        .order_by(FileRecord.created_at.asc(), FileRecord.id.asc())
        .all()
    )
    response = _serialize_project_translation_memory_settings(db, project, files)
    response["initial_match_updated_count"] = 0
    response["initial_match_queued_count"] = initial_match_queued_count
    return response


@router.post("/projects/{project_id}/term-qa-reports")
def create_project_term_qa_report(
    project_id: UUID,
    payload: TermQAReportCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_project_or_404(db, project_id)
    _require_project_read_access(project, current_user, db)
    requested_file_ids = list(dict.fromkeys(payload.file_ids))
    query = db.query(FileRecord).filter(FileRecord.project_id == project_id)
    if requested_file_ids:
        query = query.filter(FileRecord.id.in_(requested_file_ids))
    files = query.order_by(FileRecord.created_at.asc(), FileRecord.id.asc()).all()
    if requested_file_ids and len(files) != len(requested_file_ids):
        raise HTTPException(status_code=404, detail="部分文件不存在或不属于当前项目。")
    files = _visible_project_files(files, current_user, db)
    report = _create_term_qa_report(
        db,
        project_id=project.id,
        files=files,
        current_user=current_user,
        scope="project",
    )
    items = (
        db.query(TermQAReportItem)
        .filter(TermQAReportItem.report_id == report.id)
        .order_by(TermQAReportItem.file_name.asc(), TermQAReportItem.block_index.asc(), TermQAReportItem.sentence_id.asc())
        .all()
    )
    return _serialize_term_qa_report(report, items)


@router.post("/file-records/{file_record_id}/term-qa-reports")
def create_file_record_term_qa_report(
    file_record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="任务不存在。")
    _require_file_record_read_access(file_record, current_user)
    report = _create_term_qa_report(
        db,
        project_id=file_record.project_id,
        files=[file_record],
        current_user=current_user,
        scope="file",
    )
    items = (
        db.query(TermQAReportItem)
        .filter(TermQAReportItem.report_id == report.id)
        .order_by(TermQAReportItem.block_index.asc(), TermQAReportItem.sentence_id.asc())
        .all()
    )
    return _serialize_term_qa_report(report, items)


@router.get("/file-records/{file_record_id}/term-qa-reports")
def list_file_record_term_qa_reports(
    file_record_id: UUID,
    limit: int = 5,
    include_items: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="任务不存在。")
    _require_file_record_read_access(file_record, current_user)

    safe_limit = min(max(int(limit), 1), 20)
    reports = (
        db.query(TermQAReport)
        .filter(TermQAReport.file_record_id == file_record.id)
        .order_by(TermQAReport.created_at.desc(), TermQAReport.id.desc())
        .limit(safe_limit)
        .all()
    )

    items_by_report_id: dict[UUID, list[TermQAReportItem]] = {report.id: [] for report in reports}
    if include_items and reports:
        items = (
            db.query(TermQAReportItem)
            .filter(TermQAReportItem.report_id.in_([report.id for report in reports]))
            .order_by(TermQAReportItem.block_index.asc(), TermQAReportItem.sentence_id.asc())
            .all()
        )
        for item in items:
            items_by_report_id.setdefault(item.report_id, []).append(item)

    return {
        "items": [
            _serialize_term_qa_report(report, items_by_report_id.get(report.id, []))
            for report in reports
        ]
    }


@router.patch("/term-qa-report-items/{item_id}/ignore")
def set_term_qa_report_item_ignored(
    item_id: UUID,
    payload: TermQAReportItemIgnoreRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = _get_term_qa_report_item_or_404(db, item_id)
    report = _get_term_qa_report_or_404(db, item.report_id)
    _require_term_qa_item_write_access(db, item, current_user)
    _apply_term_qa_ignore_state([item], current_user, payload.ignored)
    db.commit()
    db.refresh(report)
    items = _load_term_qa_report_items_for_response(db, report.id)
    return _serialize_term_qa_report(report, items)


@router.patch("/term-qa-reports/{report_id}/items/ignore")
def set_term_qa_report_items_ignored(
    report_id: UUID,
    payload: TermQAReportItemsIgnoreRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_term_qa_report_or_404(db, report_id)
    _require_term_qa_report_read_access(report, current_user, db)
    item_ids = list(dict.fromkeys(payload.item_ids))
    if not item_ids:
        raise HTTPException(status_code=400, detail="请选择要忽略的报告项。")
    items = (
        db.query(TermQAReportItem)
        .filter(
            TermQAReportItem.report_id == report.id,
            TermQAReportItem.id.in_(item_ids),
        )
        .all()
    )
    if len(items) != len(item_ids):
        raise HTTPException(status_code=404, detail="部分术语QA报告项不存在。")
    for item in items:
        _require_term_qa_item_write_access(db, item, current_user)
    _apply_term_qa_ignore_state(items, current_user, payload.ignored)
    db.commit()
    db.refresh(report)
    report_items = _load_term_qa_report_items_for_response(db, report.id)
    return _serialize_term_qa_report(report, report_items)


@router.get("/term-qa-reports/{report_id}")
def get_term_qa_report(
    report_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_term_qa_report_or_404(db, report_id)
    _require_term_qa_report_read_access(report, current_user, db)
    items = _load_term_qa_report_items_for_response(db, report.id)
    return _serialize_term_qa_report(report, items)


@router.get("/term-qa-reports/{report_id}/export-xlsx")
def export_term_qa_report_xlsx(
    report_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_term_qa_report_or_404(db, report_id)
    _require_term_qa_report_read_access(report, current_user, db)
    items = (
        db.query(TermQAReportItem)
        .filter(TermQAReportItem.report_id == report.id)
        .order_by(TermQAReportItem.file_name.asc(), TermQAReportItem.block_index.asc(), TermQAReportItem.sentence_id.asc())
        .all()
    )
    rows = [
        [
            item.file_name,
            item.sentence_id,
            item.term_base_name,
            item.source_term,
            item.expected_target_term,
            item.source_text,
            item.target_text,
            "已忽略" if item.ignored_at else "待处理",
            item.ignored_at.isoformat() if item.ignored_at else "",
            get_user_display_name(item.ignored_by) if item.ignored_by else "",
            item.block_index,
            item.row_index if item.row_index is not None else "",
            item.cell_index if item.cell_index is not None else "",
        ]
        for item in items
    ]
    xlsx_bytes = build_tabular_xlsx(
        sheet_title="术语QA报告",
        headers=[
            "文件名",
            "句段ID",
            "术语库",
            "原文术语",
            "期望译文",
            "原文",
            "译文",
            "处理状态",
            "忽略时间",
            "忽略人",
            "块序号",
            "行序号",
            "单元格序号",
        ],
        rows=rows,
    )
    return build_xlsx_download_response(
        f"term-qa-report-{report.id}",
        xlsx_bytes,
    )


@router.get("/file-records/{file_record_id}/qa-results")
def get_file_record_qa_result(
    file_record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="任务不存在。")
    _require_file_record_read_access(file_record, current_user)
    project = _get_file_record_project_or_400(db, file_record)
    return _build_workbench_qa_result(
        db,
        project=project,
        files=[file_record],
        current_user=current_user,
        scope="file",
        generate=False,
    )


@router.post("/file-records/{file_record_id}/qa-results")
def create_file_record_qa_result(
    file_record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="任务不存在。")
    _require_file_record_work_access(file_record, current_user)
    project = _get_file_record_project_or_400(db, file_record)
    return _build_workbench_qa_result(
        db,
        project=project,
        files=[file_record],
        current_user=current_user,
        scope="file",
        generate=True,
    )


@router.get("/file-records/{file_record_id}/qa-results/export-xlsx")
def export_file_record_qa_result_xlsx(
    file_record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="任务不存在。")
    _require_file_record_read_access(file_record, current_user)
    project = _get_file_record_project_or_400(db, file_record)
    result = _build_workbench_qa_result(
        db,
        project=project,
        files=[file_record],
        current_user=current_user,
        scope="file",
        generate=False,
    )
    return _build_workbench_qa_xlsx_response(
        result,
        f"qa-result-{file_record.id}.xlsx",
    )


@router.patch("/qa-result-items/ignore")
def set_workbench_qa_result_items_ignored(
    payload: WorkbenchQAResultItemsIgnoreRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not payload.item_ids:
        raise HTTPException(status_code=400, detail="请选择要忽略的 QA 问题。")

    segment_issue_ids, term_item_ids = _parse_workbench_qa_item_ids(payload.item_ids)
    updated_count = 0

    if segment_issue_ids:
        issues = (
            db.query(SegmentQAIssue)
            .filter(SegmentQAIssue.id.in_(segment_issue_ids))
            .all()
        )
        if len(issues) != len(segment_issue_ids):
            raise HTTPException(status_code=404, detail="部分 QA 问题不存在。")
        for issue in issues:
            _require_segment_qa_issue_write_access(db, issue, current_user)
        _apply_segment_qa_ignore_state(issues, current_user, payload.ignored)
        updated_count += len(issues)

    if term_item_ids:
        items = (
            db.query(TermQAReportItem)
            .filter(TermQAReportItem.id.in_(term_item_ids))
            .all()
        )
        if len(items) != len(term_item_ids):
            raise HTTPException(status_code=404, detail="部分术语 QA 报告项不存在。")
        for item in items:
            _require_term_qa_item_write_access(db, item, current_user)
        _apply_term_qa_ignore_state(items, current_user, payload.ignored)
        updated_count += len(items)

    db.commit()
    return {
        "updated_count": updated_count,
        "ignored": payload.ignored,
    }


class NumberCheckRecheckRequest(BaseModel):
    item_ids: list[UUID] = Field(default_factory=list)


def _get_number_check_report_or_404(db: Session, report_id: UUID) -> NumberCheckReport:
    report = (
        db.query(NumberCheckReport)
        .filter(NumberCheckReport.id == report_id)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="数字专检报告不存在。")
    return report


def _get_number_check_item_or_404(db: Session, item_id: UUID) -> NumberCheckReportItem:
    item = (
        db.query(NumberCheckReportItem)
        .filter(NumberCheckReportItem.id == item_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="数字专检报告项不存在。")
    return item


def _require_number_check_report_read_access(
    report: NumberCheckReport,
    current_user: User,
    db: Session,
) -> None:
    if report.file_record_id:
        file_record = get_file_record_model(db, report.file_record_id)
        if file_record:
            _require_file_record_read_access(file_record, current_user)
            return
    if report.project_id:
        project = db.query(Project).filter(Project.id == report.project_id).first()
        if project:
            _require_project_read_access(project, current_user, db)
            return
    if not can_access_all_projects(current_user):
        raise HTTPException(status_code=403, detail="无权访问该数字专检报告。")


def _resolve_file_record_project(db: Session, file_record: FileRecord) -> Project | None:
    if not file_record.project_id:
        return None
    return file_record.project or db.query(Project).filter(Project.id == file_record.project_id).first()


@router.post("/file-records/{file_record_id}/number-check-reports")
async def create_file_record_number_check_report(
    file_record_id: UUID,
    run_ai: bool = Query(default=True),
    ai_scope: str = Query(default="program_only"),
    provider: str = Query(default="auto"),
    model: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")
    _require_file_record_read_access(file_record, current_user)
    project = _resolve_file_record_project(db, file_record)
    report = create_number_check_report(
        db,
        project=project,
        files=[file_record],
        current_user=current_user,
        scope="file",
    )
    if run_ai:
        if ai_scope == "all":
            await run_ai_number_check_all_segments(
                db, report, [file_record], provider=provider, model=model
            )
        elif report.program_issue_count > 0:
            await run_ai_number_check_for_report(db, report, provider=provider, model=model)
    return serialize_number_check_report(report, load_number_check_items(db, report.id))


def _build_number_check_stream(
    db: Session,
    request: Request,
    *,
    project: Project | None,
    files: list[FileRecord],
    current_user: User,
    scope: str,
    run_ai: bool,
    ai_scope: str,
    provider: str,
    model: str | None,
):
    async def event_stream():
        report_id: str | None = None
        try:
            async for event in aiter_number_check_generation(
                db,
                project=project,
                files=files,
                current_user=current_user,
                scope=scope,
                run_ai=run_ai,
                ai_scope=ai_scope,
                provider=provider,
                model=model,
            ):
                if await request.is_disconnected():
                    break
                stage = event.get("stage")
                if stage == "complete":
                    report_id = event.get("report_id")
                    break
                yield _sse_event(stage, event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("number-check stream failed")
            yield _sse_event("error", {"message": str(exc)})
            return

        if report_id is not None and not await request.is_disconnected():
            report = _get_number_check_report_or_404(db, UUID(report_id))
            yield _sse_event(
                "complete",
                {"report": serialize_number_check_report(report, load_number_check_items(db, report.id))},
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


@router.post("/file-records/{file_record_id}/number-check-reports/stream")
async def stream_file_record_number_check_report(
    file_record_id: UUID,
    request: Request,
    run_ai: bool = Query(default=True),
    ai_scope: str = Query(default="program_only"),
    provider: str = Query(default="auto"),
    model: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")
    _require_file_record_read_access(file_record, current_user)
    project = _resolve_file_record_project(db, file_record)
    return _build_number_check_stream(
        db,
        request,
        project=project,
        files=[file_record],
        current_user=current_user,
        scope="file",
        run_ai=run_ai,
        ai_scope=ai_scope,
        provider=provider,
        model=model,
    )


@router.get("/file-records/{file_record_id}/number-check-reports")
def list_file_record_number_check_reports(
    file_record_id: UUID,
    limit: int = 1,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")
    _require_file_record_read_access(file_record, current_user)
    safe_limit = min(max(int(limit), 1), 20)
    reports = (
        db.query(NumberCheckReport)
        .filter(NumberCheckReport.file_record_id == file_record_id)
        .order_by(NumberCheckReport.created_at.desc(), NumberCheckReport.id.desc())
        .limit(safe_limit)
        .all()
    )
    return {
        "items": [
            serialize_number_check_report(report, load_number_check_items(db, report.id))
            for report in reports
        ]
    }


@router.get("/number-check-reports/{report_id}")
def get_number_check_report(
    report_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_number_check_report_or_404(db, report_id)
    _require_number_check_report_read_access(report, current_user, db)
    return serialize_number_check_report(report, load_number_check_items(db, report.id))


@router.post("/number-check-reports/{report_id}/ai-recheck")
async def recheck_number_check_report(
    report_id: UUID,
    payload: NumberCheckRecheckRequest | None = None,
    provider: str = Query(default="auto"),
    model: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_number_check_report_or_404(db, report_id)
    _require_number_check_report_read_access(report, current_user, db)
    item_ids = list(dict.fromkeys((payload.item_ids if payload else []) or [])) or None
    await run_ai_number_check_for_report(
        db,
        report,
        item_ids=item_ids,
        provider=provider,
        model=model,
    )
    return serialize_number_check_report(report, load_number_check_items(db, report.id))


@router.patch("/number-check-report-items/{item_id}/apply")
def apply_number_check_report_item(
    item_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = _get_number_check_item_or_404(db, item_id)
    file_record = get_file_record_model(db, item.file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="对应文件不存在。")
    _require_file_record_work_access(file_record, current_user)
    apply_number_check_item(db, item, current_user)
    report = _get_number_check_report_or_404(db, item.report_id)
    return serialize_number_check_report(report, load_number_check_items(db, report.id))


@router.patch("/number-check-report-items/{item_id}/restore")
def restore_number_check_report_item(
    item_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = _get_number_check_item_or_404(db, item_id)
    file_record = get_file_record_model(db, item.file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="对应文件不存在。")
    _require_file_record_work_access(file_record, current_user)
    restore_number_check_item(db, item, current_user)
    report = _get_number_check_report_or_404(db, item.report_id)
    return serialize_number_check_report(report, load_number_check_items(db, report.id))


@router.patch("/number-check-report-items/{item_id}/ignore")
def set_number_check_report_item_ignored(
    item_id: UUID,
    payload: TermQAReportItemIgnoreRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = _get_number_check_item_or_404(db, item_id)
    file_record = get_file_record_model(db, item.file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="对应文件不存在。")
    _require_file_record_work_access(file_record, current_user)
    set_number_check_item_ignored(db, item, current_user, payload.ignored)
    report = _get_number_check_report_or_404(db, item.report_id)
    return serialize_number_check_report(report, load_number_check_items(db, report.id))


def _require_number_check_report_write_access(
    db: Session,
    report: NumberCheckReport,
    current_user: User,
) -> None:
    file_ids = {
        row.file_record_id
        for row in db.query(NumberCheckReportItem.file_record_id)
        .filter(NumberCheckReportItem.report_id == report.id)
        .distinct()
        .all()
    }
    for file_record_id in file_ids:
        file_record = get_file_record_model(db, file_record_id)
        if file_record is None:
            continue
        _require_file_record_work_access(file_record, current_user)


@router.post("/number-check-reports/{report_id}/apply-all")
def apply_all_number_check_report_items(
    report_id: UUID,
    payload: NumberCheckRecheckRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_number_check_report_or_404(db, report_id)
    _require_number_check_report_write_access(db, report, current_user)
    item_ids = list(dict.fromkeys((payload.item_ids if payload else []) or [])) or None
    applied_count = apply_number_check_items_bulk(db, report, current_user, item_ids=item_ids)
    db.refresh(report)
    result = serialize_number_check_report(report, load_number_check_items(db, report.id))
    result["applied_count"] = applied_count
    return result


@router.post("/number-check-reports/{report_id}/ignore-all")
def ignore_all_number_check_report_items(
    report_id: UUID,
    payload: NumberCheckRecheckRequest | None = None,
    ignored: bool = Query(default=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_number_check_report_or_404(db, report_id)
    _require_number_check_report_write_access(db, report, current_user)
    item_ids = list(dict.fromkeys((payload.item_ids if payload else []) or [])) or None
    updated_count = ignore_number_check_items_bulk(
        db, report, current_user, item_ids=item_ids, ignored=ignored
    )
    db.refresh(report)
    result = serialize_number_check_report(report, load_number_check_items(db, report.id))
    result["updated_count"] = updated_count
    return result


@router.get("/projects/{project_id}")
def get_project_detail(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from sqlalchemy.orm import joinedload

    project = (
        db.query(Project)
        .options(joinedload(Project.creator))
        .filter(Project.id == project_id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在。")

    _require_project_read_access(project, current_user, db)

    files = (
        db.query(FileRecord)
        .options(joinedload(FileRecord.assignee))
        .filter(FileRecord.project_id == project_id)
        .order_by(FileRecord.created_at.asc(), FileRecord.id.asc())
        .all()
    )
    files = _visible_project_files(files, current_user, db)
    if False and is_external_translator(current_user) and not files:
        raise HTTPException(status_code=404, detail="项目不存在或没有分配给当前用户的任务。")
    stale_lock_cleared = False
    for file_record in files:
        stale_lock_cleared = clear_stale_file_operation_lock(db, file_record) or stale_lock_cleared
    if stale_lock_cleared:
        db.commit()
    file_stats = _get_file_segment_stats(db, [file_record.id for file_record in files])
    if is_external_translator(current_user):
        issue_markers = []
        for file_record in files:
            issue_markers.extend(list_issue_markers_for_project(
                db,
                project_id,
                file_record_id=file_record.id,
            ))
    else:
        issue_markers = list_issue_markers_for_project(db, project_id)
    file_issue_stats = _get_file_issue_stats(db, [file_record.id for file_record in files])
    if is_external_translator(current_user):
        project_issue_stats = {
            "issue_count": sum(item.get("issue_count", 0) for item in file_issue_stats.values()),
            "open_issue_count": sum(item.get("open_issue_count", 0) for item in file_issue_stats.values()),
        }
    else:
        project_issue_stats = _get_project_issue_stats(db, [project_id]).get(project_id)
    assigned_users = _get_active_project_assignees(db, [project_id]).get(project_id, [])
    file_assignees = _get_active_file_assignees(db, [file_record.id for file_record in files])
    return _build_project_detail_payload(
        db,
        project,
        files,
        file_stats,
        issue_markers=issue_markers,
        project_issue_stats=project_issue_stats,
        file_issue_stats=file_issue_stats,
        current_user=current_user,
        assigned_users=assigned_users,
        file_assignees=file_assignees,
    )


@router.post("/projects/{project_id}/document-statistics")
def compute_project_document_statistics(
    project_id: UUID,
    payload: ProjectDocumentStatisticsPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在。")

    file_ids = list(dict.fromkeys(payload.file_ids))
    if not file_ids:
        raise HTTPException(status_code=400, detail="请先选择要统计的文件。")

    files = (
        db.query(FileRecord)
        .filter(FileRecord.project_id == project_id, FileRecord.id.in_(file_ids))
        .order_by(FileRecord.created_at.asc(), FileRecord.id.asc())
        .all()
    )
    if len(files) != len(file_ids):
        raise HTTPException(status_code=404, detail="部分文件不存在或不属于当前项目。")

    unavailable_statistics = _build_unavailable_document_statistics()
    report_statistics: list[dict[str, Any]] = []
    report = DocumentStatisticsReport(
        project_id=project.id,
        created_by_id=getattr(current_user, "id", None),
        file_ids=json.dumps([str(file_record.id) for file_record in files]),
        total_files=len(files),
        available_files=0,
        totals=json.dumps(_create_empty_document_statistics_totals(), ensure_ascii=False, sort_keys=True),
        status="completed",
    )
    db.add(report)
    db.flush()

    repetition_statistics_by_file_id = _load_document_repetition_statistics_for_files(
        db,
        [file_record.id for file_record in files],
    )
    match_analysis_by_file_id = _load_document_match_analysis_for_files(db, files)
    for file_record in files:
        source_bytes = load_file_record_source(file_record)
        source_filename = get_file_record_source_filename(file_record)
        if source_bytes and Path(source_filename).suffix.lower() in {".doc", ".docx"}:
            statistics = compute_word_document_statistics(source_bytes, source_filename)
        else:
            statistics = unavailable_statistics
        normalized_statistics = normalize_document_statistics(statistics)
        normalized_statistics.update(
            repetition_statistics_by_file_id.get(file_record.id, empty_repetition_statistics())
        )
        match_analysis = match_analysis_by_file_id.get(file_record.id, empty_document_match_analysis())
        normalized_statistics["match_analysis"] = reconcile_document_match_analysis_words(
            match_analysis,
            normalized_statistics.get("words") if isinstance(normalized_statistics.get("words"), int) else None,
        ) or match_analysis
        serialized_statistics = serialize_document_statistics(normalized_statistics)
        file_record.document_statistics = serialized_statistics
        report_statistics.append(normalized_statistics)
        db.add(DocumentStatisticsReportItem(
            report_id=report.id,
            project_id=project.id,
            file_record_id=file_record.id,
            file_name=file_record.filename,
            source_language=file_record.source_language,
            target_language=file_record.target_language,
            file_size_bytes=len(source_bytes) if source_bytes is not None else None,
            statistics=serialized_statistics,
        ))

    report.available_files = sum(1 for statistics in report_statistics if _has_any_document_statistic(statistics))
    report.totals = json.dumps(_sum_document_statistics(report_statistics), ensure_ascii=False, sort_keys=True)

    db.commit()
    db.refresh(report)
    for file_record in files:
        db.refresh(file_record)
    report_items = _load_document_statistics_report_items_for_response(db, [report.id]).get(report.id, [])

    file_stats = _get_file_segment_stats(db, [file_record.id for file_record in files])
    file_issue_stats = _get_file_issue_stats(db, [file_record.id for file_record in files])
    return {
        "report": _serialize_document_statistics_report(report, report_items),
        "files": [
            _build_project_file_payload(
                file_record=file_record,
                total_segments=file_stats.get(file_record.id, {"total": 0})["total"],
                translated_segments=file_stats.get(file_record.id, {"filled": 0})["filled"],
                pretranslated_segments=file_stats.get(file_record.id, {}).get("pretranslated", 0),
                issue_stats=file_issue_stats.get(file_record.id),
                current_user=current_user,
            )
            for file_record in files
        ]
    }


@router.get("/projects/{project_id}/document-statistics-reports")
def list_project_document_statistics_reports(
    project_id: UUID,
    limit: int = 20,
    include_items: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在。")

    safe_limit = min(max(int(limit), 1), 50)
    reports = (
        db.query(DocumentStatisticsReport)
        .filter(DocumentStatisticsReport.project_id == project_id)
        .order_by(DocumentStatisticsReport.created_at.desc(), DocumentStatisticsReport.id.desc())
        .limit(safe_limit)
        .all()
    )
    items_by_report_id = (
        _load_document_statistics_report_items_for_response(db, [report.id for report in reports])
        if include_items
        else {report.id: [] for report in reports}
    )
    return {
        "items": [
            _serialize_document_statistics_report(report, items_by_report_id.get(report.id, []))
            for report in reports
        ]
    }


@router.get("/document-statistics-reports/{report_id}")
def get_document_statistics_report(
    report_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    report = (
        db.query(DocumentStatisticsReport)
        .filter(DocumentStatisticsReport.id == report_id)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="字数统计报告不存在。")

    report_items = _load_document_statistics_report_items_for_response(db, [report.id]).get(report.id, [])
    return _serialize_document_statistics_report(report, report_items)


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在。")

    file_record_ids = [
        row.id
        for row in db.query(FileRecord.id).filter(FileRecord.project_id == project_id).all()
    ]
    for file_record_id in file_record_ids:
        delete_file_record(db, file_record_id)

    project = db.query(Project).filter(Project.id == project_id).first()
    if project is not None:
        db.delete(project)
        db.commit()

    return {"message": "项目已删除。"}


@router.patch("/projects/{project_id}")
def update_project(
    project_id: UUID,
    payload: ProjectUpdatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在。")

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="项目名称不能为空。")
        project.name = name

    if {"source_language", "target_language"} & payload.model_fields_set:
        raise HTTPException(status_code=400, detail="项目语言对不允许修改。")

    if payload.deadline is not None:
        project.deadline = _parse_optional_datetime(payload.deadline)

    if payload.access_level is not None:
        project.access_level = payload.access_level

    if payload.translation_guidelines is not None:
        project.translation_guidelines = payload.translation_guidelines

    db.commit()
    db.refresh(project)

    return {
        "id": str(project.id),
        "name": project.name,
        "filename": project.name,
        "source_language": project.source_language,
        "target_language": project.target_language,
        "deadline": project.deadline.isoformat() if project.deadline else None,
        "access_level": project.access_level,
        "translation_guidelines": project.translation_guidelines or "",
        "updated_at": project.updated_at.isoformat(),
    }


@router.get("/projects/{project_id}/quality-qa-settings")
def get_project_quality_qa_settings(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    _require_project_read_access(project, current_user, db)
    return _serialize_quality_qa_settings_response(db, project)


@router.patch("/projects/{project_id}/quality-qa-settings")
def update_project_quality_qa_settings(
    project_id: UUID,
    payload: QualityQASettingsRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    normalized = store_quality_qa_settings(project, payload.model_dump())
    db.commit()
    db.refresh(project)
    if normalized["spelling_grammar"]["enabled"]:
        background_tasks.add_task(_dispatch_spelling_grammar_qa_project, project.id)
    return _serialize_quality_qa_settings_response(db, project)


@router.post("/file-records/{file_record_id}/qa-checks/spelling-grammar")
def refresh_file_record_spelling_grammar_qa(
    file_record_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")
    _require_file_record_work_access(file_record, current_user)
    project = file_record.project or (
        db.query(Project).filter(Project.id == file_record.project_id).first()
        if file_record.project_id
        else None
    )
    if not project:
        raise HTTPException(status_code=400, detail="当前文件未归属项目，无法使用项目 QA 设置")
    quality_settings = load_quality_qa_settings(project)
    if not quality_settings["spelling_grammar"]["enabled"]:
        raise HTTPException(status_code=400, detail="项目尚未启用拼写/语法 QA")
    if not is_languagetool_configured():
        raise HTTPException(status_code=503, detail="LanguageTool 未配置，无法手动刷新")
    if not get_languagetool_language(file_record.target_language):
        raise HTTPException(status_code=400, detail="当前目标语言暂不支持拼写/语法 QA")

    segments = (
        db.query(Segment)
        .filter(
            Segment.file_record_id == file_record_id,
            func.trim(func.coalesce(Segment.target_text, "")) != "",
        )
        .all()
    )
    _schedule_spelling_grammar_qa_for_segments(background_tasks, file_record, segments)
    return {
        "file_record_id": str(file_record_id),
        "queued_count": len(segments),
    }


@router.patch("/segment-qa-issues/{issue_id}/ignore")
def update_segment_qa_issue_ignore(
    issue_id: UUID,
    payload: SegmentQAIssueIgnoreRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    issue = db.query(SegmentQAIssue).filter(SegmentQAIssue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="QA 问题不存在")
    file_record = issue.file_record or db.query(FileRecord).filter(FileRecord.id == issue.file_record_id).first()
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")
    _require_file_record_work_access(file_record, current_user)

    if payload.ignored:
        issue.status = QA_ISSUE_STATUS_IGNORED
        issue.ignored_by_id = current_user.id
        issue.ignored_at = datetime.now()
    else:
        issue.status = QA_ISSUE_STATUS_OPEN
        issue.ignored_by_id = None
        issue.ignored_at = None
    issue.updated_at = datetime.now()
    if issue.segment:
        issue.segment.updated_at = issue.updated_at
    db.commit()
    db.refresh(issue)
    return serialize_segment_qa_issue(issue)


@router.get("/projects/{project_id}/issue-markers")
def list_project_issue_markers(
    project_id: UUID,
    status: Literal["open", "resolved"] | None = None,
    file_record_id: UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在。")
    if is_external_translator(current_user):
        assigned_file_ids = [
            row.file_record_id
            for row in (
                db.query(FileAssignment.file_record_id)
                .join(FileRecord, FileRecord.id == FileAssignment.file_record_id)
                .filter(
                    FileRecord.project_id == project_id,
                    FileAssignment.assignee_id == current_user.id,
                    FileAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
                )
                .all()
            )
        ]
        if not assigned_file_ids:
            raise HTTPException(status_code=404, detail="项目不存在或没有分配给当前用户的任务。")
        if file_record_id is not None and file_record_id not in assigned_file_ids:
            raise HTTPException(status_code=404, detail="任务不存在或未分配给当前用户。")
        if file_record_id is None:
            markers = []
            for assigned_file_id in assigned_file_ids:
                markers.extend(list_issue_markers_for_project(
                    db,
                    project_id,
                    status=status,
                    file_record_id=assigned_file_id,
                ))
            return [serialize_issue_marker(marker) for marker in markers]

    markers = list_issue_markers_for_project(
        db,
        project_id,
        status=status,
        file_record_id=file_record_id,
    )
    return [serialize_issue_marker(marker) for marker in markers]


@router.post("/projects/{project_id}/issue-markers")
def create_project_issue_marker(
    project_id: UUID,
    payload: IssueMarkerCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if is_external_translator(current_user):
        if payload.file_record_id is None:
            raise HTTPException(status_code=403, detail="外部译者只能在已分配任务上提交问题标记。")
        assigned = (
            db.query(FileAssignment.id)
            .join(FileRecord, FileRecord.id == FileAssignment.file_record_id)
            .filter(
                FileRecord.project_id == project_id,
                FileRecord.id == payload.file_record_id,
                FileAssignment.assignee_id == current_user.id,
                FileAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
            )
            .first()
        )
        if assigned is None:
            raise HTTPException(status_code=404, detail="任务不存在或未分配给当前用户。")
    marker = create_issue_marker(
        db,
        project_id=project_id,
        file_record_id=payload.file_record_id,
        title=payload.title,
        description=payload.description,
        category=payload.category,
        severity=payload.severity,
        page_url=payload.page_url,
        user_agent=payload.user_agent,
        reporter=current_user,
    )
    return serialize_issue_marker(marker)


@router.patch("/issue-markers/{marker_id}")
def patch_issue_marker(
    marker_id: UUID,
    payload: IssueMarkerUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    marker = update_issue_marker(
        db,
        marker_id=marker_id,
        title=payload.title,
        description=payload.description,
        category=payload.category,
        severity=payload.severity,
        status=payload.status,
        current_user=current_user,
    )
    return serialize_issue_marker(marker)


@router.delete("/issue-markers/{marker_id}")
def remove_issue_marker(
    marker_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    delete_issue_marker(
        db,
        marker_id=marker_id,
        current_user=current_user,
    )
    return {"message": "问题标记已删除。"}


@router.post("/projects/{project_id}/detect-source-language")
def detect_project_source_language(
    project_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    # 定义为同步 def，由 FastAPI 调度到线程池执行，避免语言识别的 CPU 操作阻塞事件循环。
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在。")

    raw_bytes = _read_upload_file_bytes_with_limit(file)
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空，无法识别语言。")

    result = detect_upload_language(file.filename or "", raw_bytes)
    return result.to_dict()


@router.post("/projects/{project_id}/source-document")
async def upload_project_source_document(
    project_id: UUID,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] | None = File(default=None),
    file: UploadFile | None = File(default=None),
    threshold: float = Form(default=0.6),
    collection_ids: list[UUID] | None = Form(default=None),
    term_base_id: UUID | None = Form(default=None),
    source_language: str | None = Form(default=None),
    target_language: str | None = Form(default=None),
    document_parse_mode: str = Form(default=DOCUMENT_PARSE_MODE_FULL),
    document_parse_options: str | None = Form(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在。")

    uploaded_files = list(files or [])
    if file is not None:
        uploaded_files.append(file)
    if not uploaded_files:
        raise HTTPException(status_code=400, detail="请选择要上传的文件。")

    for upload_file in uploaded_files:
        _validate_task_upload(upload_file)
    document_parse_mode = _normalize_upload_document_parse_mode(document_parse_mode)
    normalized_parse_options = _normalize_upload_document_parse_options(document_parse_options, document_parse_mode)

    selected_collection_ids = _validate_collection_ids(db, collection_ids) or []
    primary_collection = _get_collection_or_404(
        db,
        selected_collection_ids[0] if selected_collection_ids else None,
    )
    resolved_source_language, resolved_target_language = _resolve_upload_language_pair(
        source_language,
        target_language,
        primary_collection,
    )

    term_base = None
    if term_base_id is not None:
        term_base = db.query(TermBase).filter(TermBase.id == term_base_id).first()
        if term_base is None:
            raise HTTPException(status_code=404, detail="术语库不存在。")
        _ensure_resource_language_pair_matches(
            term_base,
            resolved_source_language,
            resolved_target_language,
            "术语库",
        )

    payload = {
        "kind": "project_source_document",
        "project_id": str(project.id),
        "threshold": threshold,
        "collection_ids": [str(collection_id) for collection_id in selected_collection_ids],
        "term_base_id": str(term_base_id) if term_base_id is not None else None,
        "source_language": resolved_source_language,
        "target_language": resolved_target_language,
        "document_parse_mode": document_parse_mode,
        "document_parse_options": normalized_parse_options,
    }
    return await _queue_import_task(
        background_tasks,
        payload,
        staging_upload_files=[
            (upload_file.filename or "source.txt", upload_file.file)
            for upload_file in uploaded_files
        ],
    )


@router.get("/projects")
def list_projects(
    skip: int = 0,
    limit: int = 50,
    search: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取项目列表（分页、搜索），含翻译进度统计"""
    from sqlalchemy.orm import joinedload

    base_query = db.query(Project).options(joinedload(Project.creator))
    base_query = _apply_project_visibility_filter(base_query, db, current_user)
    if search.strip():
        base_query = base_query.filter(Project.name.ilike(f"%{search.strip()}%"))

    total = base_query.count()
    safe_skip = max(skip, 0)
    safe_limit = min(max(limit, 1), 200)
    projects = (
        base_query
        .order_by(Project.created_at.desc())
        .offset(safe_skip)
        .limit(safe_limit)
        .all()
    )

    project_ids = [project.id for project in projects]
    project_stats = _get_project_stats(db, project_ids, current_user=current_user)
    project_issue_stats = _get_project_issue_stats(db, project_ids, current_user=current_user)
    project_assignees = _get_active_project_assignees(db, project_ids)
    workflow_steps_by_project = _load_workflow_steps_by_project(db, project_ids)
    workflow_progress_by_project = _get_project_workflow_progress(db, project_ids)

    items = []
    for project in projects:
        st = project_stats.get(project.id, {"file_count": 0, "total": 0, "filled": 0, "pretranslated": 0})
        total_segs = st["total"]
        filled_segs = st["filled"]
        pretranslated_segs = st["pretranslated"]

        creator_name = None
        if project.creator:
            creator_name = get_user_display_name(project.creator)

        items.append(
            _build_project_summary_payload(
                project=project,
                total_segments=total_segs,
                translated_segments=filled_segs,
                pretranslated_segments=pretranslated_segs,
                file_count=st["file_count"],
                creator_name=creator_name,
                issue_stats=project_issue_stats.get(project.id),
                current_user=current_user,
                assigned_users=project_assignees.get(project.id),
                workflow_steps=workflow_steps_by_project.get(project.id, []),
                workflow_progress=workflow_progress_by_project.get(project.id, []),
            )
        )

    return {
        "items": items,
        "total": total,
        "skip": safe_skip,
        "limit": safe_limit,
    }


@router.get("/file-records/upload-capabilities")
def get_file_record_upload_capabilities():
    """返回任务上传入口真实支持的格式和解析能力。"""
    return get_upload_capabilities()


@router.get("/file-records")
@router.get("/documents", include_in_schema=False)
def get_file_records(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取文档列表"""
    from sqlalchemy.orm import joinedload

    safe_skip = max(skip, 0)
    safe_limit = min(max(limit, 1), 200)
    query = db.query(FileRecord).options(
        joinedload(FileRecord.assignee),
        joinedload(FileRecord.project),
    )
    if not can_access_all_projects(current_user):
        query = (
            query.join(FileAssignment, FileAssignment.file_record_id == FileRecord.id)
            .join(ProjectAssignment, ProjectAssignment.project_id == FileRecord.project_id)
            .filter(
                FileAssignment.assignee_id == current_user.id,
                FileAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
                ProjectAssignment.assignee_id == current_user.id,
                ProjectAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
            )
            .distinct()
        )
    file_records = (
        query
        .order_by(FileRecord.created_at.desc())
        .offset(safe_skip)
        .limit(safe_limit)
        .all()
    )
    file_stats = _get_file_segment_stats(db, [file_record.id for file_record in file_records])
    file_issue_stats = _get_file_issue_stats(db, [file_record.id for file_record in file_records])
    file_assignees = _get_active_file_assignees(db, [file_record.id for file_record in file_records])
    file_record_ids = [file_record.id for file_record in file_records]
    project_ids = sorted(
        {file_record.project_id for file_record in file_records if file_record.project_id},
        key=str,
    )
    workflow_steps_by_project = _load_workflow_steps_by_project(db, project_ids)
    file_workflow_progress = _get_file_workflow_progress(db, file_record_ids)
    return [
        {
            "id": file_record.id,
            "project_id": str(file_record.project_id) if file_record.project_id else None,
            "project_name": file_record.project.name if file_record.project else None,
            "filename": file_record.filename,
            "status": file_record.status,
            "progress": _calculate_workflow_overall_progress(
                file_workflow_progress.get(file_record.id, []),
                calculate_file_record_progress(
                    file_stats.get(file_record.id, {"total": 0})["total"],
                    file_stats.get(file_record.id, {"filled": 0})["filled"],
                ),
            ),
            "total_segments": file_stats.get(file_record.id, {"total": 0})["total"],
            "translated_segments": file_stats.get(file_record.id, {"filled": 0})["filled"],
            "confirmed_segments": file_stats.get(file_record.id, {"filled": 0})["filled"],
            "pretranslated_segments": file_stats.get(file_record.id, {}).get("pretranslated", 0),
            "pretranslation_progress": calculate_file_record_progress(
                file_stats.get(file_record.id, {"total": 0})["total"],
                file_stats.get(file_record.id, {}).get("pretranslated", 0),
            ),
            "issue_count": file_issue_stats.get(file_record.id, {}).get("issue_count", 0),
            "open_issue_count": file_issue_stats.get(file_record.id, {}).get("open_issue_count", 0),
            "document_parse_mode": getattr(file_record, "document_parse_mode", DOCUMENT_PARSE_MODE_FULL),
            "document_parse_options": _get_file_record_document_parse_options(file_record),
            "document_statistics": get_file_record_document_statistics(file_record),
            "source_language": file_record.source_language,
            "target_language": file_record.target_language,
            "assignee_id": str(file_record.assignee_id) if file_record.assignee_id else None,
            "assignee": _serialize_assignee(file_record.assignee),
            "assignees": _serialize_user_list(file_assignees.get(file_record.id)),
            "assigned_at": file_record.assigned_at.isoformat() if file_record.assigned_at else None,
            "workflow_steps": [
                _serialize_workflow_step(step)
                for step in workflow_steps_by_project.get(file_record.project_id, [])
            ],
            "workflow_progress": file_workflow_progress.get(file_record.id, []),
            "can_manage": _can_manage_workflow(current_user),
            "can_write": _can_write_file_record(file_record, current_user, db),
            "created_at": file_record.created_at.isoformat(),
            "updated_at": file_record.updated_at.isoformat(),
        }
        for file_record in file_records
    ]


SEGMENT_PAGE_MAX_LIMIT = 500


def _normalize_segment_page_limit(limit: int) -> int:
    return min(max(int(limit), 1), SEGMENT_PAGE_MAX_LIMIT)


def _build_segment_workflow_context(
    db: Session,
    file_record: FileRecord,
    current_user: User | None,
) -> tuple[dict[UUID, ProjectWorkflowStep], list[SegmentWritableAssignment] | None, bool]:
    _assign_file_segments_to_first_workflow_step(db, file_record)
    workflow_steps = _load_project_workflow_steps(db, file_record.project_id)
    can_manage = _can_manage_workflow(current_user)
    writable_assignments: list[SegmentWritableAssignment] | None = None
    if current_user is not None:
        if can_access_all_projects(current_user):
            writable_assignments = [
                SegmentWritableAssignment(workflow_step_id=step.id)
                for step in workflow_steps
            ]
        else:
            writable_assignments = _get_user_writable_assignments(db, file_record, current_user.id)
    return {step.id: step for step in workflow_steps}, writable_assignments, can_manage


def _build_target_automatic_numbering_text_map(
    file_record: FileRecord,
) -> dict[str, str]:
    source_filename = get_file_record_source_filename(file_record)
    if get_task_file_extension(source_filename) != ".docx":
        return {}

    raw_bytes = load_file_record_source(file_record)
    if raw_bytes is None:
        return {}

    try:
        return build_docx_target_numbering_text_map(
            raw_bytes,
            document_parse_mode=getattr(file_record, "document_parse_mode", DOCUMENT_PARSE_MODE_FULL),
            document_parse_options=_get_file_record_document_parse_options(file_record),
            target_language=file_record.target_language,
        )
    except Exception:
        logger.exception(
            "failed to build target automatic numbering map file_record_id=%s",
            getattr(file_record, "id", None),
        )
        return {}


def _serialize_workbench_segment(
    seg: Segment,
    display_index: int | None = None,
    *,
    source_filename: str | None = None,
    target_automatic_numbering_by_sentence_id: dict[str, str] | None = None,
    qa_issues_by_segment_id: dict[UUID, list[SegmentQAIssue]] | None = None,
    workflow_step_by_id: dict[UUID, ProjectWorkflowStep] | None = None,
    writable_workflow_assignments: list[SegmentWritableAssignment] | None = None,
    can_manage: bool = False,
) -> dict:
    resolved_source_filename = source_filename
    if resolved_source_filename is None:
        resolved_source_filename = getattr(getattr(seg, "file_record", None), "filename", None)
    automatic_numbering_text = (
        get_automatic_numbering_text(
            source_text=seg.source_text,
            display_text=seg.display_text,
        )
        if is_word_document_filename(resolved_source_filename)
        else ""
    )
    target_automatic_numbering_text = ""
    if automatic_numbering_text and target_automatic_numbering_by_sentence_id:
        target_automatic_numbering_text = (
            target_automatic_numbering_by_sentence_id.get(str(seg.sentence_id), "") or ""
        ).strip()
    resolved_workflow_step_id = seg.workflow_step_id
    if resolved_workflow_step_id is None and workflow_step_by_id:
        resolved_workflow_step_id = next(iter(workflow_step_by_id.keys()), None)
    workflow_step = workflow_step_by_id.get(resolved_workflow_step_id) if workflow_step_by_id else None
    can_write = True
    if writable_workflow_assignments is not None:
        can_write = bool(
            can_manage
            or _can_write_segment_with_assignments(
                resolved_workflow_step_id,
                display_index,
                writable_workflow_assignments,
            )
        )
    payload = {
        "id": str(seg.id),
        "sentence_id": seg.sentence_id,
        "source_text": seg.source_text,
        "display_text": seg.display_text,
        "source_body_text": seg.source_text,
        "automatic_numbering_text": automatic_numbering_text or None,
        "target_automatic_numbering_text": target_automatic_numbering_text or None,
        "source_html": seg.source_html,
        "target_text": seg.target_text,
        "target_html": seg.target_html,
        "status": seg.status,
        "project_sync_disabled": bool(getattr(seg, "project_sync_disabled", False)),
        "version": int(seg.version or 1),
        "score": seg.score,
        "matched_source_text": seg.matched_source_text,
        "matched_collection_name": seg.matched_collection_name,
        "matched_creator_name": seg.matched_creator_name,
        "matched_created_at": seg.matched_created_at.isoformat() if seg.matched_created_at else None,
        "matched_updated_at": seg.matched_updated_at.isoformat() if seg.matched_updated_at else None,
        "source": seg.source,
        "llm_provider": seg.llm_provider,
        "llm_model": seg.llm_model,
        "last_modified_by_id": str(seg.last_modified_by_id) if seg.last_modified_by_id else None,
        "last_modified_by": serialize_user(seg.last_modified_by) if seg.last_modified_by else None,
        "block_type": seg.block_type,
        "block_index": seg.block_index,
        "row_index": seg.row_index,
        "cell_index": seg.cell_index,
        "workflow_step_id": str(resolved_workflow_step_id) if resolved_workflow_step_id else None,
        "workflow_step_name": workflow_step.name if workflow_step else "翻译",
        "workflow_step_order": int(workflow_step.sort_order or 0) if workflow_step else 0,
        "can_write": can_write,
        "qa_issues": [
            serialize_segment_qa_issue(issue)
            for issue in (qa_issues_by_segment_id or {}).get(seg.id, [])
        ],
        "updated_at": seg.updated_at.isoformat() if seg.updated_at else None,
    }
    if display_index is not None:
        payload["display_index"] = display_index
    return payload


def _load_workbench_segment_qa_issues(
    db: Session,
    segments: list[Segment],
) -> dict[UUID, list[SegmentQAIssue]]:
    return load_open_segment_qa_issues_by_segment_id(
        db,
        [segment.id for segment in segments],
    )


def _schedule_spelling_grammar_qa_for_segments(
    background_tasks: BackgroundTasks | None,
    file_record: FileRecord,
    segments: Iterable[Segment],
) -> None:
    if background_tasks is None:
        return
    segment_ids = [
        segment.id
        for segment in segments
        if normalize_text(getattr(segment, "target_text", "") or "")
    ]
    if not segment_ids:
        return
    background_tasks.add_task(
        _dispatch_spelling_grammar_qa_segments, file_record.id, segment_ids
    )


def _serialize_segment_update_conflict(conflict) -> dict:
    return {
        "sentence_id": conflict.sentence_id,
        "current_version": conflict.current_version,
        "attempted_version": conflict.attempted_version,
        "current_target_text": conflict.current_target_text,
        "conflict_source": getattr(conflict, "current_source", None),
        "conflict_updated_at": (
            conflict.current_updated_at.isoformat()
            if getattr(conflict, "current_updated_at", None)
            else None
        ),
        "conflict_last_modified_by_id": (
            str(conflict.current_last_modified_by_id)
            if getattr(conflict, "current_last_modified_by_id", None)
            else None
        ),
        "resolution": getattr(conflict, "resolution", "conflict"),
    }


def _empty_auto_tm_summary() -> AutoTMEnqueueSummary:
    return AutoTMEnqueueSummary()


def _schedule_auto_tm_processing(
    background_tasks: BackgroundTasks | None,
    summary: AutoTMEnqueueSummary,
) -> None:
    if background_tasks is not None and summary.queued_count > 0:
        background_tasks.add_task(_dispatch_auto_tm_background)


def _notify_tm_collections_changed(
    db: Session,
    collection_ids: list[UUID],
    *,
    source_file_record_id: UUID | None = None,
) -> int:
    queued_count = register_project_collections_rematch_work(
        db,
        collection_ids=collection_ids,
        source_file_record_id=source_file_record_id,
    )
    if queued_count <= 0:
        return 0
    return process_due_auto_tm_rematches(db, force=True)


def _resolve_unconfirmed_segment_status(segment: Segment) -> str:
    if not normalize_text(segment.target_text):
        return "none"

    score = float(segment.score or 0)
    source_text = normalize_match_text(segment.source_text or "")
    matched_source_text = normalize_match_text(segment.matched_source_text or "")
    if score >= 0.999 or (matched_source_text and matched_source_text == source_text):
        return "exact"
    if score > 0 or matched_source_text:
        return "fuzzy"
    return "none"


def _apply_segment_scope_filter(query, scope: str):
    normalized_scope = (scope or "all").strip().lower()
    if normalized_scope == "exact_only":
        return query.filter(Segment.status == "exact")
    if normalized_scope == "fuzzy_only":
        return query.filter(Segment.status == "fuzzy")
    if normalized_scope == "none_only":
        return query.filter(Segment.status == "none")
    if normalized_scope == "confirmed_only":
        return query.filter(Segment.status == "confirmed")
    if normalized_scope == "empty_target":
        return query.filter(func.coalesce(Segment.target_text, "") == "")
    return query


def _apply_segment_text_filters(
    query,
    source_query: str | None,
    target_query: str | None,
    source_exclude: str | None = None,
    target_exclude: str | None = None,
    case_sensitive: bool = False,
):
    def text_contains(column, pattern: str):
        return column.like(pattern) if case_sensitive else column.ilike(pattern)

    source_keyword = _normalize_segment_search_keyword(source_query)
    target_keyword = _normalize_segment_search_keyword(target_query)
    if source_keyword:
        source_pattern = f"%{source_keyword}%"
        query = query.filter(
            or_(
                text_contains(Segment.source_text, source_pattern),
                text_contains(Segment.display_text, source_pattern),
            )
        )
    if target_keyword:
        query = query.filter(text_contains(Segment.target_text, f"%{target_keyword}%"))
    for keyword in _split_segment_exclude_keywords(source_exclude):
        source_pattern = f"%{keyword}%"
        query = query.filter(
            and_(
                or_(Segment.source_text.is_(None), ~text_contains(Segment.source_text, source_pattern)),
                or_(Segment.display_text.is_(None), ~text_contains(Segment.display_text, source_pattern)),
            )
        )
    for keyword in _split_segment_exclude_keywords(target_exclude):
        target_pattern = f"%{keyword}%"
        query = query.filter(
            or_(Segment.target_text.is_(None), ~text_contains(Segment.target_text, target_pattern))
        )
    return query


def _normalize_segment_search_keyword(value: str | None) -> str:
    if value is None:
        return ""
    return value


def _split_segment_exclude_keywords(value: str | None) -> list[str]:
    if not value:
        return []
    if not value.strip():
        return [" "]
    keywords = [item.strip() for item in re.split(r"[\s,，]+", value) if item.strip()]
    return list(dict.fromkeys(keywords))


def _normalize_segment_filter_values(*values: Any) -> list[str]:
    normalized: list[str] = []
    for value in values:
        if value is None:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            for part in str(item).split(","):
                filter_value = part.strip().lower()
                if filter_value:
                    normalized.append(filter_value)
    return list(dict.fromkeys(normalized))


def _apply_segment_screening_filters(
    query,
    *,
    status_filters: list[str] | None = None,
    match_filters: list[str] | None = None,
    source_filters: list[str] | None = None,
    workflow_step_ids: list[str] | None = None,
):
    status_values = set(_normalize_segment_filter_values(status_filters))
    if status_values:
        conditions = []
        if "empty_target" in status_values:
            conditions.append(func.coalesce(Segment.target_text, "") == "")
        if "confirmed" in status_values:
            conditions.append(Segment.status == "confirmed")
        if "unconfirmed" in status_values:
            conditions.append(or_(Segment.status.is_(None), Segment.status != "confirmed"))
        if "qa" in status_values:
            conditions.append(
                Segment.qa_issues.any(
                    SegmentQAIssue.status == QA_ISSUE_STATUS_OPEN
                )
            )
        if conditions:
            query = query.filter(or_(*conditions))

    match_values = set(_normalize_segment_filter_values(match_filters))
    if match_values:
        conditions = []
        if "exact" in match_values:
            conditions.append(Segment.status == "exact")
        if "fuzzy" in match_values:
            conditions.append(Segment.status == "fuzzy")
        if "none" in match_values:
            conditions.append(Segment.status == "none")
        if "machine_translation" in match_values:
            conditions.append(Segment.source == "llm")
        if "tm" in match_values:
            conditions.append(Segment.source == "tm")
        if "project_sync" in match_values:
            conditions.append(Segment.source == "project_sync")
        if conditions:
            query = query.filter(or_(*conditions))

    source_values = [
        value
        for value in _normalize_segment_filter_values(source_filters)
        if value in {"tm", "manual", "llm", "project_sync"}
    ]
    if source_values:
        query = query.filter(Segment.source.in_(source_values))

    workflow_step_values: list[UUID] = []
    for value in _normalize_segment_filter_values(workflow_step_ids):
        try:
            workflow_step_values.append(UUID(value))
        except ValueError:
            continue
    if workflow_step_values:
        query = query.filter(Segment.workflow_step_id.in_(workflow_step_values))

    return query


def _order_segment_query(query, file_record: FileRecord | None = None):
    return query.order_by(*get_segment_ordering_for_file_record(file_record))


def _get_segment_display_index_map(
    db: Session,
    file_record_id: UUID,
    segments: list[Segment],
) -> dict[UUID, int]:
    segment_ids = [segment.id for segment in segments]
    if not segment_ids:
        return {}

    file_record = get_file_record_model(db, file_record_id)
    ordered_segments = (
        db.query(
            Segment.id.label("id"),
            func.row_number()
            .over(
                order_by=get_segment_ordering_for_file_record(file_record)
            )
            .label("display_index"),
        )
        .filter(Segment.file_record_id == file_record_id)
        .subquery()
    )
    rows = (
        db.query(ordered_segments.c.id, ordered_segments.c.display_index)
        .filter(ordered_segments.c.id.in_(segment_ids))
        .all()
    )
    return {row.id: int(row.display_index) - 1 for row in rows}


def _apply_segment_display_range_filter(
    db: Session,
    query,
    file_record_id: UUID,
    range_start: int | None,
    range_end: int | None,
):
    if range_start is None and range_end is None:
        return query

    start = range_start if range_start is not None else range_end
    end = range_end if range_end is not None else range_start
    if start is None or end is None:
        return query
    if start > end:
        raise HTTPException(status_code=400, detail="句段范围起始值不能大于结束值。")

    file_record = get_file_record_model(db, file_record_id)
    ordered_segments = (
        db.query(
            Segment.id.label("id"),
            func.row_number()
            .over(
                order_by=get_segment_ordering_for_file_record(file_record)
            )
            .label("display_index"),
        )
        .filter(Segment.file_record_id == file_record_id)
        .subquery()
    )
    return (
        query.join(ordered_segments, ordered_segments.c.id == Segment.id)
        .filter(
            ordered_segments.c.display_index >= start,
            ordered_segments.c.display_index <= end,
        )
    )


def _generate_split_sentence_id(original_id: str, db: Session, file_record_id: UUID) -> str:
    """为拆分生成新的 sentence_id，使用子编号方式（如 "5" → "5.1"）。"""
    base = original_id
    suffix = 1
    while True:
        candidate = f"{base}.{suffix}"
        exists = (
            db.query(Segment.id)
            .filter(Segment.file_record_id == file_record_id, Segment.sentence_id == candidate)
            .first()
        )
        if not exists:
            return candidate
        suffix += 1


def _is_cjk_text(text: str) -> bool:
    """判断文本是否主要为中日韩文字（决定合并时是否加空格）。"""
    if not text:
        return False
    cjk_count = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f')
    return cjk_count > len(text) * 0.3


def _build_preview_render_segments(
    segments: list[Segment],
    mode: str,
    *,
    source_filename: str | None = None,
) -> list[dict]:
    if mode != "target":
        return [_serialize_workbench_segment(segment, source_filename=source_filename) for segment in segments]

    rendered: list[dict] = []
    for segment in segments:
        item = _serialize_workbench_segment(segment, source_filename=source_filename)
        item["display_text"] = segment.target_text or segment.display_text or segment.source_text
        rendered.append(item)
    return rendered


def _get_segment_status_stats(db: Session, file_record_id: UUID) -> dict[str, int]:
    return _get_segment_status_stats_for_query(
        db.query(Segment).filter(Segment.file_record_id == file_record_id)
    )


def _get_segment_status_stats_for_query(query) -> dict[str, int]:
    empty_target_expr = func.coalesce(Segment.target_text, "") == ""
    row = (
        query.with_entities(
            func.count(Segment.id).label("total"),
            func.coalesce(func.sum(case((Segment.status == "exact", 1), else_=0)), 0).label("exact"),
            func.coalesce(func.sum(case((Segment.status == "fuzzy", 1), else_=0)), 0).label("fuzzy"),
            func.coalesce(func.sum(case((Segment.status == "none", 1), else_=0)), 0).label("none"),
            func.coalesce(func.sum(case((Segment.status == "confirmed", 1), else_=0)), 0).label("confirmed"),
            func.coalesce(func.sum(case((empty_target_expr, 1), else_=0)), 0).label("empty_target"),
        )
        .one()
    )
    return {
        "total": int(row.total or 0),
        "exact": int(row.exact or 0),
        "fuzzy": int(row.fuzzy or 0),
        "none": int(row.none or 0),
        "confirmed": int(row.confirmed or 0),
        "empty_target": int(row.empty_target or 0),
    }


def _get_segment_page_sentence_ids(
    db: Session,
    file_record_id: UUID,
    *,
    skip: int,
    limit: int,
    scope: str = "all",
    source_query: str | None = None,
    target_query: str | None = None,
    source_exclude: str | None = None,
    target_exclude: str | None = None,
    case_sensitive: bool = False,
    status_filters: list[str] | None = None,
    match_filters: list[str] | None = None,
    source_filters: list[str] | None = None,
    workflow_step_ids: list[str] | None = None,
) -> list[str]:
    safe_skip = max(skip, 0)
    safe_limit = _normalize_segment_page_limit(limit)
    file_record = get_file_record_model(db, file_record_id)
    query = db.query(Segment.sentence_id).filter(Segment.file_record_id == file_record_id)
    query = _apply_segment_scope_filter(query, scope)
    query = _apply_segment_text_filters(
        query,
        source_query=source_query,
        target_query=target_query,
        source_exclude=source_exclude,
        target_exclude=target_exclude,
        case_sensitive=case_sensitive,
    )
    query = _apply_segment_screening_filters(
        query,
        status_filters=status_filters,
        match_filters=match_filters,
        source_filters=source_filters,
        workflow_step_ids=workflow_step_ids,
    )
    return [
        sentence_id
        for (sentence_id,) in (
            _order_segment_query(query, file_record)
            .offset(safe_skip)
            .limit(safe_limit)
            .all()
        )
        if sentence_id
    ]


def _get_workflow_step_for_file_record(
    db: Session,
    file_record: FileRecord,
    workflow_step_id: UUID,
) -> ProjectWorkflowStep:
    step = (
        db.query(ProjectWorkflowStep)
        .filter(
            ProjectWorkflowStep.id == workflow_step_id,
            ProjectWorkflowStep.project_id == file_record.project_id,
        )
        .first()
    )
    if step is None:
        raise HTTPException(status_code=400, detail="流程阶段不属于当前文件所在项目。")
    return step


def _apply_workflow_transition_filters(
    db: Session,
    query,
    file_record_id: UUID,
    payload: WorkflowTransitionPreviewRequest,
):
    file_record = get_file_record_model(db, file_record_id)
    query = query.filter(
        Segment.file_record_id == file_record_id,
        Segment.workflow_step_id == payload.from_step_id,
    )
    selected_statuses = set(payload.source_statuses or [])
    if selected_statuses:
        query = query.filter(Segment.status.in_(sorted(selected_statuses)))
    elif payload.source_status == "confirmed":
        query = query.filter(Segment.status == "confirmed")
    elif payload.source_status == "unconfirmed":
        query = query.filter(or_(Segment.status.is_(None), Segment.status != "confirmed"))

    if not payload.all_segments:
        range_end = payload.range_end if payload.range_end is not None else payload.range_start
        if payload.range_start > range_end:
            raise HTTPException(status_code=400, detail="句段范围起始值不能大于结束值。")
        ordered_segments = (
            db.query(
                Segment.id.label("id"),
                func.row_number()
                .over(
                    order_by=get_segment_ordering_for_file_record(file_record)
                )
                .label("display_index"),
            )
            .filter(Segment.file_record_id == file_record_id)
            .subquery()
        )
        query = (
            query.join(ordered_segments, ordered_segments.c.id == Segment.id)
            .filter(
                ordered_segments.c.display_index >= payload.range_start,
                ordered_segments.c.display_index <= range_end,
            )
        )
    return query


def _resolve_workflow_transition_context(
    db: Session,
    file_record: FileRecord,
    payload: WorkflowTransitionPreviewRequest,
    current_user: User,
) -> tuple[ProjectWorkflowStep, ProjectWorkflowStep]:
    _assign_file_segments_to_first_workflow_step(db, file_record)
    from_step = _get_workflow_step_for_file_record(db, file_record, payload.from_step_id)
    target_step = _get_workflow_step_for_file_record(db, file_record, payload.target_step_id)
    if from_step.id == target_step.id:
        raise HTTPException(status_code=400, detail="目标流程必须与当前流程不同。")
    if int(from_step.sort_order or 0) == int(target_step.sort_order or 0):
        raise HTTPException(status_code=400, detail="目标流程顺序必须与当前流程不同。")
    if not _can_write_workflow_step(db, file_record, current_user, from_step.id):
        raise HTTPException(status_code=403, detail="当前账号没有推进该来源流程阶段的权限。")
    return from_step, target_step


@router.post("/file-records/{file_record_id}/workflow/transition/preview")
def preview_file_record_workflow_transition(
    file_record_id: UUID,
    payload: WorkflowTransitionPreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在。")
    _require_file_record_read_access(file_record, current_user)
    from_step, target_step = _resolve_workflow_transition_context(db, file_record, payload, current_user)
    query = _apply_workflow_transition_filters(
        db,
        db.query(Segment),
        file_record_id,
        payload,
    )
    matched_count = query.count()
    return {
        "file_record_id": str(file_record_id),
        "from_step": _serialize_workflow_step(from_step),
        "target_step": _serialize_workflow_step(target_step),
        "matched_count": int(matched_count or 0),
        "source_status": payload.source_status,
        "source_statuses": payload.source_statuses,
        "target_status": payload.target_status,
    }


@router.post("/file-records/{file_record_id}/workflow/transition")
def transition_file_record_workflow(
    file_record_id: UUID,
    payload: WorkflowTransitionRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
):
    file_record = _require_file_record_write_access(db, file_record_id, current_user, operation_token)
    from_step, target_step = _resolve_workflow_transition_context(db, file_record, payload, current_user)
    segments = _order_segment_query(
        _apply_workflow_transition_filters(
            db,
            db.query(Segment),
            file_record_id,
            payload,
        ),
        file_record,
    ).all()
    matched_count = len(segments)
    updated_count = 0
    confirmed_segments: list[Segment] = []
    for segment in segments:
        next_status = "confirmed" if payload.target_status == "confirmed" else _resolve_unconfirmed_segment_status(segment)
        if segment.workflow_step_id != target_step.id or segment.status != next_status:
            segment.workflow_step_id = target_step.id
            segment.status = next_status
            segment.last_modified_by_id = current_user.id
            segment.version = int(segment.version or 1) + 1
            updated_count += 1
        if segment.status == "confirmed" and normalize_text(segment.target_text):
            confirmed_segments.append(segment)

    auto_tm_summary = _empty_auto_tm_summary()
    if confirmed_segments:
        auto_tm_summary = enqueue_confirmed_segments_for_auto_tm(
            db,
            file_record=file_record,
            segments=confirmed_segments,
            current_user=current_user,
        )
    if updated_count:
        sync_file_record_status(db, file_record_id)
    db.commit()
    _schedule_auto_tm_processing(background_tasks, auto_tm_summary)
    return {
        "file_record_id": str(file_record_id),
        "from_step": _serialize_workflow_step(from_step),
        "target_step": _serialize_workflow_step(target_step),
        "matched_count": matched_count,
        "updated_count": updated_count,
        "source_status": payload.source_status,
        "source_statuses": payload.source_statuses,
        "target_status": payload.target_status,
        "status_stats": _get_segment_status_stats(db, file_record_id),
        "workflow_progress": _get_file_workflow_progress(db, [file_record_id]).get(file_record_id, []),
        "auto_tm": auto_tm_summary.to_dict(),
    }


@router.get("/file-records/{file_record_id}")
@router.get("/documents/{file_record_id}", include_in_schema=False)
def get_file_record(
    file_record_id: UUID,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取文档详情及片段，支持分页"""
    safe_skip = max(skip, 0)
    safe_limit = _normalize_segment_page_limit(limit)
    result = get_file_record_with_segments(
        db,
        file_record_id,
        skip=safe_skip,
        limit=safe_limit,
    )
    if not result:
        raise HTTPException(status_code=404, detail="\u4efb\u52a1\u4e0d\u5b58\u5728")

    file_record = result["file_record"]
    _require_file_record_read_access(file_record, current_user)
    if clear_stale_file_operation_lock(db, file_record):
        db.commit()
        db.refresh(file_record)
    if backfill_file_record_source_html(db, file_record):
        db.commit()
        result = get_file_record_with_segments(
            db,
            file_record_id,
            skip=safe_skip,
            limit=safe_limit,
        )
        if not result:
            raise HTTPException(status_code=404, detail="\u4efb\u52a1\u4e0d\u5b58\u5728")
        file_record = result["file_record"]
    _assign_file_segments_to_first_workflow_step(db, file_record)
    visible_base_query = _apply_segment_assignment_visibility_filter(
        db,
        db.query(Segment).filter(Segment.file_record_id == file_record_id),
        file_record,
        current_user,
    )
    total_segments = visible_base_query.count()
    segments = (
        _order_segment_query(visible_base_query, file_record)
        .offset(safe_skip)
        .limit(safe_limit)
        .all()
    )
    display_index_map = _get_segment_display_index_map(db, file_record_id, segments)
    source_bytes = load_file_record_source(file_record)
    source_filename = get_file_record_source_filename(file_record)

    # 获取绑定的库信息
    collection_ids = _load_file_record_collection_ids(file_record)
    collection_name = None
    if file_record.collection:
        collection_name = file_record.collection.name
    term_base_name = None
    if file_record.term_base:
        term_base_name = file_record.term_base.name
    term_base_ids = _load_file_record_term_base_ids(file_record)
    term_base_write_ids = _load_file_record_term_base_write_ids(file_record)
    qa_term_base_ids = _load_file_record_qa_term_base_ids(file_record)
    glossary_base_ids = _load_file_record_glossary_base_ids(file_record)
    term_base_names: list[str] = []
    all_bound_term_base_ids = list(dict.fromkeys(term_base_ids + term_base_write_ids + qa_term_base_ids))
    term_base_by_id: dict[UUID, TermBase] = {}
    if all_bound_term_base_ids:
        term_bases = (
            db.query(TermBase)
            .filter(TermBase.id.in_(all_bound_term_base_ids))
            .all()
        )
        term_base_by_id = {term_base.id: term_base for term_base in term_bases}
        term_base_names = [
            term_base_by_id[term_base_id].name
            for term_base_id in term_base_ids
            if term_base_id in term_base_by_id
        ]
        if term_base_name is None and term_base_names:
            term_base_name = term_base_names[0]
    glossary_base_names: list[str] = []
    if glossary_base_ids:
        glossary_bases = (
            db.query(GlossaryBase)
            .filter(GlossaryBase.id.in_(glossary_base_ids))
            .all()
        )
        glossary_base_by_id = {glossary_base.id: glossary_base for glossary_base in glossary_bases}
        glossary_base_names = [
            glossary_base_by_id[glossary_base_id].name
            for glossary_base_id in glossary_base_ids
            if glossary_base_id in glossary_base_by_id
        ]

    project_guidelines = ""
    if file_record.project_id:
        project = db.query(Project).filter(Project.id == file_record.project_id).first()
        if project:
            project_guidelines = project.translation_guidelines or ""

    issue_stats = _get_file_issue_stats(db, [file_record.id]).get(
        file_record.id,
        {"issue_count": 0, "open_issue_count": 0},
    )
    file_assignees = _get_active_file_assignees(db, [file_record.id]).get(file_record.id, [])
    workflow_steps = _load_project_workflow_steps(db, file_record.project_id)
    workflow_step_by_id, writable_workflow_assignments, can_manage = _build_segment_workflow_context(
        db,
        file_record,
        current_user,
    )
    workflow_progress = _get_file_workflow_progress(db, [file_record.id]).get(file_record.id, [])
    target_automatic_numbering_by_sentence_id = _build_target_automatic_numbering_text_map(file_record)
    qa_issues_by_segment_id = _load_workbench_segment_qa_issues(db, segments)

    return {
        "id": file_record.id,
        "project_id": str(file_record.project_id) if file_record.project_id else None,
        "filename": file_record.filename,
        "status": file_record.status,
        "document_parse_mode": getattr(file_record, "document_parse_mode", DOCUMENT_PARSE_MODE_FULL),
        "document_parse_options": _get_file_record_document_parse_options(file_record),
        "document_statistics": get_file_record_document_statistics(file_record),
        **serialize_file_operation_state(file_record),
        "source_language": file_record.source_language,
        "target_language": file_record.target_language,
        "assignee_id": str(file_record.assignee_id) if file_record.assignee_id else None,
        "assignee": _serialize_assignee(file_record.assignee),
        "assignees": _serialize_user_list(file_assignees),
        "assigned_at": file_record.assigned_at.isoformat() if file_record.assigned_at else None,
        "collection_id": str(file_record.collection_id) if file_record.collection_id else None,
        "collection_ids": [str(collection_id) for collection_id in collection_ids],
        "tm_match_threshold": _normalize_tm_match_threshold(getattr(file_record, "tm_match_threshold", None)),
        "collection_name": collection_name,
        "term_base_id": file_record.term_base_id,
        "term_base_name": term_base_name,
        "term_base_ids": [str(term_base_id) for term_base_id in term_base_ids],
        "term_base_names": term_base_names,
        "term_base_write_ids": [str(term_base_id) for term_base_id in term_base_write_ids],
        "term_base_write_names": [
            term_base_by_id[term_base_id].name
            for term_base_id in term_base_write_ids
            if term_base_id in term_base_by_id
        ],
        "qa_term_base_ids": [str(term_base_id) for term_base_id in qa_term_base_ids],
        "qa_term_base_names": [
            term_base_by_id[term_base_id].name
            for term_base_id in qa_term_base_ids
            if term_base_id in term_base_by_id
        ],
        "glossary_base_ids": [str(glossary_base_id) for glossary_base_id in glossary_base_ids],
        "glossary_base_names": glossary_base_names,
        "translation_guidelines": project_guidelines,
        "created_at": file_record.created_at.isoformat(),
        "updated_at": file_record.updated_at.isoformat(),
        "server_time": datetime.now().isoformat(),
        "total_segments": total_segments,
        "skip": safe_skip,
        "limit": safe_limit,
        "source_extension": get_task_file_extension(source_filename),
        "has_source_document": source_bytes is not None,
        "can_export": can_export_task_file(source_filename, has_source_file=source_bytes is not None),
        "can_manage": _can_manage_workflow(current_user),
        "can_write": _can_write_file_record(file_record, current_user, db),
        "workflow_steps": [_serialize_workflow_step(step) for step in workflow_steps],
        "workflow_progress": workflow_progress,
        "issue_count": issue_stats["issue_count"],
        "open_issue_count": issue_stats["open_issue_count"],
        "status_stats": _get_segment_status_stats_for_query(visible_base_query),
        "segments": [
            _serialize_workbench_segment(
                seg,
                display_index=display_index_map.get(seg.id),
                source_filename=source_filename,
                target_automatic_numbering_by_sentence_id=target_automatic_numbering_by_sentence_id,
                qa_issues_by_segment_id=qa_issues_by_segment_id,
                workflow_step_by_id=workflow_step_by_id,
                writable_workflow_assignments=writable_workflow_assignments,
                can_manage=can_manage,
            )
            for seg in segments
        ],
    }


@router.post("/file-records/{file_record_id}/operation-lock")
def acquire_file_record_operation_lock(
    file_record_id: UUID,
    payload: FileOperationLockRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing_file_record = get_file_record_model(db, file_record_id)
    if not existing_file_record:
        raise HTTPException(status_code=404, detail="任务不存在。")
    _require_file_record_work_access(existing_file_record, current_user)
    file_record, token = acquire_file_operation_lock(
        db,
        file_record_id,
        operation=payload.operation,
        current_user=current_user,
    )
    return {
        "id": str(file_record.id),
        "token": token,
        **serialize_file_operation_state(file_record),
    }


@router.patch("/file-records/{file_record_id}/operation-lock")
def heartbeat_file_record_operation_lock(
    file_record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在。")

    _require_file_record_work_access(file_record, current_user)
    heartbeat_file_operation_lock(
        db,
        file_record,
        operation_token=operation_token,
    )
    return {
        "id": str(file_record.id),
        **serialize_file_operation_state(file_record),
    }


@router.delete("/file-records/{file_record_id}/operation-lock")
def release_file_record_operation_lock(
    file_record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在。")

    _require_file_record_work_access(file_record, current_user)
    release_file_operation_lock(
        db,
        file_record,
        operation_token=operation_token,
    )
    return {
        "id": str(file_record.id),
        **serialize_file_operation_state(file_record),
    }


@router.get("/file-records/{file_record_id}/segments")
def get_file_record_segments(
    file_record_id: UUID,
    skip: int = 0,
    limit: int = 100,
    scope: str = "all",
    source_query: str | None = None,
    target_query: str | None = None,
    source_exclude: str | None = None,
    target_exclude: str | None = None,
    search_fuzzy: bool = False,
    case_sensitive: bool = False,
    include_stats: bool = True,
    status_filters: str | None = None,
    status_filters_bracket: list[str] | None = Query(default=None, alias="status_filters[]"),
    match_filters: str | None = None,
    match_filters_bracket: list[str] | None = Query(default=None, alias="match_filters[]"),
    source_filters: str | None = None,
    source_filters_bracket: list[str] | None = Query(default=None, alias="source_filters[]"),
    workflow_step_ids: str | None = None,
    workflow_step_ids_bracket: list[str] | None = Query(default=None, alias="workflow_step_ids[]"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """分页获取工作台句段；搜索/筛选在服务端执行，避免前端加载全文。"""
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    _require_file_record_read_access(file_record, current_user)
    _assign_file_segments_to_first_workflow_step(db, file_record)
    safe_skip = max(skip, 0)
    safe_limit = _normalize_segment_page_limit(limit)
    base_query = _apply_segment_assignment_visibility_filter(
        db,
        db.query(Segment).filter(Segment.file_record_id == file_record_id),
        file_record,
        current_user,
    )
    # total_segments / status_stats 与页码、筛选无关（仅随编辑变化），
    # 翻页时可让前端复用上次结果（include_stats=false），避免每页重复全表聚合。
    total_segments = base_query.count() if include_stats else None
    filtered_query = _apply_segment_scope_filter(base_query, scope)
    filtered_query = _apply_segment_text_filters(
        filtered_query,
        source_query=source_query,
        target_query=target_query,
        source_exclude=source_exclude,
        target_exclude=target_exclude,
        case_sensitive=case_sensitive,
    )
    normalized_status_filters = _normalize_segment_filter_values(status_filters, status_filters_bracket)
    normalized_match_filters = _normalize_segment_filter_values(match_filters, match_filters_bracket)
    normalized_source_filters = _normalize_segment_filter_values(source_filters, source_filters_bracket)
    normalized_workflow_step_ids = _normalize_segment_filter_values(workflow_step_ids, workflow_step_ids_bracket)
    filtered_query = _apply_segment_screening_filters(
        filtered_query,
        status_filters=normalized_status_filters,
        match_filters=normalized_match_filters,
        source_filters=normalized_source_filters,
        workflow_step_ids=normalized_workflow_step_ids,
    )
    matched_segments = filtered_query.count()
    page_segments = (
        _order_segment_query(filtered_query, file_record)
        .offset(safe_skip)
        .limit(safe_limit)
        .all()
    )
    display_index_map = _get_segment_display_index_map(db, file_record_id, page_segments)
    workflow_step_by_id, writable_workflow_assignments, can_manage = _build_segment_workflow_context(
        db,
        file_record,
        current_user,
    )
    target_automatic_numbering_by_sentence_id = _build_target_automatic_numbering_text_map(file_record)
    qa_issues_by_segment_id = _load_workbench_segment_qa_issues(db, page_segments)

    return {
        "file_record_id": str(file_record_id),
        "total_segments": total_segments,
        "matched_segments": matched_segments,
        "status_stats": _get_segment_status_stats_for_query(base_query) if include_stats else None,
        "skip": safe_skip,
        "limit": safe_limit,
        "filters": {
            "scope": scope,
            "source_query": source_query or "",
            "target_query": target_query or "",
            "source_exclude": source_exclude or "",
            "target_exclude": target_exclude or "",
            "search_fuzzy": search_fuzzy,
            "case_sensitive": case_sensitive,
            "status_filters": normalized_status_filters,
            "match_filters": normalized_match_filters,
            "source_filters": normalized_source_filters,
            "workflow_step_ids": normalized_workflow_step_ids,
        },
        "server_time": datetime.now().isoformat(),
        "segments": [
            _serialize_workbench_segment(
                seg,
                display_index=display_index_map.get(seg.id),
                source_filename=get_file_record_source_filename(file_record),
                target_automatic_numbering_by_sentence_id=target_automatic_numbering_by_sentence_id,
                qa_issues_by_segment_id=qa_issues_by_segment_id,
                workflow_step_by_id=workflow_step_by_id,
                writable_workflow_assignments=writable_workflow_assignments,
                can_manage=can_manage,
            )
            for seg in page_segments
        ],
    }


def _parse_segment_change_cursor(cursor: str) -> tuple[datetime, UUID | None]:
    cursor = (cursor or "").strip()
    raw_timestamp = cursor
    raw_id: str | None = None
    if "|" in cursor:
        raw_timestamp, raw_id = cursor.split("|", 1)
    try:
        since_dt = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="since 参数不是有效时间。") from exc
    if not raw_id:
        return since_dt, None
    try:
        return since_dt, UUID(raw_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="since 游标不是有效格式。") from exc


def _format_segment_change_cursor(segment: Segment) -> str:
    updated_at = segment.updated_at or datetime.now()
    return f"{updated_at.isoformat()}|{segment.id}"


@router.get("/file-records/{file_record_id}/segments/changes")
def get_file_record_segment_changes(
    file_record_id: UUID,
    since: str,
    limit: int = 500,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在。")

    _require_file_record_read_access(file_record, current_user)
    _assign_file_segments_to_first_workflow_step(db, file_record)
    since_dt, since_id = _parse_segment_change_cursor(since)
    safe_limit = _normalize_segment_page_limit(limit)
    cursor_condition = Segment.updated_at > since_dt
    if since_id is not None:
        cursor_condition = or_(
            Segment.updated_at > since_dt,
            and_(Segment.updated_at == since_dt, Segment.id > since_id),
        )
    changed_query = _apply_segment_assignment_visibility_filter(
        db,
        db.query(Segment).filter(
            Segment.file_record_id == file_record_id,
            cursor_condition,
        ),
        file_record,
        current_user,
    )
    changed_segments = (
        changed_query
        .order_by(Segment.updated_at.asc(), Segment.id.asc())
        .limit(safe_limit)
        .all()
    )
    server_time = datetime.now().isoformat()
    has_more = len(changed_segments) >= safe_limit
    next_cursor = _format_segment_change_cursor(changed_segments[-1]) if has_more and changed_segments else server_time
    workflow_step_by_id, writable_workflow_assignments, can_manage = _build_segment_workflow_context(
        db,
        file_record,
        current_user,
    )
    target_automatic_numbering_by_sentence_id = _build_target_automatic_numbering_text_map(file_record)
    qa_issues_by_segment_id = _load_workbench_segment_qa_issues(db, changed_segments)
    display_index_map = _get_segment_display_index_map(db, file_record_id, changed_segments)
    return {
        "file_record_id": str(file_record_id),
        "server_time": server_time,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "segments": [
            _serialize_workbench_segment(
                segment,
                display_index=display_index_map.get(segment.id),
                source_filename=get_file_record_source_filename(file_record),
                target_automatic_numbering_by_sentence_id=target_automatic_numbering_by_sentence_id,
                qa_issues_by_segment_id=qa_issues_by_segment_id,
                workflow_step_by_id=workflow_step_by_id,
                writable_workflow_assignments=writable_workflow_assignments,
                can_manage=can_manage,
            )
            for segment in changed_segments
        ],
    }


@router.get("/file-records/{file_record_id}/segments/next-unconfirmed-position")
def get_file_record_next_unconfirmed_segment_position(
    file_record_id: UUID,
    after_sentence_id: str | None = None,
    page_size: int = 100,
    wrap: bool = True,
    scope: str = "all",
    source_query: str | None = None,
    target_query: str | None = None,
    source_exclude: str | None = None,
    target_exclude: str | None = None,
    search_fuzzy: bool = False,
    case_sensitive: bool = False,
    status_filters: str | None = None,
    status_filters_bracket: list[str] | None = Query(default=None, alias="status_filters[]"),
    match_filters: str | None = None,
    match_filters_bracket: list[str] | None = Query(default=None, alias="match_filters[]"),
    source_filters: str | None = None,
    source_filters_bracket: list[str] | None = Query(default=None, alias="source_filters[]"),
    workflow_step_ids: str | None = None,
    workflow_step_ids_bracket: list[str] | None = Query(default=None, alias="workflow_step_ids[]"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在。")

    _require_file_record_read_access(file_record, current_user)
    _assign_file_segments_to_first_workflow_step(db, file_record)
    safe_page_size = _normalize_segment_page_limit(page_size)
    visible_base_query = _apply_segment_assignment_visibility_filter(
        db,
        db.query(Segment).filter(Segment.file_record_id == file_record_id),
        file_record,
        current_user,
    )

    after_position = 0
    if after_sentence_id:
        after_segment = visible_base_query.filter(Segment.sentence_id == after_sentence_id).first()
        if not after_segment:
            raise HTTPException(status_code=404, detail="句段不存在。")
        after_position = _get_segment_display_index_map(db, file_record_id, [after_segment]).get(after_segment.id, 0) + 1

    filtered_query = _apply_segment_scope_filter(visible_base_query, scope)
    filtered_query = _apply_segment_text_filters(
        filtered_query,
        source_query=source_query,
        target_query=target_query,
        source_exclude=source_exclude,
        target_exclude=target_exclude,
        case_sensitive=case_sensitive,
    )
    filtered_query = _apply_segment_screening_filters(
        filtered_query,
        status_filters=_normalize_segment_filter_values(status_filters, status_filters_bracket),
        match_filters=_normalize_segment_filter_values(match_filters, match_filters_bracket),
        source_filters=_normalize_segment_filter_values(source_filters, source_filters_bracket),
        workflow_step_ids=_normalize_segment_filter_values(workflow_step_ids, workflow_step_ids_bracket),
    )

    filtered_segments = _order_segment_query(
        filtered_query.with_entities(
            Segment.id,
            Segment.sentence_id,
            Segment.status,
        ),
        file_record,
    ).all()
    display_index_map = _get_segment_display_index_map(db, file_record_id, filtered_segments)

    def is_target_unconfirmed(item: Any) -> bool:
        return getattr(item, "status", None) != "confirmed"

    def get_display_position(item: Any, fallback_index: int) -> int:
        return display_index_map.get(item.id, fallback_index) + 1

    row: Any | None = None
    filtered_index = -1
    for index, item in enumerate(filtered_segments):
        if is_target_unconfirmed(item) and get_display_position(item, index) > after_position:
            row = item
            filtered_index = index
            break
    wrapped = False
    if not row and wrap:
        for index, item in enumerate(filtered_segments):
            if is_target_unconfirmed(item) and get_display_position(item, index) <= after_position:
                row = item
                filtered_index = index
                wrapped = True
                break

    if not row:
        return {"target": None, "wrapped": False}

    filtered_index = max(filtered_index, 0)
    display_position = get_display_position(row, filtered_index)
    return {
        "target": {
            "file_record_id": str(file_record_id),
            "sentence_id": row.sentence_id,
            "segment_id": str(row.id),
            "index": filtered_index,
            "display_index": display_position,
            "page": (filtered_index // safe_page_size) + 1,
            "page_size": safe_page_size,
            "page_index": filtered_index % safe_page_size,
        },
        "wrapped": wrapped,
    }


@router.get("/file-records/{file_record_id}/segments/{sentence_id}/position")
def get_file_record_segment_position(
    file_record_id: UUID,
    sentence_id: str,
    page_size: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在。")

    _require_file_record_read_access(file_record, current_user)
    _assign_file_segments_to_first_workflow_step(db, file_record)
    safe_page_size = _normalize_segment_page_limit(page_size)
    visible_base_query = _apply_segment_assignment_visibility_filter(
        db,
        db.query(Segment).filter(Segment.file_record_id == file_record_id),
        file_record,
        current_user,
    )
    ordered_segments = (
        visible_base_query.with_entities(
            Segment.id.label("id"),
            Segment.sentence_id.label("sentence_id"),
            func.row_number()
            .over(
                order_by=get_segment_ordering_for_file_record(file_record)
            )
            .label("position"),
        )
        .subquery()
    )
    row = (
        db.query(
            ordered_segments.c.id,
            ordered_segments.c.sentence_id,
            ordered_segments.c.position,
        )
        .filter(ordered_segments.c.sentence_id == sentence_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="句段不存在。")

    index = max(int(row.position) - 1, 0)
    return {
        "file_record_id": str(file_record_id),
        "sentence_id": row.sentence_id,
        "segment_id": str(row.id),
        "index": index,
        "display_index": int(row.position),
        "page": (index // safe_page_size) + 1,
        "page_size": safe_page_size,
        "page_index": index % safe_page_size,
    }


@router.post("/file-records/{file_record_id}/duplicate")
def duplicate_file_record_task(
    file_record_id: UUID,
    payload: FileRecordDuplicateRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """复制一个文件任务，保留源文件和句段原文，不复制译文。"""
    duplicate = duplicate_file_record(
        db,
        file_record_id,
        current_user=current_user,
        filename=payload.filename if payload else None,
    )
    if duplicate is None:
        raise HTTPException(status_code=404, detail="文档不存在。")

    _assign_file_segments_to_first_workflow_step(db, duplicate)
    db.commit()
    db.refresh(duplicate)
    file_stats = _get_file_segment_stats(db, [duplicate.id]).get(
        duplicate.id,
        {"total": 0, "filled": 0, "pretranslated": 0},
    )
    workflow_steps = _load_project_workflow_steps(db, duplicate.project_id)
    workflow_progress = _get_file_workflow_progress(db, [duplicate.id]).get(duplicate.id, [])
    return _build_project_file_payload(
        duplicate,
        total_segments=file_stats["total"],
        translated_segments=file_stats["filled"],
        pretranslated_segments=file_stats["pretranslated"],
        current_user=current_user,
        workflow_steps=workflow_steps,
        workflow_progress=workflow_progress,
    )


@router.patch("/file-records/{file_record_id}/assignment")
def assign_file_record_task(
    file_record_id: UUID,
    payload: FileRecordAssignmentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="任务不存在。")

    assignee: User | None = None
    if payload.assignee_id is not None:
        assignee = get_user_by_id(db, payload.assignee_id)
        if assignee is None or not assignee.is_active or assignee.role != USER_ROLE:
            raise HTTPException(status_code=400, detail="只能指派给启用中的普通译者账号。")

    file_record.assignee_id = assignee.id if assignee else None
    file_record.assigned_by_id = current_user.id if assignee else None
    file_record.assigned_at = datetime.now() if assignee else None
    db.commit()
    db.refresh(file_record)

    file_stats = _get_file_segment_stats(db, [file_record.id]).get(
        file_record.id,
        {"total": 0, "filled": 0, "pretranslated": 0},
    )
    issue_stats = _get_file_issue_stats(db, [file_record.id]).get(file_record.id)
    return _build_project_file_payload(
        file_record,
        total_segments=file_stats["total"],
        translated_segments=file_stats["filled"],
        pretranslated_segments=file_stats["pretranslated"],
        issue_stats=issue_stats,
        current_user=current_user,
    )


@router.get("/file-records/{file_record_id}/preview")
@router.get("/documents/{file_record_id}/preview", include_in_schema=False)
def get_file_record_preview(
    file_record_id: UUID,
    skip: int = 0,
    limit: int = 100,
    mode: Literal["source", "target"] = "source",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    _require_file_record_read_access(file_record, current_user)
    _assign_file_segments_to_first_workflow_step(db, file_record)
    source_filename = get_file_record_source_filename(file_record)
    safe_skip = max(skip, 0)
    safe_limit = _normalize_segment_page_limit(limit)
    visible_query = _apply_segment_assignment_visibility_filter(
        db,
        db.query(Segment).filter(Segment.file_record_id == file_record_id),
        file_record,
        current_user,
    )
    page_segments = (
        _order_segment_query(visible_query, file_record)
        .offset(safe_skip)
        .limit(safe_limit)
        .all()
    )
    render_segments = _build_preview_render_segments(
        page_segments,
        mode,
        source_filename=source_filename,
    )
    preview_html = build_task_preview_html(
        filename=source_filename,
        segments=render_segments,
        source_bytes=None,
        document_parse_mode=getattr(file_record, "document_parse_mode", DOCUMENT_PARSE_MODE_FULL),
        document_parse_options=_get_file_record_document_parse_options(file_record),
    )

    return {
        "id": str(file_record.id),
        "filename": file_record.filename,
        "source_extension": get_task_file_extension(source_filename),
        "supports_preview": bool(preview_html),
        "preview_html": preview_html,
        "preview_mode": "window",
        "render_mode": mode,
        "skip": safe_skip,
        "limit": safe_limit,
        "supports_full_preview": False,
    }


@router.get("/file-records/{file_record_id}/segments/{segment_ref}/tm-candidates")
def get_segment_tm_candidates(
    file_record_id: UUID,
    segment_ref: str,
    threshold: float | None = None,
    max_candidates: int = 5,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取指定句段的 TM 匹配候选列表，兼容句段 UUID 和 sentence_id。"""
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    _require_file_record_read_access(file_record, current_user)
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

    # 优先使用 collection_ids_json（多记忆库），回退到 collection_id（单记忆库）
    collection_ids: list[UUID] = []
    if file_record.collection_ids_json and file_record.collection_ids_json != "[]":
        try:
            parsed_ids = json.loads(file_record.collection_ids_json)
            collection_ids = [UUID(cid) for cid in parsed_ids if cid]
        except (json.JSONDecodeError, ValueError):
            pass
    if not collection_ids and file_record.collection_id:
        collection_ids = [file_record.collection_id]

    candidates = get_tm_candidates_for_text(
        db=db,
        source_text=segment.source_text,
        similarity_threshold=_normalize_tm_match_threshold(
            threshold if threshold is not None else getattr(file_record, "tm_match_threshold", None),
        ),
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


def _filter_tm_rematch_segments(
    segments: list[Segment],
    *,
    skip_confirmed: bool,
) -> tuple[list[Segment], int]:
    if not skip_confirmed:
        return segments, 0

    matchable_segments: list[Segment] = []
    skipped_count = 0
    for segment in segments:
        if segment.status == "confirmed":
            skipped_count += 1
            continue
        matchable_segments.append(segment)
    return matchable_segments, skipped_count


@router.post("/file-records/{file_record_id}/rematch")
def rematch_file_record(
    file_record_id: UUID,
    payload: RematchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    _require_file_record_work_access(file_record, current_user)
    ensure_file_record_write_allowed(db, file_record, operation_token=operation_token)
    selected_collection_ids = _require_selected_collection_ids(
        _validate_collection_ids(db, payload.collection_ids)
    )
    source_language, target_language = _resolve_file_record_language_pair(file_record)
    collections = (
        db.query(MemoryBase)
        .filter(MemoryBase.id.in_(selected_collection_ids))
        .all()
    )
    for collection in collections:
        _ensure_resource_language_pair_matches(collection, source_language, target_language, "记忆库")

    threshold = float(payload.threshold)
    if threshold < 0.5 or threshold > 1:
        raise HTTPException(status_code=400, detail="TM 匹配阈值必须在 0.50 到 1.00 之间。")
    threshold = round(threshold, 2)
    tm_match_signature = build_tm_match_signature(
        db,
        file_record_id=file_record_id,
        collection_ids=selected_collection_ids,
        threshold=threshold,
        skip_confirmed=payload.skip_confirmed,
        overwrite_fuzzy=payload.overwrite_fuzzy,
        auto_confirm_exact=payload.auto_confirm_exact,
    )

    segments = list_segments_for_file_record(db, file_record_id)
    segments = _filter_writable_segments(db, file_record, current_user, segments)
    if not segments:
        return {"exact": 0, "fuzzy": 0, "skipped": 0, "updated": 0}

    matchable_segments, skipped_count = _filter_tm_rematch_segments(
        segments,
        skip_confirmed=payload.skip_confirmed,
    )
    if matchable_segments:
        source_sentences = [segment.source_text for segment in matchable_segments]
        auxiliary_sentences = [segment.display_text for segment in matchable_segments]
        matches, _ = match_sentences_with_stats(
            db=db,
            sentences=source_sentences,
            auxiliary_sentences=auxiliary_sentences,
            similarity_threshold=threshold,
            collection_ids=selected_collection_ids,
        )
    else:
        matches = []

    exact_count = 0
    fuzzy_count = 0
    updated_count = 0
    clean_numbering = is_word_document_filename(file_record.filename)

    for segment, match in zip(matchable_segments, matches, strict=False):
        before = (
            segment.target_text,
            segment.status,
            segment.score,
            segment.source,
            segment.matched_source_text,
            segment.matched_collection_name,
            segment.matched_creator_name,
            segment.matched_created_at,
            segment.matched_updated_at,
        )

        segment.score = float(match.score or 0)
        segment.matched_source_text = match.matched_source_text
        segment.matched_collection_name = match.matched_collection_name
        segment.matched_creator_name = match.matched_creator_name
        segment.matched_created_at = _parse_optional_datetime(match.matched_created_at)
        segment.matched_updated_at = _parse_optional_datetime(match.matched_updated_at)

        if match.status == "exact" and match.target_text is not None:
            target_text = (
                strip_automatic_numbering_prefix(
                    match.target_text,
                    source_text=segment.source_text,
                    display_text=segment.display_text,
                    reference_texts=[match.matched_source_text],
                )
                if clean_numbering
                else match.target_text
            )
            if not normalize_text(target_text):
                skipped_count += 1
                continue
            segment.target_text = target_text
            segment.source = "tm"
            segment.status = "confirmed" if payload.auto_confirm_exact else "exact"
            exact_count += 1
        elif match.status == "fuzzy" and match.target_text is not None:
            can_overwrite = payload.overwrite_fuzzy or (
                segment.status in {"none", "fuzzy"} and segment.source != "manual"
            )
            if can_overwrite:
                target_text = (
                    strip_automatic_numbering_prefix(
                        match.target_text,
                        source_text=segment.source_text,
                        display_text=segment.display_text,
                        reference_texts=[match.matched_source_text],
                    )
                    if clean_numbering
                    else match.target_text
                )
                if not normalize_text(target_text):
                    skipped_count += 1
                    continue
                segment.target_text = target_text
                segment.source = "tm"
                segment.status = "fuzzy"
                fuzzy_count += 1

        after = (
            segment.target_text,
            segment.status,
            segment.score,
            segment.source,
            segment.matched_source_text,
            segment.matched_collection_name,
            segment.matched_creator_name,
            segment.matched_created_at,
            segment.matched_updated_at,
        )
        if before != after:
            segment.last_modified_by_id = current_user.id
            segment.version = int(segment.version or 1) + 1
            updated_count += 1

    # 保留当前文档绑定的记忆库，供右侧匹配面板查询 TM 候选。
    if selected_collection_ids:
        file_record.collection_id = selected_collection_ids[0]
        file_record.collection_ids_json = json.dumps([str(cid) for cid in selected_collection_ids])
    file_record.tm_match_threshold = threshold
    mark_tm_match_signature_current(file_record, tm_match_signature)

    db.commit()
    return {
        "exact": exact_count,
        "fuzzy": fuzzy_count,
        "skipped": skipped_count,
        "updated": updated_count,
    }


@router.patch("/file-records/{file_record_id}/bindings")
def patch_file_record_bindings(
    file_record_id: UUID,
    payload: FileRecordBindingsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
    refresh_tm_matches: bool = Query(default=True, include_in_schema=False),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    _require_file_record_work_access(file_record, current_user)
    ensure_file_record_write_allowed(db, file_record, operation_token=operation_token)
    source_language, target_language = _resolve_file_record_language_pair(file_record)
    before_collection_binding = (
        tuple(_load_file_record_collection_ids(file_record)),
        file_record.collection_id,
        _normalize_tm_match_threshold(getattr(file_record, "tm_match_threshold", None)),
    )

    if "collection_ids" in payload.model_fields_set:
        selected_collection_ids = _validate_collection_ids(db, payload.collection_ids) or []
        for collection_id in selected_collection_ids:
            collection = _get_collection_or_404(db, collection_id)
            _ensure_resource_language_pair_matches(collection, source_language, target_language, "记忆库")
        if "collection_id" in payload.model_fields_set:
            if payload.collection_id is not None and payload.collection_id not in selected_collection_ids:
                raise HTTPException(status_code=400, detail="主写入记忆库必须包含在绑定记忆库列表中。")
            primary_collection_id = payload.collection_id
        else:
            primary_collection_id = file_record.collection_id if file_record.collection_id in selected_collection_ids else None
            if primary_collection_id is None and selected_collection_ids:
                primary_collection_id = selected_collection_ids[0]
        _store_file_record_collection_ids(file_record, selected_collection_ids)
        file_record.collection_id = primary_collection_id
    elif "collection_id" in payload.model_fields_set:
        if payload.collection_id is None:
            file_record.collection_id = None
            file_record.collection_ids_json = "[]"
        else:
            collection = _get_collection_or_404(db, payload.collection_id)
            _ensure_resource_language_pair_matches(collection, source_language, target_language, "记忆库")
            file_record.collection_id = payload.collection_id
            # 同步更新 collection_ids_json，保持单记忆库场景的一致性
            file_record.collection_ids_json = json.dumps([str(payload.collection_id)])

    if "tm_match_threshold" in payload.model_fields_set:
        file_record.tm_match_threshold = _normalize_tm_match_threshold(payload.tm_match_threshold)

    if "term_base_ids" in payload.model_fields_set:
        term_bases = _validate_term_base_ids(db, payload.term_base_ids)
        for term_base in term_bases:
            _ensure_resource_language_pair_matches(term_base, source_language, target_language, "术语库")
        _store_file_record_term_base_ids(file_record, [term_base.id for term_base in term_bases])
    elif "term_base_id" in payload.model_fields_set:
        if payload.term_base_id is None:
            _store_file_record_term_base_ids(file_record, [])
        else:
            term_base = db.query(TermBase).filter(TermBase.id == payload.term_base_id).first()
            if not term_base:
                raise HTTPException(status_code=404, detail="术语库不存在。")
            _ensure_resource_language_pair_matches(term_base, source_language, target_language, "术语库")
            _store_file_record_term_base_ids(file_record, [payload.term_base_id])

    enabled_term_base_ids = set(_load_file_record_term_base_ids(file_record))
    if "term_base_write_ids" in payload.model_fields_set:
        term_bases = _validate_term_base_ids(db, payload.term_base_write_ids)
        write_ids = [term_base.id for term_base in term_bases]
        for term_base in term_bases:
            _ensure_resource_language_pair_matches(term_base, source_language, target_language, "术语库")
        if not set(write_ids).issubset(enabled_term_base_ids):
            raise HTTPException(status_code=400, detail="写入术语库必须先启用。")
        _store_file_record_term_base_write_ids(file_record, write_ids)

    if "qa_term_base_ids" in payload.model_fields_set:
        term_bases = _validate_term_base_ids(db, payload.qa_term_base_ids)
        qa_ids = [term_base.id for term_base in term_bases]
        for term_base in term_bases:
            _ensure_resource_language_pair_matches(term_base, source_language, target_language, "术语库")
        if not set(qa_ids).issubset(enabled_term_base_ids):
            raise HTTPException(status_code=400, detail="用于 QA 的术语库必须先启用。")
        _store_file_record_qa_term_base_ids(file_record, qa_ids)

    if "glossary_base_ids" in payload.model_fields_set:
        glossary_bases = _validate_glossary_base_ids(db, payload.glossary_base_ids)
        for glossary_base in glossary_bases:
            _ensure_resource_language_pair_matches(glossary_base, source_language, target_language, "词汇表")
        _store_file_record_glossary_base_ids(
            file_record,
            [glossary_base.id for glossary_base in glossary_bases],
        )

    after_collection_binding = (
        tuple(_load_file_record_collection_ids(file_record)),
        file_record.collection_id,
        _normalize_tm_match_threshold(getattr(file_record, "tm_match_threshold", None)),
    )
    if refresh_tm_matches and before_collection_binding != after_collection_binding:
        db.flush()
        refresh_unconfirmed_segment_matches(
            db,
            file_record_id=file_record.id,
            collection_ids=_load_file_record_collection_ids(file_record),
        )

    db.commit()
    db.refresh(file_record)
    collection_ids = _load_file_record_collection_ids(file_record)
    term_base_ids = _load_file_record_term_base_ids(file_record)
    term_base_write_ids = _load_file_record_term_base_write_ids(file_record)
    qa_term_base_ids = _load_file_record_qa_term_base_ids(file_record)
    glossary_base_ids = _load_file_record_glossary_base_ids(file_record)
    return {
        "id": str(file_record.id),
        "collection_id": str(file_record.collection_id) if file_record.collection_id else None,
        "collection_ids": [str(collection_id) for collection_id in collection_ids],
        "tm_match_threshold": _normalize_tm_match_threshold(getattr(file_record, "tm_match_threshold", None)),
        "term_base_id": str(file_record.term_base_id) if file_record.term_base_id else None,
        "term_base_ids": [str(term_base_id) for term_base_id in term_base_ids],
        "term_base_write_ids": [str(term_base_id) for term_base_id in term_base_write_ids],
        "qa_term_base_ids": [str(term_base_id) for term_base_id in qa_term_base_ids],
        "glossary_base_ids": [str(glossary_base_id) for glossary_base_id in glossary_base_ids],
    }


def _require_file_export_task_read_access(
    task: FileExportTask,
    current_user: User,
) -> None:
    if task.file_record is None:
        raise HTTPException(status_code=404, detail="File record not found.")
    _require_file_record_read_access(task.file_record, current_user)


def _queue_file_record_export_for_current_user(
    *,
    file_record_id: UUID,
    export_type: str,
    db: Session,
    current_user: User,
) -> dict[str, Any]:
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File record not found.")

    _require_file_record_read_access(file_record, current_user)
    raw_bytes = load_file_record_source(file_record)
    from app.services.adapters import get_export_options_for_file

    export_option_ids = {option.get("id") for option in get_export_options_for_file(file_record.filename)}
    if export_type not in export_option_ids:
        raise HTTPException(status_code=400, detail="Current file format does not support this export type.")

    if export_type == "source" and raw_bytes is None:
        raise HTTPException(status_code=400, detail="The source file is unavailable.")

    if export_type == "original" and not can_export_task_file(
        get_file_record_source_filename(file_record),
        has_source_file=raw_bytes is not None,
    ):
        raise HTTPException(status_code=400, detail="Current file format does not support original export yet.")

    return queue_file_export(
        db,
        file_record_id=file_record_id,
        export_type=export_type,
        current_user=current_user,
    )


def _load_project_file_zip_export_files(
    *,
    db: Session,
    project: Project,
    file_ids: list[UUID],
    current_user: User,
) -> list[FileRecord]:
    normalized_file_ids = list(dict.fromkeys(file_ids))
    if len(normalized_file_ids) < 2:
        raise HTTPException(status_code=400, detail="请至少选择两个文件导出为压缩包。")

    files = (
        db.query(FileRecord)
        .filter(
            FileRecord.project_id == project.id,
            FileRecord.id.in_(normalized_file_ids),
        )
        .all()
    )
    file_by_id = {file_record.id: file_record for file_record in files}
    ordered_files: list[FileRecord] = []
    for file_id in normalized_file_ids:
        file_record = file_by_id.get(file_id)
        if file_record is None:
            raise HTTPException(status_code=404, detail="部分文件不存在或不属于当前项目。")
        if not _can_read_file_record(file_record, current_user, db):
            raise HTTPException(status_code=404, detail="部分文件不存在或未分配给当前用户。")
        raw_bytes = load_file_record_source(file_record)
        if not can_export_task_file(
            get_file_record_source_filename(file_record),
            has_source_file=raw_bytes is not None,
        ):
            raise HTTPException(
                status_code=400,
                detail=f"文件“{file_record.filename}”暂不支持目标文件导出，无法打包为压缩包。",
            )
        ordered_files.append(file_record)
    return ordered_files


def _require_project_file_zip_export_task_read_access(
    task: dict[str, Any],
    *,
    db: Session,
    current_user: User,
) -> None:
    try:
        project_id = UUID(str(task.get("project_id") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="压缩包导出任务不存在。") from exc

    project = _get_project_or_404(db, project_id)
    _require_project_read_access(project, current_user, db)


@router.post("/projects/{project_id}/file-export-zip-tasks")
def create_project_file_export_zip_task(
    project_id: UUID,
    payload: ProjectFileZipExportPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_project_or_404(db, project_id)
    _require_project_read_access(project, current_user, db)
    files = _load_project_file_zip_export_files(
        db=db,
        project=project,
        file_ids=payload.file_ids,
        current_user=current_user,
    )
    task = queue_project_file_zip_export(
        project_id=project.id,
        project_name=getattr(project, "name", None) or getattr(project, "filename", None) or "项目",
        file_ids=[file_record.id for file_record in files],
    )
    return JSONResponse(status_code=202, content=task)


@router.get("/projects/file-export-zip-tasks/{task_id}")
def get_project_file_export_zip_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = ensure_project_file_zip_export_task_status(task_id)
    _require_project_file_zip_export_task_read_access(task, db=db, current_user=current_user)
    return task


@router.get("/projects/file-export-zip-tasks/{task_id}/download")
def download_project_file_export_zip_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = ensure_project_file_zip_export_task_status(task_id)
    _require_project_file_zip_export_task_read_access(task, db=db, current_user=current_user)
    return build_project_file_zip_export_download_response(task_id)


# ---------------------------------------------------------------------------
# 项目"合并视图"（merge-views）
# 合并视图只持久化"哪些 file_records 组成一个编辑视图"。句段仍归属各自
# file_record，保存/导出复用按文件的现有接口；此处仅提供视图 CRUD 与
# 聚合读取（把多文件句段按"文件顺序 + 文件内顺序"展平，每段附 file_record_id）。
# ---------------------------------------------------------------------------


class MergeViewCreatePayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="视图名称")
    file_ids: list[UUID] = Field(..., min_length=2, description="组成视图的文件 id，至少 2 个")


class MergeViewUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    file_ids: list[UUID] | None = Field(default=None, min_length=2)


def _require_merge_view_manage_access(
    db: Session, view: ProjectMergeView, current_user: User
) -> None:
    """管理合并视图需具备项目读权限，且为管理员或视图创建者。"""
    project = db.query(Project).filter(Project.id == view.project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="视图不存在。")
    _require_project_read_access(project, current_user, db)
    if not _can_manage_merge_view(view, current_user):
        raise HTTPException(status_code=403, detail="仅管理员或视图创建者可操作合并视图。")


def _can_manage_merge_view(view: ProjectMergeView, current_user: User | None) -> bool:
    if current_user is None:
        return False
    if _can_manage_workflow(current_user):
        return True
    return view.creator_id is not None and getattr(current_user, "id", None) == view.creator_id


def _get_visible_merge_view_files(
    db: Session,
    view: ProjectMergeView,
    current_user: User,
) -> list[FileRecord]:
    files = load_view_file_records(db, view)
    return _visible_project_files(files, current_user, db)


def _serialize_merge_view_summary_for_user(
    db: Session,
    view: ProjectMergeView,
    current_user: User,
    *,
    visible_files: list[FileRecord] | None = None,
) -> dict:
    payload = serialize_merge_view_summary(db, view, current_user=current_user)
    if visible_files is None:
        visible_files = _get_visible_merge_view_files(db, view, current_user)
    if not can_access_all_projects(current_user):
        visible_ids = [str(file_record.id) for file_record in visible_files]
        payload["file_ids"] = visible_ids
        payload["file_count"] = len(visible_ids)
        payload["available_file_count"] = len(visible_ids)
    payload["can_manage"] = _can_manage_merge_view(view, current_user)
    payload["can_open"] = len(visible_files) >= 2
    return payload


def _require_merge_view_openable_files(files: list[FileRecord], current_user: User) -> None:
    if not files:
        raise HTTPException(status_code=404, detail="视图不存在或没有可访问文件。")
    if not can_access_all_projects(current_user) and len(files) < 2:
        raise HTTPException(status_code=404, detail="视图不存在或没有足够的可访问文件。")


def _get_merge_view_or_404(db: Session, view_id: UUID) -> ProjectMergeView:
    view = db.query(ProjectMergeView).filter(ProjectMergeView.id == view_id).first()
    if view is None:
        raise HTTPException(status_code=404, detail="视图不存在。")
    return view


def _get_merge_view_context(
    db: Session,
    view_id: UUID,
    current_user: User,
) -> tuple[ProjectMergeView, Project, list[FileRecord]]:
    """加载合并视图及当前用户可见文件，并统一执行读权限校验。"""
    view = _get_merge_view_or_404(db, view_id)
    project = _get_project_or_404(db, view.project_id)
    _require_project_read_access(project, current_user, db)
    files = _get_visible_merge_view_files(db, view, current_user)
    _require_merge_view_openable_files(files, current_user)
    return view, project, files


def _validate_and_order_view_file_ids(
    db: Session, project: Project, file_ids: list[UUID], current_user: User
) -> list[UUID]:
    """校验文件归属该项目且当前用户可读，去重保序返回。"""
    ordered = normalize_file_ids(file_ids)
    if len(ordered) < 2:
        raise HTTPException(status_code=400, detail="合并视图至少需要选择两个文件。")
    files = (
        db.query(FileRecord)
        .filter(
            FileRecord.project_id == project.id,
            FileRecord.id.in_(ordered),
        )
        .all()
    )
    file_by_id = {f.id: f for f in files}
    for fid in ordered:
        file_record = file_by_id.get(fid)
        if file_record is None:
            raise HTTPException(status_code=400, detail="部分文件不存在或不属于当前项目。")
        if not _can_read_file_record(file_record, current_user, db):
            raise HTTPException(status_code=404, detail="部分文件不存在或未分配给当前用户。")
    return ordered


@router.get("/projects/{project_id}/merge-views")
def list_project_merge_views(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出项目的合并视图（含文件数、creator、时间）。需项目读权限。"""
    project = _get_project_or_404(db, project_id)
    _require_project_read_access(project, current_user, db)
    views = (
        db.query(ProjectMergeView)
        .filter(ProjectMergeView.project_id == project.id)
        .order_by(ProjectMergeView.created_at.desc())
        .all()
    )
    items = []
    for view in views:
        visible_files = _get_visible_merge_view_files(db, view, current_user)
        if not can_access_all_projects(current_user) and len(visible_files) < 2:
            continue
        items.append(
            _serialize_merge_view_summary_for_user(
                db,
                view,
                current_user,
                visible_files=visible_files,
            )
        )
    return {
        "project_id": str(project.id),
        "items": items,
    }


@router.post("/projects/{project_id}/merge-views")
def create_project_merge_view(
    project_id: UUID,
    payload: MergeViewCreatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建合并视图。需项目读权限；校验文件归属该项目、去重保序且当前用户可读。"""
    project = _get_project_or_404(db, project_id)
    _require_project_read_access(project, current_user, db)
    ordered = _validate_and_order_view_file_ids(db, project, payload.file_ids, current_user)
    view = ProjectMergeView(
        project_id=project.id,
        name=payload.name.strip(),
        file_ids=serialize_file_ids(ordered),
        creator_id=current_user.id,
    )
    db.add(view)
    db.commit()
    db.refresh(view)
    return _serialize_merge_view_summary_for_user(db, view, current_user)


@router.get("/merge-views")
def list_visible_merge_views(
    project_id: UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出当前用户可打开的合并视图；普通用户按已授权文件过滤。"""
    query = (
        db.query(ProjectMergeView, Project)
        .join(Project, Project.id == ProjectMergeView.project_id)
    )
    if project_id is not None:
        project = _get_project_or_404(db, project_id)
        _require_project_read_access(project, current_user, db)
        query = query.filter(ProjectMergeView.project_id == project.id)
    elif not can_access_all_projects(current_user):
        assigned_project_ids = (
            db.query(ProjectAssignment.project_id)
            .filter(
                ProjectAssignment.assignee_id == current_user.id,
                ProjectAssignment.status == ASSIGNMENT_STATUS_ACTIVE,
            )
            .distinct()
        )
        query = query.filter(ProjectMergeView.project_id.in_(assigned_project_ids))

    rows = (
        query.order_by(ProjectMergeView.updated_at.desc(), ProjectMergeView.created_at.desc())
        .all()
    )
    items: list[dict[str, Any]] = []
    for view, project in rows:
        visible_files = _get_visible_merge_view_files(db, view, current_user)
        if not can_access_all_projects(current_user) and len(visible_files) < 2:
            continue
        payload = _serialize_merge_view_summary_for_user(
            db,
            view,
            current_user,
            visible_files=visible_files,
        )
        payload["project_name"] = project.name
        items.append(payload)
    return {"items": items}


@router.get("/merge-views/{view_id}")
def get_merge_view_detail(
    view_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """视图详情：name + 按顺序的文件元数据（含 status_stats）+ 合计。需项目读权限。"""
    view = _get_merge_view_or_404(db, view_id)
    project = _get_project_or_404(db, view.project_id)
    _require_project_read_access(project, current_user, db)
    files = _get_visible_merge_view_files(db, view, current_user)
    _require_merge_view_openable_files(files, current_user)
    payload = serialize_merge_view_detail(db, view, files)
    if not can_access_all_projects(current_user):
        visible_ids = [str(file_record.id) for file_record in files]
        payload["file_ids"] = visible_ids
        payload["total_files"] = len(files)
    payload["can_manage"] = _can_manage_merge_view(view, current_user)
    file_workflow_progress = _get_file_workflow_progress(db, [file_record.id for file_record in files])
    for file_payload, file_record in zip(payload.get("files", []), files):
        file_payload["can_write"] = _can_write_file_record(file_record, current_user, db)
        workflow_progress = file_workflow_progress.get(file_record.id, [])
        file_payload["workflow_progress"] = workflow_progress
        if workflow_progress:
            file_payload["progress"] = _calculate_workflow_overall_progress(
                workflow_progress,
                file_payload.get("progress", 0),
            )
    return payload


@router.patch("/merge-views/{view_id}")
def update_merge_view(
    view_id: UUID,
    payload: MergeViewUpdatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """重命名或更新 file_ids。需项目管理权限。"""
    view = _get_merge_view_or_404(db, view_id)
    _require_merge_view_manage_access(db, view, current_user)
    if payload.name is not None:
        view.name = payload.name.strip()
    if payload.file_ids is not None:
        project = _get_project_or_404(db, view.project_id)
        ordered = _validate_and_order_view_file_ids(db, project, payload.file_ids, current_user)
        view.file_ids = serialize_file_ids(ordered)
    db.commit()
    db.refresh(view)
    return _serialize_merge_view_summary_for_user(db, view, current_user)


@router.delete("/merge-views/{view_id}")
def delete_merge_view(
    view_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除视图记录（仅删视图，不动文件/句段）。需项目管理权限。"""
    view = _get_merge_view_or_404(db, view_id)
    _require_merge_view_manage_access(db, view, current_user)
    db.delete(view)
    db.commit()
    return JSONResponse(status_code=200, content={"deleted": True, "id": str(view_id)})


@router.post("/merge-views/{view_id}/term-qa-reports")
def create_merge_view_term_qa_report(
    view_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """为当前合并视图文件组合生成一份术语 QA 汇总报告。"""
    _view, project, files = _get_merge_view_context(db, view_id, current_user)
    file_ids = [file_record.id for file_record in files]
    report = _create_term_qa_report(
        db,
        project_id=project.id,
        files=files,
        current_user=current_user,
        scope="merge_view",
    )
    items = _load_term_qa_report_items_for_files(db, report.id, file_ids)
    return _serialize_term_qa_report(report, items)


@router.get("/merge-views/{view_id}/term-qa-reports")
def list_merge_view_term_qa_reports(
    view_id: UUID,
    limit: int = 5,
    include_items: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出当前合并视图文件组合最近生成的术语 QA 报告。"""
    _view, project, files = _get_merge_view_context(db, view_id, current_user)
    file_ids = [file_record.id for file_record in files]
    file_ids_text = serialize_file_ids(file_ids)
    safe_limit = min(max(int(limit), 1), 20)
    reports = (
        db.query(TermQAReport)
        .filter(
            TermQAReport.project_id == project.id,
            TermQAReport.scope == "merge_view",
            TermQAReport.file_ids == file_ids_text,
        )
        .order_by(TermQAReport.created_at.desc(), TermQAReport.id.desc())
        .limit(safe_limit)
        .all()
    )

    items_by_report_id: dict[UUID, list[TermQAReportItem]] = {report.id: [] for report in reports}
    if include_items:
        for report in reports:
            items_by_report_id[report.id] = _load_term_qa_report_items_for_files(db, report.id, file_ids)

    return {
        "items": [
            _serialize_term_qa_report(report, items_by_report_id.get(report.id, []))
            for report in reports
        ],
    }


@router.get("/merge-views/{view_id}/qa-results")
def get_merge_view_qa_result(
    view_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _view, project, files = _get_merge_view_context(db, view_id, current_user)
    return _build_workbench_qa_result(
        db,
        project=project,
        files=files,
        current_user=current_user,
        scope="merge_view",
        generate=False,
    )


@router.post("/merge-views/{view_id}/qa-results")
def create_merge_view_qa_result(
    view_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _view, project, files = _get_merge_view_context(db, view_id, current_user)
    for file_record in files:
        _require_file_record_work_access(file_record, current_user)
    return _build_workbench_qa_result(
        db,
        project=project,
        files=files,
        current_user=current_user,
        scope="merge_view",
        generate=True,
    )


@router.get("/merge-views/{view_id}/qa-results/export-xlsx")
def export_merge_view_qa_result_xlsx(
    view_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _view, project, files = _get_merge_view_context(db, view_id, current_user)
    result = _build_workbench_qa_result(
        db,
        project=project,
        files=files,
        current_user=current_user,
        scope="merge_view",
        generate=False,
    )
    return _build_workbench_qa_xlsx_response(
        result,
        f"merge-view-qa-result-{view_id}.xlsx",
    )


@router.post("/merge-views/{view_id}/number-check-reports")
async def create_merge_view_number_check_report(
    view_id: UUID,
    run_ai: bool = Query(default=True),
    ai_scope: str = Query(default="program_only"),
    provider: str = Query(default="auto"),
    model: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _view, project, files = _get_merge_view_context(db, view_id, current_user)
    report = create_number_check_report(
        db,
        project=project,
        files=files,
        current_user=current_user,
        scope="merge_view",
    )
    if run_ai:
        if ai_scope == "all":
            await run_ai_number_check_all_segments(
                db, report, files, provider=provider, model=model
            )
        elif report.program_issue_count > 0:
            await run_ai_number_check_for_report(db, report, provider=provider, model=model)
    return serialize_number_check_report(report, load_number_check_items(db, report.id))


@router.post("/merge-views/{view_id}/number-check-reports/stream")
async def stream_merge_view_number_check_report(
    view_id: UUID,
    request: Request,
    run_ai: bool = Query(default=True),
    ai_scope: str = Query(default="program_only"),
    provider: str = Query(default="auto"),
    model: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _view, project, files = _get_merge_view_context(db, view_id, current_user)
    return _build_number_check_stream(
        db,
        request,
        project=project,
        files=files,
        current_user=current_user,
        scope="merge_view",
        run_ai=run_ai,
        ai_scope=ai_scope,
        provider=provider,
        model=model,
    )


@router.get("/merge-views/{view_id}/number-check-reports")
def list_merge_view_number_check_reports(
    view_id: UUID,
    limit: int = 1,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _view, _project, files = _get_merge_view_context(db, view_id, current_user)
    file_ids = [str(file_record.id) for file_record in files]
    file_ids_text = json.dumps(file_ids)
    safe_limit = min(max(int(limit), 1), 20)
    reports = (
        db.query(NumberCheckReport)
        .filter(
            NumberCheckReport.scope == "merge_view",
            NumberCheckReport.file_ids == file_ids_text,
        )
        .order_by(NumberCheckReport.created_at.desc(), NumberCheckReport.id.desc())
        .limit(safe_limit)
        .all()
    )
    return {
        "items": [
            serialize_number_check_report(report, load_number_check_items(db, report.id))
            for report in reports
        ]
    }


@router.get("/merge-views/{view_id}/segments/{file_record_id}/{sentence_id}/position")
def get_merge_view_segment_position(
    view_id: UUID,
    file_record_id: UUID,
    sentence_id: str,
    page_size: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """返回句段在合并视图展平序列中的全局页码，用于跨文件 QA 定位。"""
    _view, _project, files = _get_merge_view_context(db, view_id, current_user)
    file_by_id = {file_record.id: file_record for file_record in files}
    if file_record_id not in file_by_id:
        raise HTTPException(status_code=404, detail="句段所属文件不在当前视图中。")

    safe_page_size = _normalize_segment_page_limit(page_size)
    target_file_record = file_by_id[file_record_id]
    previous_count = 0
    for file_record in files:
        if file_record.id == file_record_id:
            break
        previous_count += (
            db.query(func.count(Segment.id))
            .filter(Segment.file_record_id == file_record.id)
            .scalar()
            or 0
        )

    ordered_segments = (
        db.query(
            Segment.id.label("id"),
            Segment.sentence_id.label("sentence_id"),
            func.row_number()
            .over(
                order_by=get_segment_ordering_for_file_record(target_file_record)
            )
            .label("position"),
        )
        .filter(Segment.file_record_id == file_record_id)
        .subquery()
    )
    row = (
        db.query(
            ordered_segments.c.id,
            ordered_segments.c.sentence_id,
            ordered_segments.c.position,
        )
        .filter(ordered_segments.c.sentence_id == sentence_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="句段不存在。")

    local_index = max(int(row.position) - 1, 0)
    index = int(previous_count) + local_index
    return {
        "file_record_id": str(file_record_id),
        "sentence_id": row.sentence_id,
        "segment_id": str(row.id),
        "index": index,
        "display_index": int(row.position),
        "page": (index // safe_page_size) + 1,
        "page_size": safe_page_size,
        "page_index": index % safe_page_size,
    }


@router.get("/merge-views/{view_id}/segments")
def get_merge_view_segments(
    view_id: UUID,
    skip: int = 0,
    limit: int = 100,
    scope: str = "all",
    source_query: str | None = None,
    target_query: str | None = None,
    source_exclude: str | None = None,
    target_exclude: str | None = None,
    search_fuzzy: bool = False,
    case_sensitive: bool = False,
    status_filters: str | None = None,
    status_filters_bracket: list[str] | None = Query(default=None, alias="status_filters[]"),
    match_filters: str | None = None,
    match_filters_bracket: list[str] | None = Query(default=None, alias="match_filters[]"),
    source_filters: str | None = None,
    source_filters_bracket: list[str] | None = Query(default=None, alias="source_filters[]"),
    workflow_step_ids: str | None = None,
    workflow_step_ids_bracket: list[str] | None = Query(default=None, alias="workflow_step_ids[]"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """聚合读取：把视图下各文件句段按"文件顺序 + 文件内顺序"展平分页。

    每个句段附带 file_record_id 与 filename；返回分组边界信息供前端渲染分组表头。
    复用单文件句段的筛选/排序/序列化 helper，跨文件循环拼接。
    """
    view = _get_merge_view_or_404(db, view_id)
    project = _get_project_or_404(db, view.project_id)
    _require_project_read_access(project, current_user, db)
    files = _get_visible_merge_view_files(db, view, current_user)
    _require_merge_view_openable_files(files, current_user)

    safe_skip = max(skip, 0)
    safe_limit = _normalize_segment_page_limit(limit)
    normalized_status_filters = _normalize_segment_filter_values(status_filters, status_filters_bracket)
    normalized_match_filters = _normalize_segment_filter_values(match_filters, match_filters_bracket)
    normalized_source_filters = _normalize_segment_filter_values(source_filters, source_filters_bracket)
    normalized_workflow_step_ids = _normalize_segment_filter_values(workflow_step_ids, workflow_step_ids_bracket)

    # 先按文件累计匹配数，确定 skip/limit 落在哪些文件、各文件取多少。
    # 为避免逐文件全量序列化，采用"两段式"：先统计每文件匹配数，再按窗口取所需页片段。
    per_file_matched: list[int] = []
    per_file_queries: list = []
    for file_record in files:
        _assign_file_segments_to_first_workflow_step(db, file_record)
        base_query = _apply_segment_assignment_visibility_filter(
            db,
            db.query(Segment).filter(Segment.file_record_id == file_record.id),
            file_record,
            current_user,
        )
        filtered_query = _apply_segment_scope_filter(base_query, scope)
        filtered_query = _apply_segment_text_filters(
            filtered_query,
            source_query=source_query,
            target_query=target_query,
            source_exclude=source_exclude,
            target_exclude=target_exclude,
            case_sensitive=case_sensitive,
        )
        filtered_query = _apply_segment_screening_filters(
            filtered_query,
            status_filters=normalized_status_filters,
            match_filters=normalized_match_filters,
            source_filters=normalized_source_filters,
            workflow_step_ids=normalized_workflow_step_ids,
        )
        per_file_matched.append(filtered_query.count())
        per_file_queries.append(filtered_query)

    total_matched = sum(per_file_matched)
    # 计算每个文件的窗口起止（在展平序列中的绝对区间）
    flat_segments: list[tuple[Segment, FileRecord]] = []
    cumulative = 0
    remaining_skip = safe_skip
    remaining_limit = safe_limit
    for file_record, filtered_query, matched in zip(files, per_file_queries, per_file_matched):
        if remaining_limit <= 0:
            break
        file_start = cumulative
        file_end = cumulative + matched  # 不含
        # 该文件是否有落在 [skip, skip+limit) 窗口内的句段
        window_start = max(safe_skip, file_start)
        window_end = min(safe_skip + safe_limit, file_end)
        cumulative = file_end
        if window_start >= window_end:
            continue
        local_skip = window_start - file_start
        local_limit = window_end - window_start
        page_segments = (
            _order_segment_query(filtered_query, file_record)
            .offset(local_skip)
            .limit(local_limit)
            .all()
        )
        for seg in page_segments:
            flat_segments.append((seg, file_record))
        remaining_skip = 0
        remaining_limit -= local_limit

    # 分组边界：返回每个文件在该页内的段数，供前端定位分组表头
    groups: list[dict] = []
    seg_index = 0
    for file_record, matched in zip(files, per_file_matched):
        page_count = 0
        while seg_index < len(flat_segments) and flat_segments[seg_index][1].id == file_record.id:
            page_count += 1
            seg_index += 1
        if page_count > 0:
            groups.append({
                "file_record_id": str(file_record.id),
                "filename": file_record.filename,
                "matched_segments": matched,
                "page_segment_count": page_count,
            })

    # 序列化：每个文件独立构建 workflow/QA/numbering 上下文
    serialized: list[dict] = []
    segments_by_file: dict[UUID, list[Segment]] = {}
    for seg, file_record in flat_segments:
        segments_by_file.setdefault(file_record.id, []).append(seg)
    for file_record in files:
        segs = segments_by_file.get(file_record.id, [])
        if not segs:
            continue
        workflow_step_by_id, writable_workflow_assignments, can_manage = _build_segment_workflow_context(
            db, file_record, current_user,
        )
        target_numbering = _build_target_automatic_numbering_text_map(file_record)
        qa_issues_by_segment_id = _load_workbench_segment_qa_issues(db, segs)
        display_index_map = _get_segment_display_index_map(db, file_record.id, segs)
        source_filename = get_file_record_source_filename(file_record)
        for seg in segs:
            payload = _serialize_workbench_segment(
                seg,
                display_index=display_index_map.get(seg.id),
                source_filename=source_filename,
                target_automatic_numbering_by_sentence_id=target_numbering,
                qa_issues_by_segment_id=qa_issues_by_segment_id,
                workflow_step_by_id=workflow_step_by_id,
                writable_workflow_assignments=writable_workflow_assignments,
                can_manage=can_manage,
            )
            payload["file_record_id"] = str(file_record.id)
            payload["filename"] = file_record.filename
            serialized.append(payload)

    # 恢复"文件顺序 + 文件内顺序"的稳定排序（序列化时按文件分组，顺序已保持）
    return {
        "merge_view_id": str(view.id),
        "project_id": str(view.project_id),
        "name": view.name,
        "total_segments": total_matched,
        "matched_segments": total_matched,
        "skip": safe_skip,
        "limit": safe_limit,
        "filters": {
            "scope": scope,
            "source_query": source_query or "",
            "target_query": target_query or "",
            "source_exclude": source_exclude or "",
            "target_exclude": target_exclude or "",
            "search_fuzzy": search_fuzzy,
            "case_sensitive": case_sensitive,
            "status_filters": normalized_status_filters,
            "match_filters": normalized_match_filters,
            "source_filters": normalized_source_filters,
            "workflow_step_ids": normalized_workflow_step_ids,
        },
        "groups": groups,
        "server_time": datetime.now().isoformat(),
        "segments": serialized,
    }


@router.post("/file-records/{file_record_id}/exports")
@router.post("/documents/{file_record_id}/exports", include_in_schema=False)
def create_file_record_export_task(
    file_record_id: UUID,
    type: str = Query(default="original"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return JSONResponse(
        status_code=202,
        content=_queue_file_record_export_for_current_user(
            file_record_id=file_record_id,
            export_type=type,
            db=db,
            current_user=current_user,
        ),
    )


@router.get("/file-records/export-tasks/{task_id}")
@router.get("/documents/export-tasks/{task_id}", include_in_schema=False)
def get_file_record_export_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = get_file_export_task(db, task_id)
    _require_file_export_task_read_access(task, current_user)
    return serialize_file_export_task(task)


@router.get("/file-records/export-tasks/{task_id}/download")
@router.get("/documents/export-tasks/{task_id}/download", include_in_schema=False)
def download_file_record_export_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = get_file_export_task(db, task_id)
    _require_file_export_task_read_access(task, current_user)
    return build_file_export_download_response(task)


@router.get("/file-records/{file_record_id}/export")
@router.get("/documents/{file_record_id}/export", include_in_schema=False)
@router.get("/file-records/{file_record_id}/export-docx")
@router.get("/documents/{file_record_id}/export-docx", include_in_schema=False)
def export_file_record_docx(
    file_record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    queued_task = _queue_file_record_export_for_current_user(
        file_record_id=file_record_id,
        export_type="original",
        db=db,
        current_user=current_user,
    )
    task = wait_for_file_export_task(UUID(queued_task["task_id"]))
    return build_file_export_download_response(task)


@router.get("/file-records/{file_record_id}/export-options")
@router.get("/documents/{file_record_id}/export-options", include_in_schema=False)
def get_file_record_export_options(
    file_record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取文件支持的导出格式选项"""
    from app.services.adapters import get_export_options_for_file

    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    _require_file_record_read_access(file_record, current_user)
    raw_bytes = load_file_record_source(file_record)
    options = get_export_options_for_file(file_record.filename)
    if raw_bytes is None:
        options = [option for option in options if option.get("id") != "source"]

    return {
        "file_record_id": str(file_record_id),
        "filename": file_record.filename,
        "export_options": options,
    }


@router.get("/file-records/{file_record_id}/export/{export_type}")
@router.get("/documents/{file_record_id}/export/{export_type}", include_in_schema=False)
def export_file_record_with_type_queued(
    file_record_id: UUID,
    export_type: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    queued_task = _queue_file_record_export_for_current_user(
        file_record_id=file_record_id,
        export_type=export_type,
        db=db,
        current_user=current_user,
    )
    task = wait_for_file_export_task(UUID(queued_task["task_id"]))
    return build_file_export_download_response(task)


@router.get("/file-records/{file_record_id}/export/{export_type}", include_in_schema=False)
@router.get("/documents/{file_record_id}/export/{export_type}", include_in_schema=False)
def export_file_record_with_type(
    file_record_id: UUID,
    export_type: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """多格式导出接口 - 支持原格式、双语、TMX、XLIFF 等导出类型

    Args:
        file_record_id: 文件记录 ID
        export_type: 导出类型 (original, bilingual, bilingual_txt, tmx, xliff, xliff2)
    """
    from app.services.adapters import export_file

    _begin_repeatable_read_snapshot(db)
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    _require_file_record_read_access(file_record, current_user)
    raw_bytes = load_file_record_source(file_record)

    if export_type == "source":
        if raw_bytes is None:
            raise HTTPException(status_code=400, detail="The source file is unavailable.")
        source_filename = get_file_record_source_filename(file_record)
        return _build_binary_download_response(
            filename=source_filename,
            content=raw_bytes,
            media_type="application/octet-stream",
        )

    segments = list_segments_for_file_record(db, file_record_id)

    # 原格式导出需要按原文件重新写回，避免走通用导出器丢失格式。
    if export_type == "original":
        source_filename = get_file_record_source_filename(file_record)
        if not can_export_task_file(source_filename, has_source_file=raw_bytes is not None):
            raise HTTPException(status_code=400, detail="Current file format does not support original export yet.")
        try:
            exported_file = export_translated_task_file(
                raw_bytes=raw_bytes,
                filename=source_filename,
                segments=segments,
                document_parse_mode=getattr(file_record, "document_parse_mode", DOCUMENT_PARSE_MODE_FULL),
                document_parse_options=_get_file_record_document_parse_options(file_record),
                target_language=getattr(file_record, "target_language", None),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"导出失败: {str(exc)}") from exc

        return _build_binary_download_response(
            filename=exported_file.filename,
            content=exported_file.content,
            media_type=exported_file.media_type,
        )

    if export_type in BILINGUAL_DOCX_LAYOUT_EXPORT_ORDERS:
        source_filename = get_file_record_source_filename(file_record)
        if get_task_file_extension(source_filename) != ".docx":
            raise HTTPException(status_code=400, detail="Only DOCX source files support layout-preserving bilingual Word export.")
        try:
            exported_file = export_bilingual_task_docx_with_layout(
                raw_bytes=raw_bytes,
                filename=source_filename,
                segments=segments,
                order=BILINGUAL_DOCX_LAYOUT_EXPORT_ORDERS[export_type],
                document_parse_mode=getattr(file_record, "document_parse_mode", DOCUMENT_PARSE_MODE_FULL),
                document_parse_options=_get_file_record_document_parse_options(file_record),
                target_language=getattr(file_record, "target_language", None),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"导出失败: {str(exc)}") from exc

        return _build_binary_download_response(
            filename=exported_file.filename,
            content=exported_file.content,
            media_type=exported_file.media_type,
        )

    # 其他导出格式使用通用句段列表。
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


def _require_file_record_write_access(
    db: Session,
    file_record_id: UUID,
    current_user: User,
    operation_token: str | None = None,
) -> FileRecord:
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在。")
    _require_file_record_work_access(file_record, current_user)
    ensure_file_record_write_allowed(
        db,
        file_record,
        operation_token=operation_token,
    )
    return file_record


@router.put("/file-records/{file_record_id}/segments/{sentence_id}")
@router.put("/documents/{file_record_id}/segments/{sentence_id}", include_in_schema=False)
def update_segment(
    file_record_id: UUID,
    sentence_id: str,
    update: SegmentUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
):
    """更新单个片段的译文"""
    file_record = _require_file_record_write_access(db, file_record_id, current_user, operation_token)
    current_segment = (
        db.query(Segment)
        .filter(Segment.file_record_id == file_record_id, Segment.sentence_id == sentence_id)
        .first()
    )
    if not current_segment:
        raise HTTPException(status_code=404, detail="片段不存在。")
    _require_segment_work_access(db, file_record, current_segment, current_user)
    if update.base_version is not None:
        current_segment = (
            db.query(Segment)
            .filter(Segment.file_record_id == file_record_id, Segment.sentence_id == sentence_id)
            .first()
        )
        if not current_segment:
            raise HTTPException(status_code=404, detail="片段不存在。")
        current_version = int(current_segment.version or 1)
        if current_version != update.base_version and not _can_auto_merge_stale_segment(
            current_segment,
            incoming_source=update.source,
            current_user=current_user,
            target_text=update.target_text,
        ):
            workflow_step_by_id, writable_workflow_assignments, can_manage = _build_segment_workflow_context(
                db,
                file_record,
                current_user,
            )
            target_automatic_numbering_by_sentence_id = _build_target_automatic_numbering_text_map(file_record)
            qa_issues_by_segment_id = _load_workbench_segment_qa_issues(db, [current_segment])
            display_index_map = _get_segment_display_index_map(db, file_record_id, [current_segment])
            conflict = SegmentUpdateConflict(
                sentence_id=current_segment.sentence_id,
                current_version=current_version,
                attempted_version=update.base_version,
                current_target_text=current_segment.target_text or "",
                current_source=current_segment.source,
                current_updated_at=current_segment.updated_at,
                current_last_modified_by_id=current_segment.last_modified_by_id,
            )
            return {
                "updated_count": 0,
                "conflicts": [_serialize_segment_update_conflict(conflict)],
                "auto_tm": _empty_auto_tm_summary().to_dict(),
                "project_sync": empty_project_segment_sync_summary().to_dict(),
                "segments": [
                    _serialize_workbench_segment(
                        current_segment,
                        display_index=display_index_map.get(current_segment.id),
                        source_filename=get_file_record_source_filename(file_record),
                        target_automatic_numbering_by_sentence_id=target_automatic_numbering_by_sentence_id,
                        qa_issues_by_segment_id=qa_issues_by_segment_id,
                        workflow_step_by_id=workflow_step_by_id,
                        writable_workflow_assignments=writable_workflow_assignments,
                        can_manage=can_manage,
                    )
                ],
            }
    segment = update_segment_by_sentence_id(
        db=db,
        file_record_id=file_record_id,
        sentence_id=sentence_id,
        target_text=update.target_text,
        target_html=update.target_html,
        source=update.source,
        current_user=current_user,
        track_revision=update.track_revision,
        confirm=update.confirm,
    )
    if not segment:
        raise HTTPException(status_code=404, detail="片段不存在。")

    project_sync_summary = empty_project_segment_sync_summary()
    if normalize_text(segment.target_text) and not segment.project_sync_disabled:
        project_sync_summary = sync_project_repeated_segments_from_segments(
            db,
            file_record=file_record,
            source_segments=[segment],
            current_user=current_user,
        )

    auto_tm_summary = _empty_auto_tm_summary()
    if segment.status == "confirmed":
        auto_tm_summary = enqueue_confirmed_segments_for_auto_tm(
            db,
            file_record=file_record,
            segments=[segment],
            current_user=current_user,
        )
    if auto_tm_summary.queued_count > 0 or project_sync_summary.filled_count > 0 or project_sync_summary.updated_count > 0:
        db.commit()
        _schedule_auto_tm_processing(background_tasks, auto_tm_summary)
    workflow_step_by_id, writable_workflow_assignments, can_manage = _build_segment_workflow_context(
        db,
        file_record,
        current_user,
    )
    target_automatic_numbering_by_sentence_id = _build_target_automatic_numbering_text_map(file_record)
    response_segments = [segment, *project_sync_summary.current_file_segments]
    qa_issues_by_segment_id = _load_workbench_segment_qa_issues(db, response_segments)
    display_index_map = _get_segment_display_index_map(db, file_record_id, response_segments)
    _schedule_spelling_grammar_qa_for_segments(background_tasks, file_record, response_segments)

    return {
        "id": segment.id,
        "sentence_id": segment.sentence_id,
        "target_text": segment.target_text,
        "status": segment.status,
        "source": segment.source,
        "version": int(segment.version or 1),
        "updated_at": segment.updated_at.isoformat() if segment.updated_at else None,
        "auto_tm": auto_tm_summary.to_dict(),
        "project_sync": project_sync_summary.to_dict(),
        "updated_count": 1,
        "conflicts": [],
        "segments": [
            _serialize_workbench_segment(
                item,
                display_index=display_index_map.get(item.id),
                source_filename=get_file_record_source_filename(file_record),
                target_automatic_numbering_by_sentence_id=target_automatic_numbering_by_sentence_id,
                qa_issues_by_segment_id=qa_issues_by_segment_id,
                workflow_step_by_id=workflow_step_by_id,
                writable_workflow_assignments=writable_workflow_assignments,
                can_manage=can_manage,
            )
            for item in response_segments
        ],
    }


@router.put("/file-records/{file_record_id}/segments/{sentence_id}/source")
@router.put("/documents/{file_record_id}/segments/{sentence_id}/source", include_in_schema=False)
def update_segment_source(
    file_record_id: UUID,
    sentence_id: str,
    update: SegmentSourceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
):
    """更新单个片段的原文"""
    file_record = _require_file_record_write_access(db, file_record_id, current_user, operation_token)
    existing_segment = (
        db.query(Segment)
        .filter(Segment.file_record_id == file_record_id, Segment.sentence_id == sentence_id)
        .first()
    )
    if not existing_segment:
        raise HTTPException(status_code=404, detail="片段不存在。")
    _require_segment_work_access(db, file_record, existing_segment, current_user)
    segment = update_segment_source_text(
        db=db,
        file_record_id=file_record_id,
        sentence_id=sentence_id,
        source_text=update.source_text,
        current_user=current_user,
    )
    if not segment:
        raise HTTPException(status_code=404, detail="片段不存在。")

    return {
        "id": segment.id,
        "sentence_id": segment.sentence_id,
        "source_text": segment.source_text,
        "display_text": segment.display_text,
        "source_html": segment.source_html,
    }


@router.patch("/file-records/{file_record_id}/segments/{sentence_id}/project-sync")
def update_segment_project_sync(
    file_record_id: UUID,
    sentence_id: str,
    payload: SegmentProjectSyncUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
):
    """开启或关闭单个句段的项目内重复句段同步。"""
    file_record = _require_file_record_write_access(db, file_record_id, current_user, operation_token)
    segment = (
        db.query(Segment)
        .filter(Segment.file_record_id == file_record_id, Segment.sentence_id == sentence_id)
        .first()
    )
    if not segment:
        raise HTTPException(status_code=404, detail="片段不存在。")

    _require_segment_work_access(db, file_record, segment, current_user)
    if payload.disabled:
        summary = disable_project_sync_for_segments([segment], current_user=current_user)
        if summary.updated_count:
            sync_file_record_status(db, file_record_id)
    elif segment.project_sync_disabled:
        segment.project_sync_disabled = False
        segment.last_modified_by_id = current_user.id
        segment.version = int(segment.version or 1) + 1
    db.commit()
    db.refresh(segment)
    workflow_step_by_id, writable_workflow_assignments, can_manage = _build_segment_workflow_context(
        db,
        file_record,
        current_user,
    )
    target_automatic_numbering_by_sentence_id = _build_target_automatic_numbering_text_map(file_record)
    display_index_map = _get_segment_display_index_map(db, file_record_id, [segment])
    return _serialize_workbench_segment(
        segment,
        display_index=display_index_map.get(segment.id),
        source_filename=get_file_record_source_filename(file_record),
        target_automatic_numbering_by_sentence_id=target_automatic_numbering_by_sentence_id,
        workflow_step_by_id=workflow_step_by_id,
        writable_workflow_assignments=writable_workflow_assignments,
        can_manage=can_manage,
    )


@router.post("/file-records/{file_record_id}/segments/project-sync/disable")
def disable_file_record_project_sync(
    file_record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
) -> ProjectSyncDisableResponse:
    """一键关闭当前文件可写句段的项目同步，并清空项目同步生成的译文。"""
    file_record = _require_file_record_write_access(db, file_record_id, current_user, operation_token)
    segments = (
        db.query(Segment)
        .filter(Segment.file_record_id == file_record_id)
        .all()
    )
    writable_segments = _filter_writable_segments(db, file_record, current_user, segments)
    summary = disable_project_sync_for_segments(writable_segments, current_user=current_user)
    if summary.updated_count:
        sync_file_record_status(db, file_record_id)
    db.commit()
    return ProjectSyncDisableResponse(**summary.to_dict())


def _extend_project_sync_update_summary(
    summary: ProjectSyncDisableSummary,
    current_summary: ProjectSyncDisableSummary,
) -> None:
    summary.updated_count += current_summary.updated_count
    summary.disabled_count += current_summary.disabled_count
    summary.cleared_count += current_summary.cleared_count
    summary.updated_segments.extend(current_summary.updated_segments)


def _set_project_sync_disabled_for_project(
    db: Session,
    project_id: UUID,
    disabled: bool,
    current_user: User | None = None,
) -> ProjectSyncDisableSummary:
    _get_project_or_404(db, project_id)
    file_records = (
        db.query(FileRecord)
        .filter(FileRecord.project_id == project_id)
        .order_by(FileRecord.created_at.asc(), FileRecord.id.asc())
        .all()
    )
    summary = ProjectSyncDisableSummary()
    for file_record in file_records:
        ensure_file_record_write_allowed(db, file_record)
        segments = (
            db.query(Segment)
            .filter(Segment.file_record_id == file_record.id)
            .all()
        )
        current_summary = (
            disable_project_sync_for_segments(segments, current_user=current_user)
            if disabled
            else enable_project_sync_for_segments(segments, current_user=current_user)
        )
        _extend_project_sync_update_summary(summary, current_summary)
        if disabled and current_summary.updated_count:
            sync_file_record_status(db, file_record.id)

    return summary


@router.patch("/projects/{project_id}/segments/project-sync")
def update_project_sync_for_project(
    project_id: UUID,
    payload: SegmentProjectSyncUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> ProjectSyncDisableResponse:
    """开启或关闭项目下全部文件的项目同步。关闭时清空项目同步生成的译文。"""
    summary = _set_project_sync_disabled_for_project(db, project_id, payload.disabled, current_user)
    db.commit()
    return ProjectSyncDisableResponse(**summary.to_dict())


@router.post("/projects/{project_id}/segments/project-sync/disable")
def disable_project_sync_for_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> ProjectSyncDisableResponse:
    """一键关闭项目下全部文件的项目同步，并清空项目同步生成的译文。"""
    disable_summary = _set_project_sync_disabled_for_project(db, project_id, True, current_user)
    db.commit()
    return ProjectSyncDisableResponse(**disable_summary.to_dict())


@router.post("/file-records/{file_record_id}/segments/{sentence_id}/split")
def split_segment(
    file_record_id: UUID,
    sentence_id: str,
    payload: SegmentSplitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
):
    """拆分句段：在指定偏移位置将一个句段拆为两个。"""
    file_record = _require_file_record_write_access(db, file_record_id, current_user, operation_token)
    segment = (
        db.query(Segment)
        .filter(Segment.file_record_id == file_record_id, Segment.sentence_id == sentence_id)
        .first()
    )
    if not segment:
        raise HTTPException(status_code=404, detail="片段不存在。")

    _require_segment_work_access(db, file_record, segment, current_user)
    source_text = segment.source_text or ""
    if payload.split_offset <= 0 or payload.split_offset >= len(source_text):
        raise HTTPException(status_code=400, detail="拆分位置无效，必须在文本范围内。")

    # 拆分 source_text
    first_source = source_text[:payload.split_offset].rstrip()
    second_source = source_text[payload.split_offset:].lstrip()
    if not first_source or not second_source:
        raise HTTPException(status_code=400, detail="拆分后不能产生空句段。")

    # 拆分 target_text（按同比例偏移，如果有译文的话）
    target_text = segment.target_text or ""
    target_html = segment.target_html
    first_target = ""
    second_target = ""
    if target_text.strip():
        # 按比例估算译文拆分点
        ratio = payload.split_offset / len(source_text)
        target_offset = round(ratio * len(target_text))
        first_target = target_text[:target_offset].rstrip()
        second_target = target_text[target_offset:].lstrip()

    # 生成新的 sentence_id：使用子编号方式
    new_sentence_id = _generate_split_sentence_id(sentence_id, db, file_record_id)

    # 更新原句段
    segment.source_text = first_source
    segment.source_hash = build_source_hash(first_source)
    segment.display_text = first_source
    segment.source_html = None
    segment.target_text = first_target
    segment.target_html = None
    segment.score = 0.0
    segment.source = "manual"
    segment.last_modified_by_id = current_user.id
    segment.matched_source_text = None
    segment.matched_collection_name = None
    segment.matched_creator_name = None
    segment.matched_created_at = None
    segment.matched_updated_at = None
    segment.status = _resolve_unconfirmed_segment_status(segment)
    segment.version = int(segment.version or 1) + 1

    # 创建新句段
    new_segment = Segment(
        file_record_id=file_record_id,
        workflow_step_id=segment.workflow_step_id,
        sentence_id=new_sentence_id,
        source_text=second_source,
        source_hash=build_source_hash(second_source),
        display_text=second_source,
        source_html=None,
        target_text=second_target,
        target_html=None,
        status="none",
        score=0.0,
        source="manual",
        last_modified_by_id=current_user.id,
        block_type=segment.block_type,
        block_index=segment.block_index,
        row_index=segment.row_index,
        cell_index=segment.cell_index,
    )
    new_segment.status = _resolve_unconfirmed_segment_status(new_segment)
    db.add(new_segment)

    sync_file_record_status(db, file_record_id)
    db.commit()
    db.refresh(segment)
    db.refresh(new_segment)
    workflow_step_by_id, writable_workflow_assignments, can_manage = _build_segment_workflow_context(
        db,
        file_record,
        current_user,
    )
    target_automatic_numbering_by_sentence_id = _build_target_automatic_numbering_text_map(file_record)
    display_index_map = _get_segment_display_index_map(db, file_record_id, [segment, new_segment])

    return {
        "first": _serialize_workbench_segment(
            segment,
            display_index=display_index_map.get(segment.id),
            source_filename=get_file_record_source_filename(file_record),
            target_automatic_numbering_by_sentence_id=target_automatic_numbering_by_sentence_id,
            workflow_step_by_id=workflow_step_by_id,
            writable_workflow_assignments=writable_workflow_assignments,
            can_manage=can_manage,
        ),
        "second": _serialize_workbench_segment(
            new_segment,
            display_index=display_index_map.get(new_segment.id),
            source_filename=get_file_record_source_filename(file_record),
            target_automatic_numbering_by_sentence_id=target_automatic_numbering_by_sentence_id,
            workflow_step_by_id=workflow_step_by_id,
            writable_workflow_assignments=writable_workflow_assignments,
            can_manage=can_manage,
        ),
    }


@router.post("/file-records/{file_record_id}/segments/{sentence_id}/merge")
def merge_segment(
    file_record_id: UUID,
    sentence_id: str,
    payload: SegmentMergeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
):
    """合并句段：将当前句段与指定的下一个句段合并为一个。"""
    file_record = _require_file_record_write_access(db, file_record_id, current_user, operation_token)
    first_seg = (
        db.query(Segment)
        .filter(Segment.file_record_id == file_record_id, Segment.sentence_id == sentence_id)
        .first()
    )
    if not first_seg:
        raise HTTPException(status_code=404, detail="片段不存在。")

    second_seg = (
        db.query(Segment)
        .filter(
            Segment.file_record_id == file_record_id,
            Segment.sentence_id == payload.target_sentence_id,
        )
        .first()
    )
    if not second_seg:
        raise HTTPException(status_code=404, detail="目标合并片段不存在。")

    # 校验两个句段必须在同一个 block 中
    _require_segment_work_access(db, file_record, first_seg, current_user)
    _require_segment_work_access(db, file_record, second_seg, current_user)
    if first_seg.workflow_step_id != second_seg.workflow_step_id:
        raise HTTPException(status_code=400, detail="只能合并处于同一流程阶段的句段。")

    if not _segments_in_same_merge_block(first_seg, second_seg):
        raise HTTPException(status_code=400, detail="只能合并同一区块内相邻的句段。")

    # 合并文本
    separator = "" if _is_cjk_text(first_seg.source_text) else " "
    merged_source = first_seg.source_text.rstrip() + separator + second_seg.source_text.lstrip()
    merged_target = ""
    if (first_seg.target_text or "").strip() or (second_seg.target_text or "").strip():
        merged_target = (first_seg.target_text or "").rstrip() + separator + (second_seg.target_text or "").lstrip()

    # 更新第一个句段
    first_seg.source_text = merged_source.strip()
    first_seg.source_hash = build_source_hash(first_seg.source_text)
    first_seg.display_text = merged_source.strip()
    first_seg.source_html = None
    first_seg.target_text = merged_target.strip()
    first_seg.target_html = None
    first_seg.score = 0.0
    first_seg.source = "manual"
    first_seg.last_modified_by_id = current_user.id
    first_seg.matched_source_text = None
    first_seg.matched_collection_name = None
    first_seg.matched_creator_name = None
    first_seg.matched_created_at = None
    first_seg.matched_updated_at = None
    first_seg.status = _resolve_unconfirmed_segment_status(first_seg)
    first_seg.version = int(first_seg.version or 1) + 1

    # 迁移第二个句段的评论到第一个句段
    for comment in second_seg.comments:
        comment.segment_id = first_seg.id

    # 删除第二个句段的修订记录
    db.query(SegmentRevision).filter(SegmentRevision.segment_id == second_seg.id).delete()

    # 删除第二个句段
    db.delete(second_seg)

    sync_file_record_status(db, file_record_id)
    db.commit()
    db.refresh(first_seg)
    workflow_step_by_id, writable_workflow_assignments, can_manage = _build_segment_workflow_context(
        db,
        file_record,
        current_user,
    )
    target_automatic_numbering_by_sentence_id = _build_target_automatic_numbering_text_map(file_record)
    display_index_map = _get_segment_display_index_map(db, file_record_id, [first_seg])

    return {
        "merged": _serialize_workbench_segment(
            first_seg,
            display_index=display_index_map.get(first_seg.id),
            source_filename=get_file_record_source_filename(file_record),
            target_automatic_numbering_by_sentence_id=target_automatic_numbering_by_sentence_id,
            workflow_step_by_id=workflow_step_by_id,
            writable_workflow_assignments=writable_workflow_assignments,
            can_manage=can_manage,
        ),
        "deleted_sentence_id": payload.target_sentence_id,
    }


@router.put("/file-records/{file_record_id}/segments")
@router.put("/documents/{file_record_id}/segments", include_in_schema=False)
def batch_update(
    file_record_id: UUID,
    batch: BatchSegmentUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
):
    """批量更新片段译文"""
    file_record = _require_file_record_write_access(db, file_record_id, current_user, operation_token)
    update_items = [u.model_dump() for u in batch.updates]
    requested_sentence_ids = [
        item.get("sentence_id")
        for item in update_items
        if item.get("sentence_id")
    ]
    if requested_sentence_ids:
        target_segments = (
            db.query(Segment)
            .filter(
                Segment.file_record_id == file_record_id,
                Segment.sentence_id.in_(requested_sentence_ids),
            )
            .all()
        )
        for target_segment in target_segments:
            _require_segment_work_access(db, file_record, target_segment, current_user)
    result = batch_update_segments(
        db=db,
        file_record_id=file_record_id,
        updates=update_items,
        current_user=current_user,
        return_result=True,
    )
    segments_for_project_sync = [
        segment
        for segment in result.updated_segments
        if normalize_text(segment.target_text) and not segment.project_sync_disabled
    ]
    project_sync_summary = empty_project_segment_sync_summary()
    if segments_for_project_sync:
        project_sync_summary = sync_project_repeated_segments_from_segments(
            db,
            file_record=file_record,
            source_segments=segments_for_project_sync,
            current_user=current_user,
        )
    auto_tm_summary = enqueue_confirmed_segments_for_auto_tm(
        db,
        file_record=file_record,
        segments=result.updated_segments,
        current_user=current_user,
    )
    if auto_tm_summary.queued_count > 0 or project_sync_summary.filled_count > 0 or project_sync_summary.updated_count > 0:
        db.commit()
        _schedule_auto_tm_processing(background_tasks, auto_tm_summary)
    workflow_step_by_id, writable_workflow_assignments, can_manage = _build_segment_workflow_context(
        db,
        file_record,
        current_user,
    )
    target_automatic_numbering_by_sentence_id = _build_target_automatic_numbering_text_map(file_record)
    response_segments = [*result.updated_segments, *project_sync_summary.current_file_segments]
    qa_issues_by_segment_id = _load_workbench_segment_qa_issues(db, response_segments)
    display_index_map = _get_segment_display_index_map(db, file_record_id, response_segments)
    _schedule_spelling_grammar_qa_for_segments(background_tasks, file_record, response_segments)
    return {
        "updated_count": result.updated_count,
        "conflicts": [_serialize_segment_update_conflict(conflict) for conflict in result.conflicts],
        "auto_tm": auto_tm_summary.to_dict(),
        "project_sync": project_sync_summary.to_dict(),
        "segments": [
            _serialize_workbench_segment(
                segment,
                display_index=display_index_map.get(segment.id),
                source_filename=get_file_record_source_filename(file_record),
                target_automatic_numbering_by_sentence_id=target_automatic_numbering_by_sentence_id,
                qa_issues_by_segment_id=qa_issues_by_segment_id,
                workflow_step_by_id=workflow_step_by_id,
                writable_workflow_assignments=writable_workflow_assignments,
                can_manage=can_manage,
            )
            for segment in response_segments
        ],
    }


@router.post("/file-records/{file_record_id}/segments/confirmation")
@router.post("/documents/{file_record_id}/segments/confirmation", include_in_schema=False)
def batch_update_segment_confirmation(
    file_record_id: UUID,
    payload: SegmentConfirmationBatchUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
):
    """批量确认或取消确认当前文件的全部句段。"""
    file_record = _require_file_record_write_access(db, file_record_id, current_user, operation_token)

    if payload.action == "confirm":
        query = (
            db.query(Segment)
            .filter(
                Segment.file_record_id == file_record_id,
                or_(Segment.status.is_(None), Segment.status != "confirmed"),
            )
        )
        query = _apply_segment_display_range_filter(
            db,
            query,
            file_record_id,
            payload.range_start,
            payload.range_end,
        )
        segments = query.all()
        segments = _filter_writable_segments(db, file_record, current_user, segments)
        next_status = "confirmed"
        updated_count = 0
        for segment in segments:
            if segment.status != next_status:
                segment.status = next_status
                segment.last_modified_by_id = current_user.id
                segment.version = int(segment.version or 1) + 1
                updated_count += 1
    else:
        query = (
            db.query(Segment)
            .filter(Segment.file_record_id == file_record_id, Segment.status == "confirmed")
        )
        query = _apply_segment_display_range_filter(
            db,
            query,
            file_record_id,
            payload.range_start,
            payload.range_end,
        )
        segments = query.all()
        segments = _filter_writable_segments(db, file_record, current_user, segments)
        updated_count = 0
        for segment in segments:
            next_status = _resolve_unconfirmed_segment_status(segment)
            if segment.status != next_status:
                segment.status = next_status
                segment.last_modified_by_id = current_user.id
                segment.version = int(segment.version or 1) + 1
                updated_count += 1

    auto_tm_summary = _empty_auto_tm_summary()
    project_sync_summary = empty_project_segment_sync_summary()
    if payload.action == "confirm" and updated_count:
        confirmed_segments_for_sync = [
            segment
            for segment in segments
            if segment.status == "confirmed" and normalize_text(segment.target_text)
        ]
        if confirmed_segments_for_sync:
            project_sync_summary = sync_project_repeated_segments_from_segments(
                db,
                file_record=file_record,
                source_segments=confirmed_segments_for_sync,
                current_user=current_user,
            )
        auto_tm_summary = enqueue_confirmed_segments_for_auto_tm(
            db,
            file_record=file_record,
            segments=segments,
            current_user=current_user,
        )

    if updated_count:
        sync_file_record_status(db, file_record_id)
        db.commit()
        _schedule_auto_tm_processing(background_tasks, auto_tm_summary)

    return {
        "updated_count": updated_count,
        "auto_tm": auto_tm_summary.to_dict(),
        "project_sync": project_sync_summary.to_dict(),
    }


@router.post("/file-records/{file_record_id}/segments/replace")
def replace_file_record_segment_targets(
    file_record_id: UUID,
    payload: SegmentReplaceRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
):
    """在服务端按筛选条件替换译文，避免前端加载全文后再逐条修改。"""
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    _require_file_record_write_access(db, file_record_id, current_user, operation_token)
    target_query = _normalize_segment_search_keyword(payload.target_query)
    if not target_query:
        raise HTTPException(status_code=400, detail="请先输入译文关键词用于替换。")

    query = db.query(Segment).filter(Segment.file_record_id == file_record_id)
    query = _apply_segment_scope_filter(query, payload.scope)
    query = _apply_segment_text_filters(
        query,
        source_query=payload.source_query,
        target_query=target_query,
        source_exclude=payload.source_exclude,
        target_exclude=payload.target_exclude,
        case_sensitive=payload.case_sensitive,
    )
    query = _apply_segment_screening_filters(
        query,
        status_filters=payload.status_filters,
        match_filters=payload.match_filters,
        source_filters=payload.source_filters,
        workflow_step_ids=payload.workflow_step_ids,
    )
    segments = _order_segment_query(query, file_record).all()
    segments = _filter_writable_segments(db, file_record, current_user, segments)
    if not segments:
        return {"updated_count": 0, "occurrence_count": 0}

    flags = 0 if payload.case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(target_query), flags)
    occurrence_count = 0
    updates: list[dict[str, str]] = []
    for segment in segments:
        target_text = segment.target_text or ""
        next_text, count = pattern.subn(payload.replace_text or "", target_text)
        if count <= 0 or next_text == target_text:
            continue
        occurrence_count += count
        updates.append({
            "sentence_id": segment.sentence_id,
            "target_text": next_text,
            "source": "manual",
        })
        if not payload.replace_all:
            break

    if not updates:
        return {"updated_count": 0, "occurrence_count": 0}

    updated_count = batch_update_segments(
        db=db,
        file_record_id=file_record_id,
        updates=updates,
        current_user=current_user,
    )
    if updated_count:
        updated_sentence_ids = [item["sentence_id"] for item in updates]
        updated_segments = (
            db.query(Segment)
            .filter(
                Segment.file_record_id == file_record_id,
                Segment.sentence_id.in_(updated_sentence_ids),
            )
            .all()
        )
        _schedule_spelling_grammar_qa_for_segments(background_tasks, file_record, updated_segments)
    return {"updated_count": updated_count, "occurrence_count": occurrence_count}


@router.get("/file-records/{file_record_id}/save-to-tm/stats")
def get_save_to_tm_stats(
    file_record_id: UUID,
    scope: Literal["confirmed", "translated", "all"] = "translated",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    _require_file_record_read_access(file_record, current_user)
    total_segments = (
        db.query(func.count(Segment.id))
        .filter(Segment.file_record_id == file_record_id)
        .scalar()
        or 0
    )
    query = db.query(Segment).filter(Segment.file_record_id == file_record_id)
    if scope == "confirmed":
        query = query.filter(Segment.status == "confirmed")
    elif scope == "translated":
        query = query.filter(func.trim(Segment.target_text) != "")

    matched_count = query.count()
    valid_count = (
        query.filter(
            func.trim(Segment.source_text) != "",
            func.trim(Segment.target_text) != "",
        )
        .count()
    )
    return {
        "total_segments": int(total_segments),
        "matched_count": matched_count,
        "valid_count": valid_count,
        "skipped_count": max(int(total_segments) - valid_count, 0),
    }


@router.post("/file-records/{file_record_id}/save-to-tm")
def save_file_record_segments_to_tm(
    file_record_id: UUID,
    payload: SaveToTMRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    source_language, target_language = _resolve_file_record_language_pair(file_record)

    collection: TMCollection | None = None
    created_collection = False
    if payload.collection_mode == "existing":
        target_collection_id = payload.collection_id or file_record.collection_id
        if target_collection_id is None:
            raise HTTPException(status_code=400, detail="请先选择要追加的目标记忆库。")
        collection = _get_collection_or_404(db, target_collection_id)
        _resolve_collection_language_pair(collection, source_language, target_language)
    elif payload.collection_mode != "new":
        raise HTTPException(status_code=400, detail="不支持的记忆库保存方式。")

    segments = list_segments_for_file_record(db, file_record_id)
    skipped_count = 0
    save_rows: list[tuple[str, str]] = []
    clean_numbering = is_word_document_filename(file_record.filename)

    for segment in segments:
        if payload.scope == "confirmed" and segment.status != "confirmed":
            skipped_count += 1
            continue
        if payload.scope == "translated" and not normalize_text(segment.target_text):
            skipped_count += 1
            continue

        source_text = normalize_text(segment.source_text)
        target_text = normalize_text(
            strip_automatic_numbering_prefix(
                segment.target_text,
                source_text=segment.source_text,
                display_text=segment.display_text,
                reference_texts=[segment.matched_source_text],
            )
            if clean_numbering
            else segment.target_text
        )
        if not source_text or not target_text:
            skipped_count += 1
            continue

        save_rows.append((source_text, target_text))

    if payload.collection_mode == "new" and save_rows:
        requested_name = payload.collection_name or _build_default_save_to_tm_collection_name(file_record)
        collection_name = _build_unique_memory_base_name(db, requested_name)
        collection = MemoryBase(
            name=collection_name,
            description=f"由任务「{file_record.filename}」保存生成",
            source_language=source_language,
            target_language=target_language,
        )
        db.add(collection)
        db.flush()
        created_collection = True

    if collection is None:
        skipped_count += len(save_rows)
        upsert_summary = None
    else:
        upsert_summary = batch_upsert_tm_entries(
            db,
            [
                TMUpsertEntry(
                    collection_id=collection.id,
                    source_text=source_text,
                    target_text=target_text,
                    source_language=source_language,
                    target_language=target_language,
                    creator_id=current_user.id,
                )
                for source_text, target_text in save_rows
            ],
        )
        skipped_count += upsert_summary.skipped_count

    if created_collection or (upsert_summary is not None and upsert_summary.total_written > 0):
        db.commit()
        sync_tm_embeddings(db, upsert_summary.sync_rows if upsert_summary is not None else [])

    created_count = upsert_summary.created_count if upsert_summary is not None else 0
    updated_count = upsert_summary.updated_count if upsert_summary is not None else 0
    refreshed_count = 0
    if collection is not None and upsert_summary is not None and upsert_summary.total_written > 0:
        refreshed_count = _notify_tm_collections_changed(
            db,
            [collection.id],
            source_file_record_id=file_record.id,
        )
        notification_title, notification_body = build_save_to_tm_notification(
            filename=file_record.filename,
            collection_name=collection.name,
            created_count=created_count,
            updated_count=updated_count,
            skipped_count=skipped_count,
            refreshed_count=refreshed_count,
        )
        create_operation_notification(
            db,
            user_id=current_user.id,
            notification_type="save_to_tm",
            title=notification_title,
            body=notification_body,
            project_id=file_record.project_id,
            file_record_id=file_record.id,
        )
        db.commit()

    return {
        "created_count": created_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "total_segments": len(segments),
        "refreshed_segments": refreshed_count,
        "collection_id": collection.id if collection else None,
        "collection_name": collection.name if collection else None,
        "created_collection": created_collection,
    }


@router.get("/file-records/{file_record_id}/revisions")
def get_file_record_revisions(
    file_record_id: UUID,
    sentence_id: str | None = None,
    sentence_ids: list[str] | None = Query(default=None),
    sentence_ids_bracket: list[str] | None = Query(default=None, alias="sentence_ids[]"),
    skip: int = 0,
    limit: int | None = None,
    scope: str = "all",
    source_query: str | None = None,
    target_query: str | None = None,
    source_exclude: str | None = None,
    target_exclude: str | None = None,
    search_fuzzy: bool = False,
    case_sensitive: bool = False,
    status_filters: str | None = None,
    status_filters_bracket: list[str] | None = Query(default=None, alias="status_filters[]"),
    match_filters: str | None = None,
    match_filters_bracket: list[str] | None = Query(default=None, alias="match_filters[]"),
    source_filters: str | None = None,
    source_filters_bracket: list[str] | None = Query(default=None, alias="source_filters[]"),
    workflow_step_ids: str | None = None,
    workflow_step_ids_bracket: list[str] | None = Query(default=None, alias="workflow_step_ids[]"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File record not found.")

    _require_file_record_read_access(file_record, current_user)
    requested_sentence_ids = [item for item in ((sentence_ids or []) + (sentence_ids_bracket or [])) if item]
    if sentence_id:
        requested_sentence_ids.append(sentence_id)

    if requested_sentence_ids:
        revisions = list_revisions(
            db,
            file_record_id=file_record_id,
            sentence_ids=list(dict.fromkeys(requested_sentence_ids)),
        )
        return [serialize_segment_revision(revision) for revision in revisions]

    if limit is not None:
        page_sentence_ids = _get_segment_page_sentence_ids(
            db,
            file_record_id,
            skip=skip,
            limit=limit,
            scope=scope,
            source_query=source_query,
            target_query=target_query,
            source_exclude=source_exclude,
            target_exclude=target_exclude,
            case_sensitive=case_sensitive,
            status_filters=_normalize_segment_filter_values(status_filters, status_filters_bracket),
            match_filters=_normalize_segment_filter_values(match_filters, match_filters_bracket),
            source_filters=_normalize_segment_filter_values(source_filters, source_filters_bracket),
            workflow_step_ids=_normalize_segment_filter_values(workflow_step_ids, workflow_step_ids_bracket),
        )
        revisions = list_revisions(
            db,
            file_record_id=file_record_id,
            sentence_ids=page_sentence_ids,
        )
        return [serialize_segment_revision(revision) for revision in revisions]

    revisions = list_revisions(
        db,
        file_record_id=file_record_id,
    )
    return [serialize_segment_revision(revision) for revision in revisions]


@router.get("/file-records/{file_record_id}/revision-settings")
def get_file_record_revision_settings(
    file_record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File record not found.")

    _require_file_record_read_access(file_record, current_user)
    return get_revision_display_settings(db, file_record_id)


@router.put("/file-records/{file_record_id}/revision-settings")
def update_file_record_revision_settings(
    file_record_id: UUID,
    payload: RevisionDisplaySettingsPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_file_record_write_access(db, file_record_id, current_user)
    return upsert_revision_display_settings(
        db,
        file_record_id=file_record_id,
        payload=payload.model_dump(mode="json"),
        updated_by=current_user,
    )


@router.patch("/revisions/{revision_id}")
def resolve_revision(
    revision_id: UUID,
    payload: RevisionResolvePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing_revision = get_revision_or_404(db, revision_id)
    _require_file_record_write_access(db, existing_revision.file_record_id, current_user)
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
    _require_file_record_write_access(db, file_record_id, current_user)

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
    _require_file_record_write_access(db, file_record_id, current_user)

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
    sentence_ids: list[str] | None = Query(default=None),
    sentence_ids_bracket: list[str] | None = Query(default=None, alias="sentence_ids[]"),
    skip: int = 0,
    limit: int | None = None,
    scope: str = "all",
    source_query: str | None = None,
    target_query: str | None = None,
    source_exclude: str | None = None,
    target_exclude: str | None = None,
    search_fuzzy: bool = False,
    case_sensitive: bool = False,
    status_filters: str | None = None,
    status_filters_bracket: list[str] | None = Query(default=None, alias="status_filters[]"),
    match_filters: str | None = None,
    match_filters_bracket: list[str] | None = Query(default=None, alias="match_filters[]"),
    source_filters: str | None = None,
    source_filters_bracket: list[str] | None = Query(default=None, alias="source_filters[]"),
    workflow_step_ids: str | None = None,
    workflow_step_ids_bracket: list[str] | None = Query(default=None, alias="workflow_step_ids[]"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    requested_sentence_ids = [item for item in ((sentence_ids or []) + (sentence_ids_bracket or [])) if item]
    _require_file_record_read_access(file_record, current_user)
    if limit is not None and not requested_sentence_ids:
        requested_sentence_ids = _get_segment_page_sentence_ids(
            db,
            file_record_id,
            skip=skip,
            limit=limit,
            scope=scope,
            source_query=source_query,
            target_query=target_query,
            source_exclude=source_exclude,
            target_exclude=target_exclude,
            case_sensitive=case_sensitive,
            status_filters=_normalize_segment_filter_values(status_filters, status_filters_bracket),
            match_filters=_normalize_segment_filter_values(match_filters, match_filters_bracket),
            source_filters=_normalize_segment_filter_values(source_filters, source_filters_bracket),
            workflow_step_ids=_normalize_segment_filter_values(workflow_step_ids, workflow_step_ids_bracket),
        )

    comments = list_segment_comments_for_file_record(
        db,
        file_record_id,
        sentence_ids=requested_sentence_ids if (limit is not None or requested_sentence_ids) else None,
    )
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

    _require_file_record_read_access(file_record, current_user)
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


def _normalize_term_extraction_models(models: list[str] | None) -> list[str]:
    requested = models or [TERM_EXTRACTION_MODEL]
    normalized: list[str] = []
    for model in requested:
        model_text = normalize_text(str(model or ""))
        if not model_text:
            continue
        if len(model_text) > 120:
            raise HTTPException(status_code=400, detail="模型 ID 过长。")
        if model_text not in normalized:
            normalized.append(model_text)
    if not normalized:
        raise HTTPException(status_code=400, detail="请至少选择一个提取模型。")
    if len(normalized) > 2:
        raise HTTPException(status_code=400, detail="最多只能选择两个模型进行比对。")
    return normalized


def _serialize_term_extraction_items(
    db: Session,
    term_base: TermBase | None,
    terms: list[ExtractedTerm],
) -> list[dict[str, Any]]:
    term_items = [
        {
            "source_text": item.source_text,
            "target_text": item.target_text,
            "source_normalized": item.source_normalized,
        }
        for item in terms
    ]
    if term_base is not None:
        return build_term_entry_conflict_items(
            db=db,
            term_base=term_base,
            entries=term_items,
        )
    return [
        {
            "index": index,
            "source_text": item["source_text"],
            "target_text": item["target_text"],
            "source_normalized": item["source_normalized"],
            "has_conflict": False,
            "conflict": None,
        }
        for index, item in enumerate(term_items)
    ]


@router.post("/file-records/{file_record_id}/term-extraction")
async def extract_file_record_terms(
    file_record_id: UUID,
    payload: TermExtractionRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    _require_file_record_read_access(file_record, current_user)
    source_language, target_language = _resolve_file_record_language_pair(file_record)
    body = payload or TermExtractionRequest()
    selected_models = _normalize_term_extraction_models(body.models)

    term_base = None
    target_term_base_id = body.term_base_id or file_record.term_base_id
    if target_term_base_id is not None:
        term_base = db.query(TermBase).filter(TermBase.id == target_term_base_id).first()
        if not term_base:
            raise HTTPException(status_code=404, detail="术语库不存在。")
        _ensure_resource_language_pair_matches(term_base, source_language, target_language, "术语库")

    segments = list_segments_for_file_record(db, file_record_id)
    if not segments:
        raise HTTPException(status_code=400, detail="当前文件没有可用于术语提取的已解析句段。")

    # 捕获响应所需的纯数据，并在 LLM 调用前释放请求级连接：
    # 术语提取需要多次调用 LLM（可能耗时数十秒），期间不应一直占用连接池连接。
    file_record_id_value = file_record.id
    file_record_filename = file_record.filename
    file_record_term_base_id = file_record.term_base_id
    total_segments = len(segments)
    db.close()

    extractions = []
    extraction_errors: list[dict[str, str | int]] = []
    for model in selected_models:
        try:
            extractions.append(await extract_terms_from_segments(
                segments=segments,
                source_language=source_language,
                target_language=target_language,
                max_terms=body.max_terms,
                model=model,
                extraction_prompt=body.extraction_prompt,
            ))
        except LLMConfigurationError as exc:
            extraction_errors.append({"model": model, "message": str(exc), "status_code": 400})
        except LLMResponseValidationError as exc:
            extraction_errors.append({"model": model, "message": str(exc), "status_code": 502})
        except LLMRequestError as exc:
            extraction_errors.append({"model": model, "message": str(exc), "status_code": 502})
        except TermExtractionError as exc:
            extraction_errors.append({"model": model, "message": str(exc), "status_code": 400})

    if not extractions:
        status_code = 502 if any(error["status_code"] == 502 for error in extraction_errors) else 400
        detail = str(extraction_errors[0]["message"]) if extraction_errors else "术语提取失败。"
        raise HTTPException(status_code=status_code, detail=detail)

    # LLM 调用完成后，使用独立短事务连接进行术语库查重与序列化。
    with SessionLocal() as serialize_db:
        serialize_term_base = (
            serialize_db.query(TermBase).filter(TermBase.id == target_term_base_id).first()
            if target_term_base_id is not None
            else None
        )
        results = []
        for extraction in extractions:
            terms = _serialize_term_extraction_items(serialize_db, serialize_term_base, extraction.terms)
            results.append({
                "provider": extraction.provider,
                "model": extraction.model,
                "terms": terms,
                "total": len(terms),
            })
        merged_terms = _serialize_term_extraction_items(
            serialize_db,
            serialize_term_base,
            merge_extracted_terms(extractions, max_terms=body.max_terms),
        )
    default_terms = merged_terms if len(results) > 1 else (results[0]["terms"] if results else [])
    primary_result = results[0] if results else {
        "provider": "openrouter",
        "model": selected_models[0],
        "terms": [],
        "total": 0,
    }

    return {
        "file_record": {
            "id": str(file_record_id_value),
            "filename": file_record_filename,
            "term_base_id": str(file_record_term_base_id) if file_record_term_base_id else None,
            "total_segments": total_segments,
        },
        "term_base_id": str(target_term_base_id) if target_term_base_id else None,
        "source_language": source_language,
        "target_language": target_language,
        "provider": primary_result["provider"],
        "model": primary_result["model"],
        "available_models": list(TERM_EXTRACTION_MODEL_OPTIONS),
        "results": results,
        "merged_terms": merged_terms,
        "terms": default_terms,
        "total": len(default_terms),
        "errors": [
            {"model": str(error["model"]), "message": str(error["message"])}
            for error in extraction_errors
        ],
    }


@router.post("/file-records/{file_record_id}/term-extraction/save")
def save_file_record_extracted_terms(
    file_record_id: UUID,
    payload: TermExtractionSaveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    _require_file_record_work_access(file_record, current_user)
    ensure_file_record_write_allowed(db, file_record, operation_token=operation_token)
    source_language, target_language = _resolve_file_record_language_pair(file_record)

    term_base = db.query(TermBase).filter(TermBase.id == payload.term_base_id).first()
    if not term_base:
        raise HTTPException(status_code=404, detail="术语库不存在。")
    _ensure_resource_language_pair_matches(term_base, source_language, target_language, "术语库")

    writable_term_base_ids = set(_load_file_record_term_base_write_ids(file_record))
    if term_base.id not in writable_term_base_ids:
        raise HTTPException(status_code=403, detail="目标术语库未绑定为当前文件的可写术语库。")

    return save_term_entries_batch(
        db=db,
        term_base=term_base,
        entries=[entry.model_dump() for entry in payload.entries],
        current_user=current_user,
    )


@router.post("/file-records/{file_record_id}/llm-translate")
@router.post("/documents/{file_record_id}/llm-translate", include_in_schema=False)
async def llm_translate_file_record(
    file_record_id: UUID,
    request: Request,
    payload: LLMTranslateRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: str | None = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
):
    """对指定范围的片段触发 LLM 译文修正，并通过 SSE 逐条返回结果。"""
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    _require_file_record_work_access(file_record, current_user)
    ensure_file_record_write_allowed(db, file_record, operation_token=operation_token)
    source_language, target_language = _resolve_file_record_language_pair(file_record)
    body = payload or LLMTranslateRequest()
    if body.scope == "current_segment" and not normalize_text(body.sentence_id or ""):
        raise HTTPException(status_code=400, detail="当前句段范围需要提供 sentence_id。")
    requested_model = normalize_text(body.model or "") or None
    try:
        validate_provider_choice(body.provider, model_override=requested_model)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    guidelines = _resolve_llm_guidelines(db, file_record, body)
    if "glossary_base_ids" in body.model_fields_set:
        glossary_bases = _validate_glossary_base_ids(db, body.glossary_base_ids)
        glossary_base_ids = [glossary_base.id for glossary_base in glossary_bases]
    else:
        glossary_base_ids = _load_file_record_glossary_base_ids(file_record)
        glossary_bases = _validate_glossary_base_ids(db, glossary_base_ids)
    for glossary_base in glossary_bases:
        _ensure_resource_language_pair_matches(
            glossary_base,
            source_language,
            target_language,
            "词汇表",
        )

    translation_tasks = _build_llm_translation_tasks(
        db=db,
        file_record_id=file_record_id,
        scope=body.scope,
        source_language=source_language,
        target_language=target_language,
        collection_id=file_record.collection_id,
        glossary_base_ids=glossary_base_ids,
        include_context=body.translation_unit == "paragraph",
        sentence_id=body.sentence_id,
        source_filename=get_file_record_source_filename(file_record),
    )
    deduplication = _deduplicate_llm_translation_tasks(translation_tasks)
    target_task_by_sentence_id = {
        task.sentence_id: task
        for task in translation_tasks
        if task.should_translate
    }

    current_user_id = current_user.id if current_user else None

    def _expand_sentence_ids(representative_sentence_id: str) -> list[str]:
        return deduplication.result_sentence_ids_by_representative.get(
            representative_sentence_id,
            [representative_sentence_id],
        )

    def _persist_llm_results(batch: list) -> tuple[list[tuple[str, dict]], int]:
        """在独立短事务中写回一批 LLM 翻译结果，返回 (待下发事件, 成功写回数量)。

        每次调用使用全新的 SessionLocal，仅在写回期间短暂占用连接；LLM 调用过程中
        不持有任何数据库连接，从而避免长时间流式翻译把连接池占满，也不会阻塞事件循环。
        """
        events: list[tuple[str, dict]] = []
        updated = 0
        with SessionLocal() as fdb:
            fr = fdb.query(FileRecord).filter(FileRecord.id == file_record_id).first()
            if fr is None:
                for result in batch:
                    for sentence_id in _expand_sentence_ids(result.sentence_id):
                        target_task = target_task_by_sentence_id.get(sentence_id)
                        events.append((
                            "error",
                            {
                                "sentence_id": sentence_id,
                                "status": target_task.status if target_task else result.status,
                                "message": "片段所属文件不存在，无法写回 LLM 译文。",
                            },
                        ))
                return events, 0

            user = (
                fdb.query(User).filter(User.id == current_user_id).first()
                if current_user_id
                else None
            )
            needed_sentence_ids = [
                sentence_id
                for result in batch
                for sentence_id in _expand_sentence_ids(result.sentence_id)
            ]
            seg_map = {
                segment.sentence_id: segment
                for segment in (
                    fdb.query(Segment)
                    .filter(
                        Segment.file_record_id == file_record_id,
                        Segment.sentence_id.in_(needed_sentence_ids),
                    )
                    .all()
                )
            }
            is_word_document = is_word_document_filename(fr.filename)

            for result in batch:
                try:
                    ensure_file_record_write_allowed(fdb, fr, operation_token=operation_token)
                except Exception as exc:  # noqa: BLE001
                    fdb.rollback()
                    for sentence_id in _expand_sentence_ids(result.sentence_id):
                        target_task = target_task_by_sentence_id.get(sentence_id)
                        events.append((
                            "error",
                            {
                                "sentence_id": sentence_id,
                                "status": target_task.status if target_task else result.status,
                                "message": f"数据库更新失败：{exc}",
                            },
                        ))
                    continue

                for sentence_id in _expand_sentence_ids(result.sentence_id):
                    target_task = target_task_by_sentence_id.get(sentence_id)
                    segment = seg_map.get(sentence_id)
                    if not segment:
                        events.append((
                            "error",
                            {
                                "sentence_id": sentence_id,
                                "status": target_task.status if target_task else result.status,
                                "message": "片段不存在，无法写回 LLM 译文。",
                            },
                        ))
                        continue

                    try:
                        _require_segment_work_access(fdb, fr, segment, user)
                    except HTTPException as exc:
                        events.append((
                            "error",
                            {
                                "sentence_id": sentence_id,
                                "status": target_task.status if target_task else result.status,
                                "message": str(exc.detail),
                            },
                        ))
                        continue

                    try:
                        with fdb.begin_nested():
                            before_text = segment.target_text
                            translated_text = (
                                strip_automatic_numbering_prefix(
                                    result.translated_text,
                                    source_text=segment.source_text,
                                    display_text=segment.display_text,
                                    reference_texts=[segment.matched_source_text],
                                )
                                if is_word_document
                                else result.translated_text
                            )
                            segment.target_text = translated_text
                            segment.target_html = None
                            segment.source = "llm"
                            segment.last_modified_by_id = current_user_id
                            segment.version = int(segment.version or 1) + 1
                            segment.source_word_count = segment.source_word_count or count_source_words(segment.source_text)
                            segment.llm_provider = result.provider
                            segment.llm_model = result.model
                            segment.status = _resolve_unconfirmed_segment_status(segment)
                            reject_stale_manual_revisions_for_segment(
                                fdb,
                                segment_id=segment.id,
                                after_text=translated_text,
                                current_user=user,
                            )

                            if (before_text or "") != (translated_text or ""):
                                fdb.add(SegmentRevision(
                                    file_record_id=file_record_id,
                                    segment_id=segment.id,
                                    sentence_id=segment.sentence_id,
                                    before_text=before_text or "",
                                    after_text=translated_text or "",
                                    source="llm",
                                    status="pending",
                                    author_id=current_user_id,
                                ))
                            record_translation_metric_event(
                                fdb,
                                segment=segment,
                                before_text=before_text,
                                after_text=translated_text,
                                source="llm",
                                current_user=user,
                            )
                    except Exception as exc:  # noqa: BLE001
                        events.append((
                            "error",
                            {
                                "sentence_id": sentence_id,
                                "status": target_task.status if target_task else result.status,
                                "message": f"数据库更新失败：{exc}",
                            },
                        ))
                        continue

                    updated += 1
                    events.append((
                        "segment",
                        {
                            "sentence_id": segment.sentence_id,
                            "target_text": segment.target_text,
                            "status": segment.status,
                            "source": segment.source,
                            "provider": result.provider,
                            "model": result.model,
                        },
                    ))

            fdb.commit()
        return events, updated

    def _finalize_file_record_status() -> None:
        with SessionLocal() as fdb:
            try:
                sync_file_record_status(fdb, file_record_id)
                fdb.commit()
            except Exception:  # noqa: BLE001
                fdb.rollback()

    async def event_stream():
        # 准备阶段已取完所需数据，立即归还请求级连接；后续写回使用独立短事务连接，
        # 避免在数分钟的流式翻译过程中一直占用连接池中的连接。
        db.close()
        updated_count = 0
        error_count = 0
        total_count = sum(1 for task in translation_tasks if task.should_translate)

        yield _sse_event(
            "start",
            {
                "file_record_id": str(file_record_id),
                "scope": body.scope,
                "provider": body.provider,
                "model": requested_model,
                "translation_unit": body.translation_unit,
                "source_language": source_language,
                "target_language": target_language,
                "glossary_base_ids": [str(glossary_base_id) for glossary_base_id in glossary_base_ids],
                "total": total_count,
                "unique_total": deduplication.unique_total,
                "deduplicated_count": deduplication.deduplicated_count,
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

        FLUSH_INTERVAL = 10
        pending_results: list = []
        request_cancel_check = getattr(request, "is_cancel_requested", None)

        def cancel_requested() -> bool:
            return bool(callable(request_cancel_check) and request_cancel_check())

        async def flush_pending():
            nonlocal updated_count, error_count, pending_results
            if not pending_results:
                return
            batch = pending_results
            pending_results = []
            events, updated = await asyncio.to_thread(_persist_llm_results, batch)
            updated_count += updated
            for event_name, event_data in events:
                if event_name == "error":
                    error_count += 1
                yield _sse_event(event_name, event_data)

        async for result in iter_batch_translate(
            deduplication.tasks,
            provider=body.provider,
            translation_guidelines=guidelines,
            translation_unit=body.translation_unit,
            model_override=requested_model,
            cancel_check=cancel_requested if callable(request_cancel_check) else None,
        ):
            if cancel_requested() or await request.is_disconnected():
                return

            if isinstance(result, LLMTranslationFailure):
                async for event in flush_pending():
                    yield event
                for sentence_id in _expand_sentence_ids(result.sentence_id):
                    target_task = target_task_by_sentence_id.get(sentence_id)
                    error_count += 1
                    yield _sse_event(
                        "error",
                        {
                            "sentence_id": sentence_id,
                            "status": target_task.status if target_task else result.status,
                            "message": result.error_message,
                        },
                    )
                continue

            pending_results.append(result)
            if len(pending_results) >= FLUSH_INTERVAL:
                async for event in flush_pending():
                    yield event

        async for event in flush_pending():
            yield event

        if updated_count > 0:
            await asyncio.to_thread(_finalize_file_record_status)

        if not cancel_requested() and not await request.is_disconnected():
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


def _entry_user_name(user: User | None) -> str | None:
    if user is None:
        return None
    return user.nickname or user.username


def _serialize_tm_entry(entry: TranslationMemory) -> dict:
    creator_name = _entry_user_name(entry.creator)
    last_modified_by_name = _entry_user_name(entry.last_modified_by)
    return {
        "id": entry.id,
        "collection_id": entry.collection_id,
        "source_text": entry.source_text,
        "target_text": entry.target_text,
        "source_language": entry.source_language,
        "target_language": entry.target_language,
        "creator_id": entry.creator_id,
        "creator_name": creator_name,
        "last_modified_by_id": entry.last_modified_by_id,
        "last_modified_by_name": last_modified_by_name,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
    }


def _serialize_resource_search_tm_row(entry: TranslationMemory, collection_name: str | None) -> dict:
    return {
        "id": str(entry.id),
        "type": "tm",
        "library_id": str(entry.collection_id) if entry.collection_id else None,
        "library_name": collection_name,
        "source_text": entry.source_text,
        "target_text": entry.target_text,
        "source_language": entry.source_language,
        "target_language": entry.target_language,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
    }


def _serialize_resource_search_term_row(entry: TermEntry, term_base_name: str | None) -> dict:
    return {
        "id": str(entry.id),
        "type": "term",
        "library_id": str(entry.term_base_id),
        "library_name": term_base_name,
        "source_text": entry.source_text,
        "target_text": entry.target_text,
        "source_language": entry.source_language,
        "target_language": entry.target_language,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
    }


@router.get("/file-records/{file_record_id}/resource-search")
def search_file_record_bound_resources(
    file_record_id: UUID,
    q: str = Query(default="", max_length=200),
    mode: Literal["exact", "fuzzy"] = Query(default="exact"),
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在。")

    _require_file_record_read_access(file_record, current_user)
    query_text = normalize_text(q or "")
    safe_limit = min(max(limit, 1), 100)
    source_language, target_language = _resolve_file_record_language_pair(file_record)
    collection_ids = _load_file_record_collection_ids(file_record)
    term_base_ids = _load_file_record_term_base_ids(file_record)

    if collection_ids:
        collections = (
            db.query(MemoryBase)
            .filter(MemoryBase.id.in_(collection_ids))
            .all()
        )
        for collection in collections:
            _ensure_resource_language_pair_matches(collection, source_language, target_language, "记忆库")

    if term_base_ids:
        term_bases = (
            db.query(TermBase)
            .filter(TermBase.id.in_(term_base_ids))
            .all()
        )
        for term_base in term_bases:
            _ensure_resource_language_pair_matches(term_base, source_language, target_language, "术语库")

    if not query_text:
        return {
            "items": [],
            "total": 0,
            "tm_total": 0,
            "term_total": 0,
            "collection_ids": [str(collection_id) for collection_id in collection_ids],
            "term_base_ids": [str(term_base_id) for term_base_id in term_base_ids],
            "query": query_text,
            "mode": mode,
        }

    like_pattern = f"%{query_text}%"
    fuzzy_keywords = [keyword for keyword in re.split(r"\s+", query_text) if keyword]

    def apply_resource_text_filter(query, source_column, target_column):
        if mode == "fuzzy" and len(fuzzy_keywords) > 1:
            return query.filter(and_(*[
                or_(
                    source_column.ilike(f"%{keyword}%"),
                    target_column.ilike(f"%{keyword}%"),
                )
                for keyword in fuzzy_keywords
            ]))
        return query.filter(
            or_(
                source_column.ilike(like_pattern),
                target_column.ilike(like_pattern),
            )
        )

    tm_rows: list[tuple[TranslationMemory, str | None]] = []
    tm_total = 0
    if collection_ids:
        tm_query = (
            db.query(TranslationMemory, MemoryBase.name.label("collection_name"))
            .outerjoin(MemoryBase, TranslationMemory.collection_id == MemoryBase.id)
            .filter(TranslationMemory.collection_id.in_(collection_ids))
            .filter(TranslationMemory.source_language == source_language)
            .filter(TranslationMemory.target_language == target_language)
        )
        tm_query = apply_resource_text_filter(
            tm_query,
            TranslationMemory.source_text,
            TranslationMemory.target_text,
        )
        tm_total = tm_query.count()
        tm_rows = (
            tm_query
            .order_by(TranslationMemory.updated_at.desc(), TranslationMemory.created_at.desc())
            .limit(safe_limit)
            .all()
        )

    term_rows: list[tuple[TermEntry, str | None]] = []
    term_total = 0
    if term_base_ids:
        term_query = (
            db.query(TermEntry, TermBase.name.label("term_base_name"))
            .outerjoin(TermBase, TermEntry.term_base_id == TermBase.id)
            .filter(TermEntry.term_base_id.in_(term_base_ids))
            .filter(TermEntry.source_language == source_language)
            .filter(TermEntry.target_language == target_language)
        )
        term_query = apply_resource_text_filter(
            term_query,
            TermEntry.source_text,
            TermEntry.target_text,
        )
        term_total = term_query.count()
        term_rows = (
            term_query
            .order_by(TermEntry.updated_at.desc(), TermEntry.created_at.desc())
            .limit(safe_limit)
            .all()
        )

    items = [
        *[_serialize_resource_search_tm_row(entry, collection_name) for entry, collection_name in tm_rows],
        *[_serialize_resource_search_term_row(entry, term_base_name) for entry, term_base_name in term_rows],
    ]
    items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    items = items[:safe_limit]

    return {
        "items": items,
        "total": tm_total + term_total,
        "tm_total": tm_total,
        "term_total": term_total,
        "collection_ids": [str(collection_id) for collection_id in collection_ids],
        "term_base_ids": [str(term_base_id) for term_base_id in term_base_ids],
        "query": query_text,
        "mode": mode,
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
    _: User = Depends(require_admin),
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
    _: User = Depends(require_admin),
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


@router.post("/translation-memory/collections/merge")
@router.post("/tm/collections/merge", include_in_schema=False)
def merge_tm_collections(
    payload: TMCollectionMergePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    source_collection_ids = list(dict.fromkeys(payload.source_collection_ids))
    if len(source_collection_ids) < 2:
        raise HTTPException(status_code=400, detail="请至少选择两个记忆库进行合并。")

    collections = (
        db.query(MemoryBase)
        .filter(MemoryBase.id.in_(source_collection_ids))
        .all()
    )
    collection_by_id = {collection.id: collection for collection in collections}
    missing_ids = [
        collection_id
        for collection_id in source_collection_ids
        if collection_id not in collection_by_id
    ]
    if missing_ids:
        raise HTTPException(status_code=404, detail="选择的记忆库不存在。")

    ordered_collections = [
        collection_by_id[collection_id]
        for collection_id in source_collection_ids
    ]
    source_language, target_language = _require_same_tm_collection_language_pair(
        ordered_collections,
    )
    name = _normalize_collection_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="合并后的记忆库名称不能为空。")

    target_collection = MemoryBase(
        name=name,
        description=normalize_text(payload.description or "") or None,
        source_language=source_language,
        target_language=target_language,
    )
    db.add(target_collection)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名记忆库已存在。") from exc

    created_rows = 0
    updated_rows = 0
    skipped_rows = 0
    merged_entries: list[MemoryEntry] = []
    merged_by_hash: dict[str, MemoryEntry] = {}
    merged_by_source_text: dict[str, MemoryEntry] = {}

    for source_collection_id in source_collection_ids:
        source_entries = (
            db.query(MemoryEntry)
            .filter(MemoryEntry.collection_id == source_collection_id)
            .order_by(MemoryEntry.created_at.asc(), MemoryEntry.updated_at.asc())
            .all()
        )
        for entry in source_entries:
            source_text = normalize_text(entry.source_text)
            target_text = normalize_text(entry.target_text)
            if not source_text or not target_text:
                skipped_rows += 1
                continue

            source_hash = entry.source_hash or build_source_hash(source_text)
            source_normalized = entry.source_normalized or normalize_match_text(source_text) or source_text
            existing = merged_by_hash.get(source_hash) or merged_by_source_text.get(source_text)
            if existing is not None:
                existing.source_text = source_text
                existing.target_text = target_text
                existing.source_hash = source_hash
                existing.source_normalized = source_normalized
                existing.source_language = source_language
                existing.target_language = target_language
                if existing.creator_id is None:
                    existing.creator_id = entry.creator_id or current_user.id
                existing.last_modified_by_id = current_user.id
                merged_by_hash[source_hash] = existing
                merged_by_source_text[source_text] = existing
                merged_entries.append(existing)
                updated_rows += 1
                continue

            merged_entry = MemoryEntry(
                collection_id=target_collection.id,
                source_text=source_text,
                target_text=target_text,
                source_hash=source_hash,
                source_normalized=source_normalized,
                source_language=source_language,
                target_language=target_language,
                creator_id=entry.creator_id or current_user.id,
                last_modified_by_id=current_user.id,
            )
            db.add(merged_entry)
            merged_by_hash[source_hash] = merged_entry
            merged_by_source_text[source_text] = merged_entry
            merged_entries.append(merged_entry)
            created_rows += 1

    db.flush()
    sync_rows = list(
        {
            row.id: row.source_text
            for row in merged_entries
            if row.id is not None and row.source_text
        }.items()
    )
    db.commit()
    sync_tm_embeddings(db, sync_rows)

    entry_count = (
        db.query(MemoryEntry)
        .filter(MemoryEntry.collection_id == target_collection.id)
        .count()
    )
    db.refresh(target_collection)
    return {
        "collection": _serialize_tm_collection(target_collection, entry_count),
        "source_count": len(source_collection_ids),
        "created_rows": created_rows,
        "updated_rows": updated_rows,
        "skipped_rows": skipped_rows,
        "merged_rows": created_rows + updated_rows,
    }


@router.delete("/translation-memory/collections/{collection_id}")
@router.delete("/tm/collections/{collection_id}", include_in_schema=False)
def delete_tm_collection(
    collection_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    collection = _get_collection_or_404(db, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="TM 记忆库不存在。")

    entry_count = (
        db.query(MemoryEntry)
        .filter(MemoryEntry.collection_id == collection.id)
        .count()
    )
    (
        db.query(FileRecord)
        .filter(FileRecord.collection_id == collection.id)
        .update({FileRecord.collection_id: None}, synchronize_session=False)
    )
    deleted_id = collection.id
    bound_file_records = (
        db.query(FileRecord)
        .filter(FileRecord.collection_ids_json.isnot(None))
        .all()
    )
    for file_record in bound_file_records:
        current_ids = _load_file_record_collection_ids(file_record)
        if deleted_id not in current_ids:
            continue
        _store_file_record_collection_ids(
            file_record,
            [collection_id for collection_id in current_ids if collection_id != deleted_id],
        )
    (
        db.query(MemoryEntry)
        .filter(MemoryEntry.collection_id == collection.id)
        .delete(synchronize_session=False)
    )

    db.delete(collection)
    db.commit()
    return {"message": "记忆库已删除。", "deleted_entries": entry_count}


@router.post("/translation-memory/preview-sdltm")
@router.post("/tm/preview-sdltm", include_in_schema=False)
async def preview_sdltm(
    file: UploadFile = File(...),
):
    """Preview SDLTM file metadata without importing."""
    extension = f".{(file.filename or '').split('.')[-1].lower()}" if file.filename else ""
    if extension not in SDLTM_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持 .sdltm 文件。")

    task_id, staged_file = await asyncio.to_thread(_stage_resource_upload_file, file)
    try:
        metadata = preview_sdltm_metadata_from_path(staged_file["path"])
        return {
            "name": metadata.name,
            "source_language": metadata.source_language,
            "target_language": metadata.target_language,
            "entry_count": metadata.entry_count,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取 SDLTM 元数据失败：{exc}") from exc
    finally:
        cleanup_import_task_staging(task_id)


def _validate_tm_import_upload(file: UploadFile, raw_bytes: bytes | None = None) -> str:
    extension = f".{(file.filename or '').split('.')[-1].lower()}" if file.filename else ""
    if extension not in TM_IMPORT_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持上传 .tmx、.sdltm、.xls、.xlsx 或 .csv 文件。")
    if raw_bytes is not None and not raw_bytes:
        raise HTTPException(status_code=400, detail="上传的文件为空。")
    return extension


def _resource_import_preview_max_scan_rows() -> int:
    value = int(get_settings().resource_import_preview_max_scan_rows or 1000)
    return max(1, min(value, 10000))


def _resource_import_batch_size() -> int:
    value = int(get_settings().resource_import_batch_size or 1000)
    return max(1, min(value, 5000))


def _resource_import_max_file_bytes(_: str) -> int:
    return max(1, int(get_settings().resource_import_max_size_mb or 1024)) * 1024 * 1024


def _stage_resource_upload_file(file: UploadFile) -> tuple[str, dict[str, Any]]:
    task_id = str(uuid4())
    try:
        staged = stage_import_file_streams(
            task_id,
            [(file.filename or "uploaded", file.file)],
            max_files=1,
            max_size_resolver=_resource_import_max_file_bytes,
            max_total_bytes=_resource_import_max_file_bytes(file.filename or "uploaded"),
        )[0]
        return task_id, staged
    except UploadLimitError as exc:
        cleanup_import_task_staging(task_id)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except Exception:
        cleanup_import_task_staging(task_id)
        raise


def _normalize_duplicate_policy(value: str | None) -> Literal["overwrite", "keep"]:
    return "keep" if value == "keep" else "overwrite"


def _parse_import_row_indexes(value: str | None) -> set[int]:
    if not value:
        return set()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(parsed, list):
        raise HTTPException(status_code=400, detail="重复行处理参数格式不正确。")

    row_indexes: set[int] = set()
    for item in parsed:
        try:
            row_index = int(item)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="重复行处理参数必须是行号列表。") from exc
        if row_index > 0:
            row_indexes.add(row_index)
    return row_indexes


def _build_tm_import_result_payload(
    *,
    import_summary,
    collection_response_id: UUID | None,
    collection_response_name: str | None,
    resolved_source_language: str,
    resolved_target_language: str,
    refreshed_count: int,
) -> dict[str, Any]:
    return {
        "filename": import_summary.filename,
        "created_rows": import_summary.created_rows,
        "updated_rows": import_summary.updated_rows,
        "skipped_duplicate_rows": import_summary.skipped_duplicate_rows,
        "skipped_empty_rows": import_summary.skipped_empty_rows,
        "skipped_header_rows": import_summary.skipped_header_rows,
        "imported_rows": import_summary.imported_rows,
        "refreshed_segments": refreshed_count,
        "collection_id": str(collection_response_id) if collection_response_id is not None else None,
        "collection_name": collection_response_name,
        "source_language": resolved_source_language,
        "target_language": resolved_target_language,
    }


def _run_tm_resource_import_task(task_id: str, payload: dict[str, Any]) -> None:
    staging_task_id = str(payload.get("staging_task_id") or task_id)
    _set_import_task_status(task_id, "running", progress=5, message="记忆库导入开始处理。")
    try:
        raise_if_import_task_canceled(task_id)
        with SessionLocal() as db:
            file_payload = payload["file"]
            filename = file_payload.get("filename") or "uploaded.xlsx"
            extension = str(payload.get("extension") or "")
            collection_id = UUID(payload["collection_id"]) if payload.get("collection_id") else None
            creator_id = UUID(payload["creator_id"]) if payload.get("creator_id") else None
            collection_response_id = (
                UUID(payload["collection_response_id"])
                if payload.get("collection_response_id")
                else None
            )
            collection_response_name = payload.get("collection_response_name")
            resolved_source_language = str(payload["source_language"])
            resolved_target_language = str(payload["target_language"])
            normalized_duplicate_policy = _normalize_duplicate_policy(payload.get("duplicate_policy"))
            skipped_row_indexes = {
                int(item)
                for item in payload.get("skip_duplicate_row_indexes", [])
                if int(item) > 0
            }
            import_batch = create_resource_import_batch(
                db,
                resource_type="tm",
                resource_id=collection_id,
                filename=filename,
                file_path=file_payload["path"],
                file_format=extension,
                source_language=resolved_source_language,
                target_language=resolved_target_language,
                created_by_id=creator_id,
            )
            db.commit()

            def cancel_check() -> bool:
                return import_task_cancel_requested(task_id)

            try:
                _set_import_task_status(task_id, "running", progress=20, message=f"正在导入 {filename}。")
                if extension in SDLTM_EXTENSIONS:
                    import_summary = import_tm_from_sdltm_path(
                        db=db,
                        sdltm_path=file_payload["path"],
                        filename=filename,
                        collection_id=collection_id,
                        source_language=resolved_source_language,
                        target_language=resolved_target_language,
                        creator_id=creator_id,
                        duplicate_policy=normalized_duplicate_policy,
                        skip_duplicate_row_indexes=skipped_row_indexes,
                        batch_size=_resource_import_batch_size(),
                        cancel_check=cancel_check,
                        import_batch_id=import_batch.id,
                    )
                elif extension in TMX_EXTENSIONS:
                    import_summary = import_tm_from_tmx_path(
                        db=db,
                        tmx_path=file_payload["path"],
                        filename=filename,
                        collection_id=collection_id,
                        source_language=resolved_source_language,
                        target_language=resolved_target_language,
                        creator_id=creator_id,
                        duplicate_policy=normalized_duplicate_policy,
                        skip_duplicate_row_indexes=skipped_row_indexes,
                        batch_size=_resource_import_batch_size(),
                        cancel_check=cancel_check,
                        import_batch_id=import_batch.id,
                    )
                elif extension in XLSX_EXTENSIONS:
                    import_summary = import_tm_from_xlsx_path(
                        db=db,
                        xlsx_path=file_payload["path"],
                        filename=filename,
                        collection_id=collection_id,
                        source_language=resolved_source_language,
                        target_language=resolved_target_language,
                        creator_id=creator_id,
                        duplicate_policy=normalized_duplicate_policy,
                        skip_duplicate_row_indexes=skipped_row_indexes,
                        skip_header=bool(payload.get("skip_header")),
                        batch_size=_resource_import_batch_size(),
                        cancel_check=cancel_check,
                        import_batch_id=import_batch.id,
                    )
                else:
                    import_summary = import_tm_from_upload(
                        db=db,
                        raw_bytes=read_import_file_bytes(file_payload),
                        filename=filename,
                        collection_id=collection_id,
                        source_language=resolved_source_language,
                        target_language=resolved_target_language,
                        creator_id=creator_id,
                        duplicate_policy=normalized_duplicate_policy,
                        skip_duplicate_row_indexes=skipped_row_indexes,
                        skip_header=bool(payload.get("skip_header")),
                        batch_size=_resource_import_batch_size(),
                        cancel_check=cancel_check,
                        import_batch_id=import_batch.id,
                    )
            except ImportTaskCanceled:
                db.rollback()
                raise
            except Exception as exc:
                db.rollback()
                raise RuntimeError(f"TM 导入失败：{exc}") from exc
            raise_if_import_task_canceled(task_id)
            db.commit()

            refreshed_count = 0
            try:
                _set_import_task_status(task_id, "running", progress=90, message="正在刷新相关匹配与通知。")
                raise_if_import_task_canceled(task_id)
                if collection_response_id is not None and import_summary.imported_rows > 0:
                    refreshed_count = _notify_tm_collections_changed(db, [collection_response_id])
                if collection_response_id is not None:
                    notification_title, notification_body = build_resource_import_notification(
                        resource_label="记忆库",
                        resource_name=collection_response_name or "",
                        filename=import_summary.filename,
                        imported_rows=import_summary.imported_rows,
                        created_rows=import_summary.created_rows,
                        updated_rows=import_summary.updated_rows,
                        skipped_empty_rows=import_summary.skipped_empty_rows,
                        skipped_header_rows=import_summary.skipped_header_rows,
                        source_language=resolved_source_language,
                        target_language=resolved_target_language,
                    )
                    create_operation_notification(
                        db,
                        user_id=creator_id,
                        notification_type="resource_import",
                        title=notification_title,
                        body=notification_body,
                    )
                    db.commit()
            except ImportTaskCanceled:
                db.rollback()
                raise
            except Exception:
                db.rollback()
                refreshed_count = 0
                logger.exception("TM import post-processing failed")

            raise_if_import_task_canceled(task_id)
            result = _build_tm_import_result_payload(
                import_summary=import_summary,
                collection_response_id=collection_response_id,
                collection_response_name=collection_response_name,
                resolved_source_language=resolved_source_language,
                resolved_target_language=resolved_target_language,
                refreshed_count=refreshed_count,
            )
            _set_import_task_status(
                task_id,
                "completed",
                progress=100,
                message="记忆库导入完成。",
                result=result,
            )
    except ImportTaskCanceled:
        _set_import_task_status(task_id, "canceled", progress=100, message="记忆库导入已取消。")
    except Exception as exc:
        logger.exception("TM resource import task failed task_id=%s", task_id)
        _set_import_task_status(
            task_id,
            "failed",
            progress=100,
            message="记忆库导入失败。",
            error=str(exc),
        )
    finally:
        cleanup_import_task_staging(staging_task_id)


@router.post("/translation-memory/import/preview")
@router.post("/tm/import/preview", include_in_schema=False)
async def preview_tm_xlsx(
    file: UploadFile = File(...),
    collection_id: UUID | None = Form(default=None),
    source_language: str = Form(...),
    target_language: str = Form(...),
    duplicate_policy: str = Form(default="keep"),
    preview_limit: int = Form(default=100),
    skip_header: bool = Form(default=False),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    extension = _validate_tm_import_upload(file)
    task_id, staged_file = await asyncio.to_thread(_stage_resource_upload_file, file)

    collection = _get_collection_or_404(db, collection_id)
    resolved_source_language, resolved_target_language = _resolve_collection_language_pair(
        collection,
        source_language,
        target_language,
    )
    normalized_duplicate_policy = _normalize_duplicate_policy(duplicate_policy)

    try:
        if extension in SDLTM_EXTENSIONS:
            preview = preview_tm_from_sdltm_path(
                db=db,
                sdltm_path=staged_file["path"],
                filename=file.filename or "uploaded.sdltm",
                collection_id=collection_id,
                source_language=resolved_source_language,
                target_language=resolved_target_language,
                duplicate_policy=normalized_duplicate_policy,
                preview_limit=max(1, min(preview_limit, 500)),
                skip_header=skip_header,
                max_scan_rows=_resource_import_preview_max_scan_rows(),
            )
        elif extension in TMX_EXTENSIONS:
            preview = preview_tm_from_tmx_path(
                db=db,
                tmx_path=staged_file["path"],
                filename=file.filename or "uploaded.tmx",
                collection_id=collection_id,
                source_language=resolved_source_language,
                target_language=resolved_target_language,
                duplicate_policy=normalized_duplicate_policy,
                preview_limit=max(1, min(preview_limit, 500)),
                skip_header=skip_header,
                max_scan_rows=_resource_import_preview_max_scan_rows(),
            )
        elif extension in XLSX_EXTENSIONS:
            preview = preview_tm_from_xlsx_path(
                db=db,
                xlsx_path=staged_file["path"],
                filename=file.filename or "uploaded.xlsx",
                collection_id=collection_id,
                source_language=resolved_source_language,
                target_language=resolved_target_language,
                duplicate_policy=normalized_duplicate_policy,
                preview_limit=max(1, min(preview_limit, 500)),
                skip_header=skip_header,
                max_scan_rows=_resource_import_preview_max_scan_rows(),
            )
        else:
            preview = preview_tm_from_upload(
                db=db,
                raw_bytes=read_import_file_bytes(staged_file),
                filename=file.filename or "uploaded.xlsx",
                collection_id=collection_id,
                source_language=resolved_source_language,
                target_language=resolved_target_language,
                duplicate_policy=normalized_duplicate_policy,
                preview_limit=max(1, min(preview_limit, 500)),
                skip_header=skip_header,
                max_scan_rows=_resource_import_preview_max_scan_rows(),
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TM 预览失败：{exc}") from exc
    finally:
        cleanup_import_task_staging(task_id)

    return {
        "filename": preview.filename,
        "rows": [
            {
                "row_index": row.row_index,
                "source_text": row.source_text,
                "target_text": row.target_text,
                "status": row.status,
                "message": row.message,
            }
            for row in preview.rows
        ],
        "total_rows": preview.total_rows,
        "valid_rows": preview.valid_rows,
        "create_rows": preview.create_rows,
        "update_rows": preview.update_rows,
        "keep_rows": preview.keep_rows,
        "duplicate_rows": preview.duplicate_rows,
        "skipped_empty_rows": preview.skipped_empty_rows,
        "skipped_header_rows": preview.skipped_header_rows,
        "preview_limit": preview.preview_limit,
        "duplicate_policy": preview.duplicate_policy,
        "scanned_rows": preview.scanned_rows,
        "truncated": preview.truncated,
        "max_scan_rows": _resource_import_preview_max_scan_rows(),
        "collection_id": str(collection.id) if collection else None,
        "collection_name": collection.name if collection else "",
        "source_language": resolved_source_language,
        "target_language": resolved_target_language,
    }


@router.post("/translation-memory/import-xlsx")
@router.post("/tm/import-xlsx", include_in_schema=False)
@router.post("/translation-memory/import", include_in_schema=False)
@router.post("/tm/import", include_in_schema=False)
async def import_tm_xlsx(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    collection_id: UUID | None = Form(default=None),
    source_language: str = Form(...),
    target_language: str = Form(...),
    duplicate_policy: str = Form(default="keep"),
    skip_duplicate_row_indexes: str = Form(default="[]"),
    skip_header: bool = Form(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    extension = _validate_tm_import_upload(file)
    task_id, staged_file = await asyncio.to_thread(_stage_resource_upload_file, file)
    try:
        collection = _get_collection_or_404(db, collection_id)
        resolved_source_language, resolved_target_language = _resolve_collection_language_pair(
            collection,
            source_language,
            target_language,
        )
        db.commit()
        collection_response_id = collection.id if collection is not None else None
        collection_response_name = collection.name if collection is not None else None
        skipped_row_indexes = _parse_import_row_indexes(skip_duplicate_row_indexes)

        payload = {
            "kind": "tm_resource_import",
            "staging_task_id": task_id,
            "file": staged_file,
            "extension": extension,
            "collection_id": str(collection_id) if collection_id is not None else None,
            "collection_response_id": str(collection_response_id) if collection_response_id is not None else None,
            "collection_response_name": collection_response_name,
            "source_language": resolved_source_language,
            "target_language": resolved_target_language,
            "duplicate_policy": _normalize_duplicate_policy(duplicate_policy),
            "skip_duplicate_row_indexes": sorted(skipped_row_indexes),
            "skip_header": bool(skip_header),
            "creator_id": str(current_user.id),
        }
        _set_import_task_status(task_id, "queued", progress=0, message="记忆库导入任务已进入队列。")
        await _queue_tm_resource_import_task(task_id, payload)
        return JSONResponse(
            status_code=202,
            content={
                "task_id": task_id,
                "status": "queued",
                "progress": 0,
                "message": "记忆库导入任务已进入队列。",
            },
        )
    except Exception:
        cleanup_import_task_staging(task_id)
        raise


@router.get("/translation-memory/collections/{collection_id}/entries")
@router.get("/tm/collections/{collection_id}/entries", include_in_schema=False)
def list_tm_collection_entries(
    collection_id: UUID,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
    case_sensitive: bool = False,
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
        if case_sensitive:
            query = query.filter(
                or_(
                    TranslationMemory.source_text.like(like_pattern),
                    TranslationMemory.target_text.like(like_pattern),
                )
            )
        else:
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
            _entry_user_name(entry.creator) or "",
            entry.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            _entry_user_name(entry.last_modified_by) or "",
            entry.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
        ]
        for entry in entries
    ]
    xlsx_bytes = build_tabular_xlsx(
        sheet_title=collection.name,
        headers=["原文", "译文", "源语言", "目标语言", "创建人", "创建时间", "最后修改人", "更新时间"],
        rows=rows,
    )
    return build_xlsx_download_response(f"{collection.name}-tm.xlsx", xlsx_bytes)


@router.post("/translation-memory/collections/{collection_id}/exports")
def queue_tm_collection_export(
    collection_id: UUID,
    format: ResourceExportFormat = Query(default="xlsx"),
    db: Session = Depends(get_db),
):
    collection = _get_collection_or_404(db, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="TM 记忆库不存在。")

    return JSONResponse(
        status_code=202,
        content=queue_resource_export(
            resource_type="tm",
            resource_id=collection.id,
            export_format=format,
        ),
    )


@router.get("/translation-memory/export-tasks/{task_id}")
def get_tm_export_task(task_id: str):
    return ensure_export_task_status(task_id, expected_resource_type="tm")


@router.post("/translation-memory/export-tasks/{task_id}/cancel")
def cancel_tm_export_task(task_id: str):
    return cancel_resource_export_task(task_id, expected_resource_type="tm")


@router.get("/translation-memory/export-tasks/{task_id}/download")
def download_tm_export_task(task_id: str):
    return build_resource_export_download_response(task_id, expected_resource_type="tm")


@router.post("/translation-memory/collections/{collection_id}/entries")
def add_tm_collection_entry(
    collection_id: UUID,
    payload: TMEntryUpdatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
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
        current_user,
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
    current_user: User = Depends(require_admin),
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
        if existing.creator_id is None:
            existing.creator_id = current_user.id
        existing.last_modified_by_id = current_user.id
        db.commit()
        sync_tm_embeddings(db, [(existing.id, existing.source_text)])
        _notify_tm_collections_changed(db, [existing.collection_id] if existing.collection_id else [])
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
        creator_id=current_user.id,
        last_modified_by_id=current_user.id,
    )
    db.add(tm)
    db.commit()
    db.refresh(tm)
    sync_tm_embeddings(db, [(tm.id, tm.source_text)])
    _notify_tm_collections_changed(db, [tm.collection_id] if tm.collection_id else [])

    return {"status": "created", "id": tm.id, "message": "已添加新记录。"}


@router.put("/translation-memory/entries/{entry_id}")
@router.put("/tm/entries/{entry_id}", include_in_schema=False)
def update_tm_entry(
    entry_id: UUID,
    payload: TMEntryUpdatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
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
    if entry.creator_id is None:
        entry.creator_id = current_user.id
    entry.last_modified_by_id = current_user.id
    db.commit()
    db.refresh(entry)
    sync_tm_embeddings(db, [(entry.id, entry.source_text)])
    _notify_tm_collections_changed(db, [entry.collection_id] if entry.collection_id else [])
    return _serialize_tm_entry(entry)


@router.delete("/translation-memory/entries/{entry_id}")
@router.delete("/tm/entries/{entry_id}", include_in_schema=False)
def delete_tm_entry(
    entry_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    entry = db.query(TranslationMemory).filter(TranslationMemory.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="TM 条目不存在。")

    db.delete(entry)
    db.commit()
    return {"message": "TM 条目已删除。"}


def _upsert_tm_entry(
    db: Session,
    source_text: str,
    target_text: str,
    collection_id: UUID,
    source_language: str,
    target_language: str,
) -> tuple[Literal["created", "updated"], MemoryEntry]:
    source_hash = build_source_hash(source_text)
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
        return "updated", existing

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
    return "created", tm


@router.post("/translation-memory/entries/batch")
@router.post("/tm/batch-add", include_in_schema=False)
def batch_add_tm_entries(
    batch: BatchTMEntry,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """批量添加 TM 记录（去重）"""
    skipped_count = 0
    upsert_entries: list[TMUpsertEntry] = []
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

        collection = _get_collection_or_404(db, collection_id)
        source_language, target_language = _resolve_collection_language_pair(
            collection,
            entry.source_language or batch.source_language,
            entry.target_language or batch.target_language,
        )
        upsert_entries.append(
            TMUpsertEntry(
                collection_id=collection_id,
                source_text=source_text,
                target_text=target_text,
                source_language=source_language,
                target_language=target_language,
                creator_id=current_user.id,
            )
        )

    upsert_summary = batch_upsert_tm_entries(db, upsert_entries)
    skipped_count += upsert_summary.skipped_count
    refreshed_count = 0
    if upsert_summary.total_written > 0:
        db.commit()
        sync_tm_embeddings(db, upsert_summary.sync_rows or [])
        refreshed_count = _notify_tm_collections_changed(
            db,
            [entry.collection_id for entry in upsert_entries],
        )

    return {
        "created": upsert_summary.created_count,
        "updated": upsert_summary.updated_count,
        "skipped": skipped_count,
        "refreshed_segments": refreshed_count,
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
@router.post("/termbase/import", include_in_schema=False)
def import_termbase_xlsx(
    file: UploadFile = File(...),
    collection_id: UUID | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    # 定义为同步 def，由 FastAPI 调度到线程池执行，避免术语库导入的解析/写库阻塞事件循环。
    extension = f".{(file.filename or '').split('.')[-1].lower()}" if file.filename else ""
    if extension not in TERM_IMPORT_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持上传 .tmx、.xls、.xlsx 或 .csv 文件。")

    task_id, staged_file = _stage_resource_upload_file(file)

    collection = _get_termbase_collection_or_404(db, collection_id)
    collection_response_id = collection.id if collection is not None else None
    collection_response_name = collection.name if collection is not None else None
    collection_source_language = collection.source_language if collection is not None else "zh"
    collection_target_language = collection.target_language if collection is not None else "en"

    try:
        if extension in TBX_EXTENSIONS:
            import_summary = import_terms_from_tbx_path(
                db=db,
                tbx_path=staged_file["path"],
                filename=file.filename or "uploaded.tbx",
                term_base_id=collection_id,
                source_language=collection_source_language,
                target_language=collection_target_language,
                batch_size=_resource_import_batch_size(),
            )
        elif extension in TMX_EXTENSIONS:
            import_summary = import_terms_from_tmx_path(
                db=db,
                tmx_path=staged_file["path"],
                filename=file.filename or "uploaded.tmx",
                term_base_id=collection_id,
                source_language=collection_source_language,
                target_language=collection_target_language,
                batch_size=_resource_import_batch_size(),
            )
        elif extension == ".xlsx":
            import_summary = import_terms_from_xlsx_path(
                db=db,
                xlsx_path=staged_file["path"],
                filename=file.filename or "uploaded.xlsx",
                term_base_id=collection_id,
                source_language=collection_source_language,
                target_language=collection_target_language,
                batch_size=_resource_import_batch_size(),
            )
        else:
            import_summary = import_terms_from_xlsx_upload(
                db=db,
                raw_bytes=read_import_file_bytes(staged_file),
                filename=file.filename or "uploaded.xlsx",
                term_base_id=collection_id,
                source_language=collection_source_language,
                target_language=collection_target_language,
                batch_size=_resource_import_batch_size(),
            )
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"术语库导入失败：{exc}") from exc
    finally:
        cleanup_import_task_staging(task_id)

    try:
        if collection_response_id is not None:
            notification_title, notification_body = build_resource_import_notification(
                resource_label="术语库",
                resource_name=collection_response_name or "",
                filename=import_summary.filename,
                imported_rows=import_summary.imported_rows,
                created_rows=import_summary.created_rows,
                updated_rows=import_summary.updated_rows,
                skipped_empty_rows=import_summary.skipped_empty_rows,
                skipped_header_rows=import_summary.skipped_header_rows,
                source_language=collection_source_language,
                target_language=collection_target_language,
            )
            create_operation_notification(
                db,
                user_id=current_user.id,
                notification_type="resource_import",
                title=notification_title,
                body=notification_body,
            )
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("Termbase import post-processing failed")

    return {
        "filename": import_summary.filename,
        "created_rows": import_summary.created_rows,
        "updated_rows": import_summary.updated_rows,
        "skipped_rows": import_summary.skipped_empty_rows + import_summary.skipped_header_rows,
        "skipped_empty_rows": import_summary.skipped_empty_rows,
        "skipped_header_rows": import_summary.skipped_header_rows,
        "imported_rows": import_summary.imported_rows,
        "collection_id": collection_response_id,
        "collection_name": collection_response_name,
    }


@router.get("/termbase/match")
def match_terms(
    text: str,
    collection_ids: list[UUID] | None = None,
    case_sensitive: bool = False,
    db: Session = Depends(get_db),
):
    """匹配文本中的术语，返回匹配到的术语列表（长术语优先）"""
    match_text = normalize_text(text or "")
    if not match_text:
        return {"matches": []}

    # 防止未绑定术语库时全表扫描 term_entries。
    if not collection_ids:
        return {"matches": []}

    candidate_query = (
        db.query(
            TermEntry.id,
            TermEntry.term_base_id,
            TermEntry.source_text,
            TermEntry.target_text,
            TermBase.name.label("term_base_name"),
        )
        .outerjoin(TermBase, TermEntry.term_base_id == TermBase.id)
        .filter(
            TermEntry.term_base_id.in_(collection_ids),
            TermEntry.source_text != "",
            func.length(TermEntry.source_text) <= len(match_text),
        )
        .order_by(func.length(TermEntry.source_text).desc(), TermEntry.updated_at.desc())
        .limit(1000)
    )
    if case_sensitive:
        candidate_query = candidate_query.filter(literal(match_text).contains(TermEntry.source_text))
    else:
        candidate_query = candidate_query.filter(
            literal(match_text.lower()).contains(func.lower(TermEntry.source_text))
        )

    candidate_terms = candidate_query.all()
    term_matches = find_non_overlapping_term_text_matches(
        text,
        candidate_terms,
        lambda term: term.source_text,
        case_sensitive=case_sensitive,
    )
    return {
        "matches": [
            {
                "term_id": str(term_match.item.id),
                "term_base_id": str(term_match.item.term_base_id),
                "term_base_name": term_match.item.term_base_name,
                "source_text": term_match.item.source_text,
                "target_text": term_match.item.target_text,
                "start": term_match.start,
                "end": term_match.end,
            }
            for term_match in term_matches
        ],
    }
