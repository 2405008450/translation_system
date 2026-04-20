from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings, validate_runtime_settings
from app.logging import configure_logging
from app.routers.api import router as api_router
from app.routers.auth import router as auth_router


configure_logging()
settings = get_settings()
validate_runtime_settings(settings)
frontend_dist_dir = Path("frontend/dist")
frontend_assets_dir = frontend_dist_dir / "assets"

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


if frontend_assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=frontend_assets_dir), name="spa-assets")


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
    return FileResponse(index_path)


@app.get("/{full_path:path}", include_in_schema=False)
def serve_spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")

    asset_path = _resolve_spa_asset(full_path)
    if asset_path is not None:
        return FileResponse(asset_path)

    index_path = frontend_dist_dir / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="前端构建产物不存在，请先运行 frontend 构建。")
    return FileResponse(index_path)
