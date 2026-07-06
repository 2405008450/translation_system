from __future__ import annotations

import logging
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

app = FastAPI(title=settings.app_name)

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


@app.on_event("startup")
async def _configure_runtime() -> None:
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
