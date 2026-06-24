from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import GuidelineTemplate


logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDELINE_TEMPLATE_DIR = REPO_ROOT / "prompt_templates" / "translation_guidelines"
SUPPORTED_GUIDELINE_IMPORT_EXTENSIONS = frozenset({".md", ".markdown", ".txt"})
CANONICAL_GUIDELINE_EXTENSION = ".md"
MAX_GUIDELINE_TEMPLATE_BYTES = 256 * 1024

_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_WHITESPACE_RE = re.compile(r"\s+")


def list_guideline_templates(db: Session) -> list[GuidelineTemplate]:
    return (
        db.query(GuidelineTemplate)
        .order_by(GuidelineTemplate.updated_at.desc(), GuidelineTemplate.name.asc())
        .all()
    )


def read_guideline_template(db: Session, template_id: str) -> GuidelineTemplate:
    template = db.get(GuidelineTemplate, _resolve_template_id(template_id))
    if template is None:
        raise FileNotFoundError("翻译细则模板不存在。")
    return template


def save_guideline_template(
    db: Session,
    filename: str,
    raw_bytes: bytes,
    *,
    user_id: UUID | None = None,
) -> GuidelineTemplate:
    if not filename:
        raise ValueError("文件名不能为空。")
    if not raw_bytes:
        raise ValueError("上传的细则文件为空。")
    if len(raw_bytes) > MAX_GUIDELINE_TEMPLATE_BYTES:
        raise ValueError("细则文件过大，请控制在 256KB 以内。")

    source_path = Path(filename)
    source_ext = source_path.suffix.lower()
    if source_ext not in SUPPORTED_GUIDELINE_IMPORT_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_GUIDELINE_IMPORT_EXTENSIONS))
        raise ValueError(f"仅支持导入文本细则文件：{supported}。")

    content = _prepare_content(_decode_text(raw_bytes))
    safe_stem = _safe_template_stem(source_path.stem)
    return _insert_with_available_id(
        db,
        safe_stem,
        content,
        source_filename=filename,
        source_path=None,
        user_id=user_id,
    )


def update_guideline_template(
    db: Session,
    template_id: str,
    content: str,
    *,
    user_id: UUID | None = None,
) -> GuidelineTemplate:
    template = read_guideline_template(db, template_id)
    normalized_content = _prepare_content(content)

    template.content = normalized_content
    template.name = _extract_template_name(normalized_content, template.id)
    template.content_hash = _content_hash(normalized_content)
    template.size_bytes = len(normalized_content.encode("utf-8"))
    if user_id is not None:
        template.last_modified_by_id = user_id
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


def delete_guideline_template(db: Session, template_id: str) -> None:
    template = read_guideline_template(db, template_id)
    db.delete(template)
    db.commit()


def seed_guideline_templates_from_files(db: Session) -> int:
    if not GUIDELINE_TEMPLATE_DIR.exists():
        return 0

    imported_count = 0
    for path in sorted(GUIDELINE_TEMPLATE_DIR.iterdir()):
        if not _is_seed_candidate(path):
            continue
        template_id = _safe_template_stem(path.stem)
        if db.get(GuidelineTemplate, template_id) is not None:
            continue
        try:
            raw_bytes = path.read_bytes()
            if not raw_bytes or len(raw_bytes) > MAX_GUIDELINE_TEMPLATE_BYTES:
                continue
            content = _prepare_content(_decode_text(raw_bytes))
            _insert_template(
                db,
                template_id=template_id,
                content=content,
                source_filename=path.name,
                source_path=_display_seed_path(path),
                user_id=None,
            )
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            logger.warning("skip guideline seed file %s: %s", path, exc)
            continue
        except IntegrityError:
            db.rollback()
            continue
        imported_count += 1
    return imported_count


def _insert_with_available_id(
    db: Session,
    stem: str,
    content: str,
    *,
    source_filename: str,
    source_path: str | None,
    user_id: UUID | None,
) -> GuidelineTemplate:
    for index in range(1, 1000):
        template_id = stem if index == 1 else f"{stem}-{index}"
        if db.get(GuidelineTemplate, template_id) is not None:
            continue
        try:
            return _insert_template(
                db,
                template_id=template_id,
                content=content,
                source_filename=source_filename,
                source_path=source_path,
                user_id=user_id,
            )
        except IntegrityError:
            db.rollback()
            continue
    raise ValueError("同名细则模板过多，请换一个文件名后再导入。")


def _insert_template(
    db: Session,
    *,
    template_id: str,
    content: str,
    source_filename: str,
    source_path: str | None,
    user_id: UUID | None,
) -> GuidelineTemplate:
    template = GuidelineTemplate(
        id=template_id,
        name=_extract_template_name(content, template_id),
        filename=f"{template_id}{CANONICAL_GUIDELINE_EXTENSION}",
        content=content,
        content_hash=_content_hash(content),
        size_bytes=len(content.encode("utf-8")),
        source_path=source_path or source_filename,
        created_by_id=user_id,
        last_modified_by_id=user_id,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


def _prepare_content(content: str) -> str:
    normalized_content = (content or "").strip()
    if not normalized_content:
        raise ValueError("翻译规则内容不能为空。")

    stored_content = normalized_content + "\n"
    if len(stored_content.encode("utf-8")) > MAX_GUIDELINE_TEMPLATE_BYTES:
        raise ValueError("翻译规则内容过大，请控制在 256KB 以内。")
    return stored_content


def _decode_text(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别细则文件编码，请使用 UTF-8 或 GB18030 文本。")


def _safe_template_stem(stem: str) -> str:
    normalized = _UNSAFE_FILENAME_RE.sub("-", stem).strip(" .-")
    normalized = _WHITESPACE_RE.sub("-", normalized)
    return (normalized[:80].strip(" .-") or "translation-guidelines")


def _resolve_template_id(template_id: str) -> str:
    resolved = (template_id or "").strip()
    if not resolved or "/" in resolved or "\\" in resolved:
        raise FileNotFoundError("翻译细则模板不存在。")
    return resolved


def _is_seed_candidate(path: Path) -> bool:
    if not path.is_file() or path.name.startswith(".") or path.name.lower() == "readme.md":
        return False
    return path.suffix.lower() in SUPPORTED_GUIDELINE_IMPORT_EXTENSIONS


def _display_seed_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return path.name


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _extract_template_name(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title[:80]
    return fallback.replace("-", " ")[:80]
