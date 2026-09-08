from __future__ import annotations

import json
import unicodedata
from typing import Any

from app.services.adapters.exceptions import ExportError
from app.services.adapters.psd_runtime import PsdRuntimeError, export_psd_document


PSD_MEDIA_TYPE = "image/vnd.adobe.photoshop"


class PsdExporter:
    """将译文写回 PSD 可编辑文本图层。"""

    def export(self, original_bytes: bytes, segments: list[Any]) -> bytes:
        translations: list[dict[str, Any]] = []
        for segment in segments:
            target_text = str(_get_value(segment, "target_text", "") or "")
            if not target_text.strip():
                continue

            source_text = str(
                _get_value(segment, "display_text", "")
                or _get_value(segment, "source_text", "")
            )
            # 富文本和路径文本即使内容未变，只要重新写回也可能触发 Photoshop
            # 重排。仅空白或标点发生变化时保留原图层及其像素缓存。
            if not _has_meaningful_text_change(source_text, target_text):
                continue

            metadata = _load_metadata(segment)
            layer_path = metadata.get("layer_path")
            if layer_path is None or str(layer_path) == "":
                continue
            translations.append(
                {
                    "entity_type": str(metadata.get("entity_type") or "PSD_TEXT"),
                    "layer_path": str(layer_path),
                    "layer_id": metadata.get("layer_id"),
                    "source_text": source_text,
                    "target_text": target_text,
                    "ocr_bounds": metadata.get("ocr_bounds"),
                    "ocr_confidence": metadata.get("ocr_confidence"),
                    "font_size": metadata.get("font_size"),
                    "background_color": metadata.get("background_color"),
                    "foreground_color": metadata.get("foreground_color"),
                }
            )

        if not translations:
            return original_bytes

        try:
            content, _result = export_psd_document(original_bytes, translations)
        except PsdRuntimeError as exc:
            raise ExportError(format="PSD", reason=str(exc)) from exc
        return content


def _has_meaningful_text_change(source_text: str, target_text: str) -> bool:
    return _text_content_key(source_text) != _text_content_key(target_text)


def _text_content_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


def _get_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _load_metadata(segment: Any) -> dict[str, Any]:
    raw_metadata = _get_value(segment, "metadata", None)
    if raw_metadata is None:
        raw_metadata = _get_value(segment, "segment_metadata", {})
    if isinstance(raw_metadata, dict):
        return raw_metadata
    if isinstance(raw_metadata, str) and raw_metadata.strip():
        try:
            parsed = json.loads(raw_metadata)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
