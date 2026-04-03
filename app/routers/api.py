from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.document_workspace import build_docx_workspace
from app.services.slate_parser import parse_docx_for_slate


router = APIRouter()


def _validate_docx_upload(file: UploadFile) -> None:
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="仅支持 DOCX 文件。")


@router.post("/parser/slate")
async def upload_for_slate(
    file: UploadFile = File(...),
    threshold: float = Form(default=0.6),
    db: Session = Depends(get_db),
):
    _validate_docx_upload(file)

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")

    try:
        result = parse_docx_for_slate(db=db, raw_bytes=raw_bytes, similarity_threshold=threshold)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/parser/workspace")
async def upload_for_workspace(
    file: UploadFile = File(...),
    threshold: float = Form(default=0.6),
    db: Session = Depends(get_db),
):
    _validate_docx_upload(file)

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")

    try:
        return build_docx_workspace(
            db=db,
            raw_bytes=raw_bytes,
            similarity_threshold=threshold,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
