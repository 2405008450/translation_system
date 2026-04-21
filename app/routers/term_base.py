from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models import TermBase, TermEntry, User
from app.services.language_pairs import require_language_pair
from app.services.normalizer import normalize_match_text, normalize_text
from app.services.term_importer import (
    XLSX_EXTENSIONS,
    import_terms_from_xlsx_upload,
)


router = APIRouter(dependencies=[Depends(get_current_user)])


class TermBasePayload(BaseModel):
    name: str
    description: str | None = None
    source_language: str
    target_language: str


class TermEntryUpdatePayload(BaseModel):
    source_text: str
    target_text: str


def _normalize_term_base_name(name: str) -> str:
    return " ".join(name.strip().split())


def _require_term_language_pair(
    source_language: str | None,
    target_language: str | None,
) -> tuple[str, str]:
    try:
        return require_language_pair(source_language, target_language)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _get_term_base_or_404(db: Session, term_base_id: UUID) -> TermBase:
    term_base = db.query(TermBase).filter(TermBase.id == term_base_id).first()
    if term_base is None:
        raise HTTPException(status_code=404, detail="术语库不存在。")
    return term_base


def _resolve_term_base_language_pair(
    term_base: TermBase,
    source_language: str | None,
    target_language: str | None,
) -> tuple[str, str]:
    if term_base.source_language and term_base.target_language:
        normalized_source_language, normalized_target_language = _require_term_language_pair(
            source_language,
            target_language,
        )
        if (
            normalized_source_language != term_base.source_language
            or normalized_target_language != term_base.target_language
        ):
            raise HTTPException(status_code=400, detail="所选术语库的语言对与本次导入不一致。")
        return normalized_source_language, normalized_target_language

    normalized_source_language, normalized_target_language = _require_term_language_pair(
        source_language,
        target_language,
    )
    term_base.source_language = normalized_source_language
    term_base.target_language = normalized_target_language
    return normalized_source_language, normalized_target_language


def _serialize_term_base(term_base: TermBase, entry_count: int = 0) -> dict:
    return {
        "id": term_base.id,
        "name": term_base.name,
        "description": term_base.description,
        "source_language": term_base.source_language,
        "target_language": term_base.target_language,
        "created_at": term_base.created_at.isoformat(),
        "updated_at": term_base.updated_at.isoformat(),
        "entry_count": entry_count,
    }


def _serialize_term_entry(entry: TermEntry) -> dict:
    return {
        "id": entry.id,
        "term_base_id": entry.term_base_id,
        "source_text": entry.source_text,
        "target_text": entry.target_text,
        "source_language": entry.source_language,
        "target_language": entry.target_language,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
    }


@router.get("/term-bases")
def list_term_bases(db: Session = Depends(get_db)):
    rows = (
        db.query(TermBase, func.count(TermEntry.id).label("entry_count"))
        .outerjoin(TermEntry, TermEntry.term_base_id == TermBase.id)
        .group_by(TermBase.id)
        .order_by(TermBase.created_at.desc())
        .all()
    )
    return [
        _serialize_term_base(term_base, int(entry_count))
        for term_base, entry_count in rows
    ]


@router.get("/term-bases/{term_base_id}")
def get_term_base(
    term_base_id: UUID,
    db: Session = Depends(get_db),
):
    term_base = _get_term_base_or_404(db, term_base_id)
    entry_count = (
        db.query(TermEntry)
        .filter(TermEntry.term_base_id == term_base.id)
        .count()
    )
    return _serialize_term_base(term_base, entry_count)


@router.post("/term-bases")
def create_term_base(
    payload: TermBasePayload,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    name = _normalize_term_base_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="术语库名称不能为空。")
    source_language, target_language = _require_term_language_pair(
        payload.source_language,
        payload.target_language,
    )

    term_base = TermBase(
        name=name,
        description=normalize_text(payload.description or "") or None,
        source_language=source_language,
        target_language=target_language,
    )
    db.add(term_base)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名术语库已存在。") from exc

    db.refresh(term_base)
    return _serialize_term_base(term_base)


@router.put("/term-bases/{term_base_id}")
def update_term_base(
    term_base_id: UUID,
    payload: TermBasePayload,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    term_base = _get_term_base_or_404(db, term_base_id)
    name = _normalize_term_base_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="术语库名称不能为空。")
    source_language, target_language = _require_term_language_pair(
        payload.source_language,
        payload.target_language,
    )

    term_base.name = name
    term_base.description = normalize_text(payload.description or "") or None
    term_base.source_language = source_language
    term_base.target_language = target_language
    (
        db.query(TermEntry)
        .filter(TermEntry.term_base_id == term_base.id)
        .update(
            {
                TermEntry.source_language: source_language,
                TermEntry.target_language: target_language,
            },
            synchronize_session=False,
        )
    )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名术语库已存在。") from exc

    db.refresh(term_base)
    entry_count = (
        db.query(TermEntry)
        .filter(TermEntry.term_base_id == term_base.id)
        .count()
    )
    return _serialize_term_base(term_base, entry_count)


@router.delete("/term-bases/{term_base_id}")
def delete_term_base(
    term_base_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    term_base = _get_term_base_or_404(db, term_base_id)
    entry_count = (
        db.query(TermEntry)
        .filter(TermEntry.term_base_id == term_base.id)
        .count()
    )
    if entry_count:
        raise HTTPException(status_code=409, detail="请先清空术语库中的术语条目。")

    db.delete(term_base)
    db.commit()
    return {"message": "术语库已删除。"}


@router.post("/term-bases/import-xlsx")
async def import_term_base_xlsx(
    file: UploadFile = File(...),
    term_base_id: UUID | None = Form(default=None),
    source_language: str = Form(...),
    target_language: str = Form(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if term_base_id is None:
        raise HTTPException(status_code=400, detail="请先选择要导入的术语库。")

    extension = f".{(file.filename or '').split('.')[-1].lower()}" if file.filename else ""
    if extension not in XLSX_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持上传 .xlsx 文件。")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="上传的 XLSX 文件为空。")

    term_base = _get_term_base_or_404(db, term_base_id)
    resolved_source_language, resolved_target_language = _resolve_term_base_language_pair(
        term_base,
        source_language,
        target_language,
    )

    try:
        import_summary = import_terms_from_xlsx_upload(
            db=db,
            raw_bytes=raw_bytes,
            filename=file.filename or "uploaded.xlsx",
            term_base_id=term_base_id,
            source_language=resolved_source_language,
            target_language=resolved_target_language,
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"术语库导入失败：{exc}") from exc

    return {
        "filename": import_summary.filename,
        "created_rows": import_summary.created_rows,
        "updated_rows": import_summary.updated_rows,
        "skipped_empty_rows": import_summary.skipped_empty_rows,
        "skipped_header_rows": import_summary.skipped_header_rows,
        "imported_rows": import_summary.imported_rows,
        "term_base_id": term_base.id,
        "term_base_name": term_base.name,
        "source_language": resolved_source_language,
        "target_language": resolved_target_language,
    }


@router.get("/term-bases/{term_base_id}/entries")
def list_term_base_entries(
    term_base_id: UUID,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
    db: Session = Depends(get_db),
):
    term_base = _get_term_base_or_404(db, term_base_id)
    safe_skip = max(skip, 0)
    safe_limit = min(max(limit, 1), 200)
    query = (
        db.query(TermEntry)
        .filter(TermEntry.term_base_id == term_base.id)
    )
    normalized_search = normalize_text(search or "")
    if normalized_search:
        like_pattern = f"%{normalized_search}%"
        query = query.filter(
            or_(
                TermEntry.source_text.ilike(like_pattern),
                TermEntry.target_text.ilike(like_pattern),
            )
        )

    total = query.count()
    rows = (
        query
        .order_by(TermEntry.updated_at.desc(), TermEntry.created_at.desc())
        .offset(safe_skip)
        .limit(safe_limit)
        .all()
    )
    return {
        "items": [_serialize_term_entry(row) for row in rows],
        "total": total,
        "skip": safe_skip,
        "limit": safe_limit,
    }


@router.put("/term-entries/{entry_id}")
def update_term_entry(
    entry_id: UUID,
    payload: TermEntryUpdatePayload,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    entry = db.query(TermEntry).filter(TermEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="术语条目不存在。")

    source_text = normalize_text(payload.source_text)
    target_text = normalize_text(payload.target_text)
    if not source_text or not target_text:
        raise HTTPException(status_code=400, detail="术语原文和译文不能为空。")

    term_base = _get_term_base_or_404(db, entry.term_base_id)
    source_language = term_base.source_language or entry.source_language
    target_language = term_base.target_language or entry.target_language
    source_normalized = normalize_match_text(source_text) or source_text

    duplicate = (
        db.query(TermEntry)
        .filter(
            TermEntry.id != entry.id,
            TermEntry.term_base_id == entry.term_base_id,
            TermEntry.source_language == source_language,
            TermEntry.target_language == target_language,
            or_(
                TermEntry.source_normalized == source_normalized,
                TermEntry.source_text == source_text,
            ),
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="当前术语库中已存在相同原文的术语条目。")

    entry.source_text = source_text
    entry.target_text = target_text
    entry.source_normalized = source_normalized
    entry.source_language = source_language
    entry.target_language = target_language
    db.commit()
    db.refresh(entry)
    return _serialize_term_entry(entry)
