"""通用文档工具 API。"""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.auth import get_current_user
from app.services import quote_converter

router = APIRouter(prefix="/tools", tags=["tools"], dependencies=[Depends(get_current_user)])
logger = logging.getLogger(__name__)

# 与常规上传保持一致的上限，避免大文件把内存打满。
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@router.post("/quote-convert", response_class=Response)
async def convert_quotes(
    file: UploadFile = File(...),
    scope_width: str = Form(...),
    scope_shape: str = Form(...),
    target_width: str = Form(...),
    target_shape: str = Form(...),
) -> Response:
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="缺少上传文件")

    if not quote_converter.is_supported(filename):
        supported = ", ".join(sorted(quote_converter.SUPPORTED_EXTS))
        raise HTTPException(status_code=400, detail=f"仅支持以下格式：{supported}")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传的文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"文件大小超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 限制")

    try:
        result_bytes, content_type = quote_converter.convert_bytes(
            data, filename,
            scope_width, scope_shape, target_width, target_shape,
        )
    except quote_converter.QuoteConverterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("引号转换失败：%s", filename)
        raise HTTPException(status_code=500, detail="引号转换失败，请稍后重试") from exc

    timestamp = datetime.now().strftime("%m%d_%H%M")
    output_name = quote_converter.build_output_filename(
        filename, scope_width, scope_shape, target_width, target_shape, timestamp,
    )
    disposition = (
        f"attachment; filename=\"{quote(output_name)}\"; "
        f"filename*=UTF-8''{quote(output_name)}"
    )

    return Response(
        content=result_bytes,
        media_type=content_type,
        headers={"Content-Disposition": disposition},
    )
