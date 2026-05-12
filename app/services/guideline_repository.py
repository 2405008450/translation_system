from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDELINE_TEMPLATE_DIR = REPO_ROOT / "prompt_templates" / "translation_guidelines"
SUPPORTED_GUIDELINE_IMPORT_EXTENSIONS = frozenset({".md", ".markdown", ".txt"})
CANONICAL_GUIDELINE_EXTENSION = ".md"
MAX_GUIDELINE_TEMPLATE_BYTES = 256 * 1024

_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class GuidelineTemplate:
    id: str
    name: str
    filename: str
    content: str
    size_bytes: int
    updated_at: datetime


def list_guideline_templates() -> list[GuidelineTemplate]:
    """列出仓库内可复用的翻译细则 Markdown 模板。"""
    GUIDELINE_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    templates: list[GuidelineTemplate] = []
    for path in sorted(GUIDELINE_TEMPLATE_DIR.glob(f"*{CANONICAL_GUIDELINE_EXTENSION}")):
        if path.name.lower() == "readme.md" or path.name.startswith("."):
            continue
        templates.append(_read_template_from_path(path))
    return templates


def read_guideline_template(template_id: str) -> GuidelineTemplate:
    path = _resolve_template_path(template_id)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("翻译细则模板不存在。")
    return _read_template_from_path(path)


def save_guideline_template(filename: str, raw_bytes: bytes) -> GuidelineTemplate:
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

    content = _decode_text(raw_bytes).strip()
    if not content:
        raise ValueError("细则文件没有可用文本内容。")

    safe_stem = _safe_template_stem(source_path.stem)
    GUIDELINE_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    target_path = _next_available_path(safe_stem)
    target_path.write_text(content + "\n", encoding="utf-8")
    return _read_template_from_path(target_path)


def update_guideline_template(template_id: str, content: str) -> GuidelineTemplate:
    path = _resolve_template_path(template_id)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("翻译细则模板不存在。")

    normalized_content = (content or "").strip()
    if not normalized_content:
        raise ValueError("翻译规则内容不能为空。")
    if len(normalized_content.encode("utf-8")) > MAX_GUIDELINE_TEMPLATE_BYTES:
        raise ValueError("翻译规则内容过大，请控制在 256KB 以内。")

    path.write_text(normalized_content + "\n", encoding="utf-8")
    return _read_template_from_path(path)


def delete_guideline_template(template_id: str) -> None:
    path = _resolve_template_path(template_id)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("翻译细则模板不存在。")
    path.unlink()


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


def _next_available_path(stem: str) -> Path:
    candidate = GUIDELINE_TEMPLATE_DIR / f"{stem}{CANONICAL_GUIDELINE_EXTENSION}"
    if not candidate.exists():
        return candidate

    for index in range(2, 1000):
        candidate = GUIDELINE_TEMPLATE_DIR / f"{stem}-{index}{CANONICAL_GUIDELINE_EXTENSION}"
        if not candidate.exists():
            return candidate
    raise ValueError("同名细则模板过多，请换一个文件名后再导入。")


def _resolve_template_path(template_id: str) -> Path:
    stem = Path(template_id or "").stem
    if not stem or stem != template_id or any(char in template_id for char in ("/", "\\")):
        raise FileNotFoundError("翻译细则模板不存在。")
    path = (GUIDELINE_TEMPLATE_DIR / f"{stem}{CANONICAL_GUIDELINE_EXTENSION}").resolve()
    root = GUIDELINE_TEMPLATE_DIR.resolve()
    if root not in path.parents:
        raise FileNotFoundError("翻译细则模板不存在。")
    return path


def _read_template_from_path(path: Path) -> GuidelineTemplate:
    content = path.read_text(encoding="utf-8")
    stat = path.stat()
    return GuidelineTemplate(
        id=path.stem,
        name=_extract_template_name(content, path.stem),
        filename=path.name,
        content=content,
        size_bytes=stat.st_size,
        updated_at=datetime.fromtimestamp(stat.st_mtime),
    )


def _extract_template_name(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title[:80]
    return fallback.replace("-", " ")[:80]
