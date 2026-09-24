from __future__ import annotations

import logging
import asyncio
import shutil
from pathlib import Path

import anyio.to_thread
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import get_settings, validate_runtime_settings
from app.database import SessionLocal, engine
from app.logging import configure_logging
from app.routers.api import router as api_router
from app.routers.auth import router as auth_router
from app.routers.glossary_base import router as glossary_base_router
from app.routers.term_base import router as term_base_router
from app.routers.reference import router as reference_router
from app.routers.tools import router as tools_router
from app.routers.proofreading import router as proofreading_router
from app.routers.document_alignment import router as document_alignment_router
from app.services.guideline_repository import seed_guideline_templates_from_files
from app.services.import_task_storage import initialize_import_task_storage
from app.services.schema_setup import ensure_runtime_schema


configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()
validate_runtime_settings(settings)
ensure_runtime_schema()


def _engine_pool_stats() -> dict[str, int | str]:
    """返回 SQLAlchemy 连接池的实时使用情况，便于监控连接是否接近打满。"""
    pool = engine.pool
    stats: dict[str, int | str] = {}
    for key, getter in (
        ("size", getattr(pool, "size", None)),
        ("checked_in", getattr(pool, "checkedin", None)),
        ("checked_out", getattr(pool, "checkedout", None)),
        ("overflow", getattr(pool, "overflow", None)),
    ):
        if callable(getter):
            try:
                stats[key] = getter()
            except Exception:  # noqa: BLE001
                continue
    return stats


frontend_dist_dir = Path("frontend/dist")
frontend_assets_dir = frontend_dist_dir / "assets"
frontend_version_file = frontend_dist_dir / "app-version.txt"

SPA_ENTRY_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}
SPA_ASSET_CACHE_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
}


def _get_app_version() -> str:
    try:
        built_version = frontend_version_file.read_text(encoding="utf-8").strip()
    except OSError:
        built_version = ""
    if built_version:
        return built_version

    configured_version = (settings.app_version or "").strip()
    return configured_version or "dev"


class CacheControlStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.update(SPA_ASSET_CACHE_HEADERS)
        return response


def _spa_entry_response(index_path: Path) -> FileResponse:
    return FileResponse(index_path, headers=SPA_ENTRY_CACHE_HEADERS)


def _spa_file_response(path: Path) -> FileResponse:
    return FileResponse(path, headers=SPA_ENTRY_CACHE_HEADERS)

RESOURCE_IMPORT_REQUEST_PATHS = frozenset(
    {
        "/api/glossary-bases/import/preview",
        "/api/glossary-bases/import-xlsx",
        "/api/term-bases/import/preview",
        "/api/term-bases/import-xlsx",
        "/api/term-bases/import",
        "/api/translation-memory/import/preview",
        "/api/translation-memory/import-xlsx",
        "/api/translation-memory/import",
        "/api/tm/import/preview",
        "/api/tm/import-xlsx",
        "/api/tm/import",
        "/api/termbase/import-xlsx",
        "/api/termbase/import",
    }
)


class RequestBodyTooLarge(Exception):
    """请求体实际读取字节数超过当前端点预算。"""


class RequestBodyLimitMiddleware:
    """在 multipart 解析期间按实际接收字节计数，兼容 chunked 请求。"""

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.asgi_app(scope, receive, send)
            return

        path = str(scope.get("path") or "/").rstrip("/") or "/"
        if path in RESOURCE_IMPORT_REQUEST_PATHS:
            max_request_mb = (
                max(int(settings.resource_import_max_size_mb), 1)
                + max(int(settings.resource_import_request_overhead_mb), 0)
            )
        else:
            max_request_mb = max(int(settings.upload_max_request_size_mb), 1)
        max_request_bytes = max_request_mb * 1024 * 1024

        content_length: bytes | None = None
        for key, value in scope.get("headers", []):
            if key.lower() == b"content-length":
                content_length = value
                break
        if content_length is not None:
            try:
                declared_size = int(content_length.decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                declared_size = -1
            if declared_size > max_request_bytes:
                await self._send_rejection(scope, receive, send, max_request_mb)
                return
            large_upload_threshold = max(int(settings.ai_inline_max_size_mb), 1) * 1024 * 1024
            if path not in RESOURCE_IMPORT_REQUEST_PATHS and declared_size > large_upload_threshold:
                import_root = Path(settings.import_task_dir)
                import_root.mkdir(parents=True, exist_ok=True)
                reserve_bytes = max(int(settings.ai_min_free_disk_mb), 1) * 1024 * 1024
                # multipart spool、任务暂存和永久源文件在导入完成前可能同时存在。
                required_bytes = declared_size * 3 + reserve_bytes
                if shutil.disk_usage(import_root).free < required_bytes:
                    await self._send_disk_rejection(scope, receive, send, required_bytes)
                    return

        received_bytes = 0
        limit_exceeded = False

        async def limited_receive():
            nonlocal limit_exceeded, received_bytes
            message = await receive()
            if message.get("type") == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > max_request_bytes:
                    limit_exceeded = True
                    raise RequestBodyTooLarge
            return message

        response_started = False

        async def tracked_send(message):
            nonlocal response_started
            # FastAPI 可能把 receive 的异常转换为 400；超限后屏蔽该响应，统一改发 413。
            if limit_exceeded:
                return
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.asgi_app(scope, limited_receive, tracked_send)
        except Exception:
            if not limit_exceeded:
                raise

        if limit_exceeded and not response_started:
            await self._send_rejection(scope, receive, send, max_request_mb)

    @staticmethod
    async def _send_disk_rejection(scope, receive, send, required_bytes: int) -> None:
        required_gib = round(required_bytes / (1024 ** 3), 2)
        response = JSONResponse(
            status_code=507,
            content={"detail": f"上传磁盘空间不足，至少需要 {required_gib} GiB 可用空间。"},
        )
        await response(scope, receive, send)

    @staticmethod
    async def _send_rejection(scope, receive, send, max_request_mb: int) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": f"请求体超过服务器上限（{max_request_mb} MB）。"},
        )
        await response(scope, receive, send)


app = FastAPI(title=settings.app_name)

# 后添加的 CORS 位于请求体限制器外层，确保 413 响应也带正确的跨域响应头。
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api")
app.include_router(api_router, prefix="/api")
app.include_router(term_base_router, prefix="/api")
app.include_router(reference_router, prefix="/api")
app.include_router(glossary_base_router, prefix="/api")
app.include_router(tools_router, prefix="/api")
app.include_router(proofreading_router, prefix="/api")
app.include_router(document_alignment_router, prefix="/api")


@app.on_event("startup")
async def _configure_runtime() -> None:
    app.state.review_sync_worker = asyncio.create_task(_review_sync_recovery_loop())
    storage_state = initialize_import_task_storage()
    logger.info("upload storage initialized: %s", storage_state)
    with SessionLocal() as db:
        seeded_guideline_count = seed_guideline_templates_from_files(db)
    if seeded_guideline_count:
        logger.info("seeded %s translation guideline templates", seeded_guideline_count)
    # 同步接口由 FastAPI 调度到 anyio 线程池执行，按需调大其容量以匹配并发与连接池规模。
    if settings.server_threadpool_size and settings.server_threadpool_size > 0:
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = settings.server_threadpool_size
        logger.info("anyio thread pool size set to %s", settings.server_threadpool_size)
    logger.info(
        "DB pool config: pool_size=%s max_overflow=%s pool_timeout=%s pgbouncer_mode=%s application_name=%s",
        settings.database_pool_size,
        settings.database_max_overflow,
        settings.database_pool_timeout,
        settings.database_pgbouncer_transaction_mode,
        settings.database_application_name,
    )


async def _review_sync_recovery_loop() -> None:
    from app.services.review_sync import run_review_sync_once
    while True:
        try:
            await asyncio.to_thread(run_review_sync_once)
        except Exception:
            logger.exception("review sync recovery failed")
        await asyncio.sleep(15)


@app.on_event("shutdown")
async def _stop_review_sync_worker() -> None:
    task = getattr(app.state, "review_sync_worker", None)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@app.get("/api/health", include_in_schema=False)
def health_check():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="数据库连接不可用。") from exc
    return {"status": "ok", "database": "ok", "db_pool": _engine_pool_stats()}


@app.get("/api/app-version", include_in_schema=False)
def app_version():
    return JSONResponse({"version": _get_app_version()}, headers=SPA_ENTRY_CACHE_HEADERS)


if frontend_assets_dir.exists():
    app.mount("/assets", CacheControlStaticFiles(directory=frontend_assets_dir), name="spa-assets")


def _resolve_spa_asset(full_path: str) -> Path | None:
    if not frontend_dist_dir.exists():
        return None

    requested_path = (frontend_dist_dir / full_path).resolve()
    frontend_root = frontend_dist_dir.resolve()
    if frontend_root not in requested_path.parents and requested_path != frontend_root:
        return None
    if requested_path.is_file():
        return requested_path
    return None


@app.get("/", include_in_schema=False)
def serve_spa_root():
    index_path = frontend_dist_dir / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="前端构建产物不存在，请先运行 frontend 构建。")
    return _spa_entry_response(index_path)


@app.get("/{full_path:path}", include_in_schema=False)
def serve_spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")

    asset_path = _resolve_spa_asset(full_path)
    if asset_path is not None:
        return _spa_file_response(asset_path)

    index_path = frontend_dist_dir / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="前端构建产物不存在，请先运行 frontend 构建。")
    return _spa_entry_response(index_path)
