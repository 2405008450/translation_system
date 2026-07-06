from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Any
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET

from app.config import get_settings
from app.services.libreoffice_service import (
    LibreOfficeError,
    compute_libreoffice_document_statistics,
)
from app.services.document_match_analysis import normalize_document_match_analysis


APP_PROPERTIES_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
STATISTIC_FIELDS = {
    "Pages": "pages",
    "Words": "words",
    "Characters": "characters",
    "CharactersWithSpaces": "characters_with_spaces",
    "Paragraphs": "paragraphs",
    "Lines": "lines",
}
STATISTIC_NUMBER_KEYS = (
    "pages",
    "words",
    "non_asian_words",
    "asian_characters",
    "characters",
    "characters_with_spaces",
    "paragraphs",
    "lines",
    "internal_repeated_words",
    "internal_repeated_characters",
    "cross_file_repeated_words",
    "cross_file_repeated_characters",
)
NON_ASIAN_WORD_PATTERN = re.compile(
    r"[A-Za-z0-9]+(?:[.,:/_-][A-Za-z0-9]+)*%?|[^\W_\d\s]+(?:[-'][^\W_\d\s]+)*",
    re.UNICODE,
)

_LICENSE_LOCK = Lock()
_LICENSE_STATUS: str | None = None


def compute_docx_statistics(raw_bytes: bytes) -> dict[str, Any]:
    """计算 DOCX 文档级统计，保留旧调用入口。"""
    return compute_word_document_statistics(raw_bytes, "source.docx")


def compute_word_document_statistics(raw_bytes: bytes, filename: str) -> dict[str, Any]:
    """计算 Word 文档级统计，优先使用 LibreOffice。"""
    libreoffice_statistics = _compute_with_libreoffice(raw_bytes, filename)
    if libreoffice_statistics is not None:
        return libreoffice_statistics

    if Path(filename or "").suffix.lower() != ".docx":
        return _build_statistics_payload(
            source="unavailable",
            engine=None,
            include_textboxes_footnotes_endnotes=True,
            license_status=None,
        )

    return _compute_docx_statistics_without_libreoffice(raw_bytes)


def _compute_with_libreoffice(raw_bytes: bytes, filename: str) -> dict[str, Any] | None:
    try:
        return compute_libreoffice_document_statistics(raw_bytes, filename)
    except LibreOfficeError:
        return None


def _compute_docx_statistics_without_libreoffice(raw_bytes: bytes) -> dict[str, Any]:
    """计算 DOCX 文档级统计，优先使用 Aspose，失败时用 OpenXML 直接重算文本统计。"""
    aspose_statistics = _compute_with_aspose(raw_bytes)
    if aspose_statistics is not None:
        return aspose_statistics

    openxml_statistics = _compute_with_openxml(raw_bytes)
    if openxml_statistics is not None:
        return openxml_statistics

    cached_statistics = _read_cached_docprops_statistics(raw_bytes)
    if cached_statistics is not None:
        return cached_statistics

    return _build_statistics_payload(
        source="unavailable",
        engine=None,
        include_textboxes_footnotes_endnotes=True,
        license_status=None,
    )


def normalize_document_statistics(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        if not value:
            return {}
        return _coerce_statistics_payload(value)
    if isinstance(value, str) and value.strip():
        try:
            import json

            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        if isinstance(decoded, dict):
            if not decoded:
                return {}
            return _coerce_statistics_payload(decoded)
    return {}


def serialize_document_statistics(value: Any) -> str:
    import json

    statistics = normalize_document_statistics(value)
    return json.dumps(statistics, ensure_ascii=False, sort_keys=True)


def _compute_with_aspose(raw_bytes: bytes) -> dict[str, Any] | None:
    try:
        import aspose.words as aw
    except Exception:
        return None

    license_status = _apply_aspose_license(aw)
    try:
        with TemporaryDirectory(prefix="docx-statistics-") as temp_dir:
            docx_path = Path(temp_dir) / "source.docx"
            docx_path.write_bytes(raw_bytes)
            document = aw.Document(str(docx_path))
            document.include_textboxes_footnotes_endnotes_in_stat = False
            document.update_word_count(True)
            properties = document.built_in_document_properties

            payload = _build_statistics_payload(
                source="aspose",
                engine="aspose-words",
                include_textboxes_footnotes_endnotes=False,
                license_status=license_status,
            )
            payload.update(
                {
                    "pages": _first_int(
                        getattr(document, "page_count", None),
                        getattr(properties, "pages", None),
                    ),
                    "words": _to_optional_int(getattr(properties, "words", None)),
                    "characters": _to_optional_int(getattr(properties, "characters", None)),
                    "characters_with_spaces": _to_optional_int(
                        getattr(properties, "characters_with_spaces", None)
                    ),
                    "paragraphs": _to_optional_int(getattr(properties, "paragraphs", None)),
                    "lines": _to_optional_int(getattr(properties, "lines", None)),
                }
            )
            return payload
    except Exception:
        return None


def _compute_with_openxml(raw_bytes: bytes) -> dict[str, Any] | None:
    try:
        with ZipFile(BytesIO(raw_bytes)) as archive:
            document_xml = archive.read("word/document.xml")
            cached_statistics = _read_cached_docprops_statistics_from_archive(archive)
    except (BadZipFile, KeyError):
        return None

    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError:
        return None

    paragraphs = [
        text
        for text in _iter_visible_paragraph_texts(root, include_textboxes=False)
        if text.strip()
    ]
    if not paragraphs:
        return _build_statistics_payload(
            source="openxml_computed",
            engine="openxml-text",
            include_textboxes_footnotes_endnotes=False,
            license_status=None,
        )

    payload = _build_statistics_payload(
        source="openxml_computed",
        engine="openxml-text",
        include_textboxes_footnotes_endnotes=False,
        license_status=None,
    )
    payload.update(
        {
            "pages": _to_optional_int((cached_statistics or {}).get("pages")),
            "words": _count_word_words(paragraphs),
            "characters": _count_characters(paragraphs, include_spaces=False),
            "characters_with_spaces": _count_characters(paragraphs, include_spaces=True),
            "paragraphs": len(paragraphs),
            "lines": _trusted_cached_line_count(cached_statistics, paragraph_count=len(paragraphs)),
        }
    )
    return payload


def _apply_aspose_license(aw_module: Any) -> str:
    global _LICENSE_STATUS

    settings = get_settings()
    license_path = (settings.aspose_words_license_path or "").strip()
    if not license_path:
        return "unlicensed"

    with _LICENSE_LOCK:
        if _LICENSE_STATUS is not None:
            return _LICENSE_STATUS

        path = Path(license_path)
        if not path.is_file():
            _LICENSE_STATUS = "license_missing"
            return _LICENSE_STATUS

        try:
            license_obj = aw_module.License()
            license_obj.set_license(str(path))
        except Exception:
            _LICENSE_STATUS = "license_error"
        else:
            _LICENSE_STATUS = "licensed"
        return _LICENSE_STATUS


def _read_cached_docprops_statistics(raw_bytes: bytes) -> dict[str, Any] | None:
    try:
        with ZipFile(BytesIO(raw_bytes)) as archive:
            return _read_cached_docprops_statistics_from_archive(archive)
    except BadZipFile:
        return None


def _read_cached_docprops_statistics_from_archive(archive: ZipFile) -> dict[str, Any] | None:
    try:
        xml_bytes = archive.read("docProps/app.xml")
    except KeyError:
        return None
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    payload = _build_statistics_payload(
        source="docprops_cached",
        engine="openxml-docprops",
        include_textboxes_footnotes_endnotes=None,
        license_status=None,
    )
    for element_name, key in STATISTIC_FIELDS.items():
        element = root.find(f"{{{APP_PROPERTIES_NS}}}{element_name}")
        payload[key] = _to_optional_int(element.text if element is not None else None)
    return payload


def _iter_visible_paragraph_texts(root: ET.Element, *, include_textboxes: bool) -> Iterable[str]:
    def walk(node: ET.Element, *, in_textbox: bool = False) -> Iterable[str]:
        next_in_textbox = in_textbox or node.tag == _w_tag("txbxContent")
        if next_in_textbox and not include_textboxes:
            return
        if node.tag == _w_tag("p"):
            yield _extract_paragraph_text(node, include_textboxes=include_textboxes)
            if include_textboxes:
                for child in node:
                    if child.tag == _w_tag("txbxContent"):
                        yield from walk(child, in_textbox=True)
                    else:
                        for textbox in child.findall(f".//{_w_tag('txbxContent')}"):
                            yield from walk(textbox, in_textbox=True)
            return
        for child in node:
            yield from walk(child, in_textbox=next_in_textbox)

    yield from walk(root)


def _extract_paragraph_text(paragraph: ET.Element, *, include_textboxes: bool) -> str:
    parts: list[str] = []

    def walk(node: ET.Element, *, deleted: bool = False) -> None:
        if node.tag == _w_tag("txbxContent") and not include_textboxes:
            return
        next_deleted = deleted or node.tag == _w_tag("del")
        if not next_deleted:
            if node.tag == _w_tag("t"):
                parts.append(node.text or "")
                return
            if node.tag == _w_tag("tab"):
                parts.append("\t")
                return
            if node.tag == _w_tag("br"):
                parts.append("\n")
                return
        for child in node:
            walk(child, deleted=next_deleted)

    walk(paragraph)
    return "".join(parts)


def _count_word_words(paragraphs: Iterable[str]) -> int:
    word_count = 0
    for text in paragraphs:
        east_asian_chars = sum(1 for char in text if _is_east_asian_word_count_char(char))
        non_asian_text = "".join(
            " " if _is_east_asian_word_count_char(char) else char
            for char in text
        )
        word_count += east_asian_chars + len(NON_ASIAN_WORD_PATTERN.findall(non_asian_text))
    return word_count


def _count_characters(paragraphs: Iterable[str], *, include_spaces: bool) -> int:
    count = 0
    for text in paragraphs:
        for char in text:
            if char in "\r\n":
                continue
            if not include_spaces and char.isspace():
                continue
            count += 1
    return count


def _trusted_cached_line_count(
    cached_statistics: dict[str, Any] | None,
    *,
    paragraph_count: int,
) -> int | None:
    cached_lines = _to_optional_int((cached_statistics or {}).get("lines"))
    if cached_lines is None or cached_lines < paragraph_count:
        return None
    return cached_lines


def _is_east_asian_word_count_char(char: str) -> bool:
    if char.isspace() or char.isascii():
        return False
    return unicodedata.east_asian_width(char) in {"W", "F"}


def _w_tag(local_name: str) -> str:
    return f"{{{WORD_NS}}}{local_name}"


def _build_statistics_payload(
    *,
    source: str,
    engine: str | None,
    include_textboxes_footnotes_endnotes: bool | None,
    license_status: str | None,
    engine_version: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": source,
        "engine": engine,
        "engine_version": engine_version,
        "license_status": license_status,
        "include_textboxes_footnotes_endnotes": include_textboxes_footnotes_endnotes,
        "match_analysis": None,
    }
    for key in STATISTIC_NUMBER_KEYS:
        payload[key] = None
    return payload


def _coerce_statistics_payload(value: dict[str, Any]) -> dict[str, Any]:
    payload = _build_statistics_payload(
        source=str(value.get("source") or ""),
        engine=value.get("engine") if value.get("engine") is None else str(value.get("engine")),
        engine_version=(
            value.get("engine_version")
            if value.get("engine_version") is None
            else str(value.get("engine_version"))
        ),
        include_textboxes_footnotes_endnotes=_to_optional_bool(
            value.get("include_textboxes_footnotes_endnotes")
        ),
        license_status=(
            value.get("license_status")
            if value.get("license_status") is None
            else str(value.get("license_status"))
        ),
    )
    for key in STATISTIC_NUMBER_KEYS:
        payload[key] = _to_optional_int(value.get(key))
    payload["match_analysis"] = normalize_document_match_analysis(value.get("match_analysis"))
    return payload


def _configured_license_status_hint() -> str:
    license_path = (get_settings().aspose_words_license_path or "").strip()
    if not license_path:
        return "unlicensed"
    return "not_applied"


def _first_int(*values: Any) -> int | None:
    for value in values:
        converted = _to_optional_int(value)
        if converted is not None:
            return converted
    return None


def _to_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)
