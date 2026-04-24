from __future__ import annotations

import json
import mimetypes
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Any
import re

import yaml

from app.services.adapters.export_formats import get_supported_exports
from app.services.adapters.tmx_exporter import TmxExporter
from app.services.adapters.xliff_exporter import XliffExporter


class MultiFormatExporter:
    def __init__(
        self,
        source_lang: str = "zh-CN",
        target_lang: str = "en-US",
    ):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.tmx_exporter = TmxExporter(source_lang, target_lang)
        self.xliff_exporter = XliffExporter(source_lang, target_lang)

    def get_available_exports(self, filename: str) -> list[dict]:
        extension = Path(filename).suffix.lower()
        return [
            {
                "id": option.id,
                "name": option.name,
                "description": option.description,
                "extension": option.extension or extension,
            }
            for option in get_supported_exports(extension)
        ]

    def export(
        self,
        export_type: str,
        segments: list[Any],
        filename: str,
        original_bytes: bytes | None = None,
    ) -> tuple[bytes, str, str]:
        normalized_segments = self._normalize_segments(segments)
        translation_maps = self._build_translation_maps(normalized_segments)
        extension = Path(filename).suffix.lower()
        base_name = Path(filename).stem or "translated"

        if export_type == "original":
            return self._export_original(
                extension,
                filename,
                original_bytes,
                translation_maps,
            )
        if export_type == "bilingual":
            return self._export_bilingual_original(
                extension,
                base_name,
                normalized_segments,
                original_bytes,
                translation_maps,
            )
        if export_type == "bilingual_docx":
            return self._export_bilingual_docx(normalized_segments, base_name)
        if export_type == "bilingual_txt":
            return self._export_bilingual_txt(normalized_segments, base_name)
        if export_type == "tmx":
            return self._export_tmx(normalized_segments, base_name)
        if export_type in {"xliff", "xliff2"}:
            version = "2.0" if export_type == "xliff2" else "1.2"
            return self._export_xliff(normalized_segments, filename, version)

        raise ValueError(f"Unsupported export type: {export_type}")

    def _normalize_segments(self, segments: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, segment in enumerate(segments):
            if isinstance(segment, dict):
                item = dict(segment)
            else:
                item = {
                    "segment_id": getattr(segment, "segment_id", None),
                    "sentence_id": getattr(segment, "sentence_id", None),
                    "source_text": getattr(segment, "source_text", ""),
                    "display_text": getattr(segment, "display_text", ""),
                    "target_text": getattr(segment, "target_text", ""),
                    "status": getattr(segment, "status", "none"),
                    "matched_source_text": getattr(segment, "matched_source_text", ""),
                    "block_type": getattr(segment, "block_type", "paragraph"),
                    "block_index": getattr(segment, "block_index", index),
                    "row_index": getattr(segment, "row_index", None),
                    "cell_index": getattr(segment, "cell_index", None),
                }

            sentence_id = item.get("sentence_id") or item.get("segment_id") or f"seg_{index}"
            item.setdefault("segment_id", sentence_id)
            item.setdefault("sentence_id", sentence_id)
            item.setdefault("source_text", "")
            item.setdefault("display_text", item.get("source_text", ""))
            item.setdefault("target_text", "")
            item.setdefault("status", "none")
            item.setdefault("matched_source_text", "")
            item.setdefault("block_type", "paragraph")
            item.setdefault("block_index", index)
            item.setdefault("row_index", None)
            item.setdefault("cell_index", None)
            normalized.append(item)
        return normalized

    def _build_translation_maps(self, segments: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
        source_candidates: dict[str, set[str]] = defaultdict(set)
        display_candidates: dict[str, set[str]] = defaultdict(set)
        key_map: dict[str, str] = {}
        path_map: dict[str, str] = {}
        row_col_map: dict[str, str] = {}
        index_map: dict[str, str] = {}
        segment_id_map: dict[str, str] = {}

        for segment in segments:
            target_text = str(segment.get("target_text") or "").strip()
            if not target_text:
                continue

            source_text = str(segment.get("source_text") or "").strip()
            display_text = str(segment.get("display_text") or "").strip()
            segment_id = str(segment.get("segment_id") or segment.get("sentence_id") or "")
            if segment_id:
                segment_id_map[segment_id] = target_text
            if source_text:
                source_candidates[source_text].add(target_text)
            if display_text:
                display_candidates[display_text].add(target_text)

            key = str(segment.get("key") or "").strip()
            if key:
                key_map[key] = target_text

            metadata_path = str(
                segment.get("metadata_path")
                or segment.get("json_path")
                or segment.get("path")
                or ""
            ).strip()
            if metadata_path:
                path_map[metadata_path] = target_text

            row_index = self._to_optional_int(segment.get("row_index"))
            cell_index = self._to_optional_int(segment.get("cell_index"))
            if row_index is not None and cell_index is not None:
                row_col_map[f"{row_index},{cell_index}"] = target_text

            subtitle_index = segment.get("subtitle_index", segment.get("index"))
            if subtitle_index is not None and str(subtitle_index).strip():
                index_map[str(subtitle_index).strip()] = target_text

        return {
            "source_text": self._collapse_unique_values(source_candidates),
            "display_text": self._collapse_unique_values(display_candidates),
            "key": key_map,
            "path": path_map,
            "row_col": row_col_map,
            "index": index_map,
            "segment_id": segment_id_map,
        }

    def _collapse_unique_values(self, candidates: dict[str, set[str]]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, values in candidates.items():
            if len(values) == 1:
                result[key] = next(iter(values))
        return result

    def _export_original(
        self,
        extension: str,
        filename: str,
        original_bytes: bytes | None,
        translation_maps: dict[str, dict[str, str]],
    ) -> tuple[bytes, str, str]:
        if original_bytes is None:
            raise ValueError("Original export requires the original source file.")

        text_map = self._build_text_translation_map(translation_maps)
        export_filename = self._build_translated_filename(filename)

        if extension == ".txt":
            content = self._replace_plain_text(original_bytes, text_map)
        elif extension in {".html", ".htm"}:
            from app.services.adapters.html_exporter import HtmlExporter

            content = HtmlExporter().export(original_bytes, text_map)
        elif extension in {".md", ".markdown"}:
            from app.services.adapters.markdown_exporter import MarkdownExporter

            content = MarkdownExporter().export(original_bytes, text_map)
        elif extension == ".csv":
            from app.services.adapters.csv_exporter import CsvExporter

            content = CsvExporter().export(
                original_bytes,
                {**text_map, **translation_maps["row_col"]},
            )
        elif extension == ".properties":
            from app.services.adapters.properties_exporter import PropertiesExporter

            content = PropertiesExporter().export(
                original_bytes,
                {**text_map, **translation_maps["key"]},
            )
        elif extension in {".po", ".pot"}:
            from app.services.adapters.po_exporter import PoExporter

            content = PoExporter().export(original_bytes, text_map)
        elif extension == ".strings":
            from app.services.adapters.strings_exporter import StringsExporter

            content = StringsExporter().export(
                original_bytes,
                {**text_map, **translation_maps["key"]},
            )
        elif extension == ".srt":
            from app.services.adapters.srt_exporter import SrtExporter

            content = SrtExporter().export(
                original_bytes,
                {**text_map, **translation_maps["index"]},
            )
        elif extension == ".json":
            content = self._export_json(original_bytes, text_map, translation_maps["path"])
        elif extension in {".yaml", ".yml"}:
            content = self._export_yaml(original_bytes, text_map, translation_maps["path"])
        elif extension == ".php":
            content = self._replace_plain_text(original_bytes, text_map)
        elif extension in {".dita", ".ditamap", ".xml"}:
            from app.services.adapters.dita_exporter import DitaExporter

            content = DitaExporter().export(original_bytes, text_map)
        elif extension == ".svg":
            from app.services.adapters.svg_exporter import SvgExporter

            content = SvgExporter().export(original_bytes, text_map)
        elif extension == ".sdlxliff":
            from app.services.adapters.sdlxliff_exporter import SdlxliffExporter

            content = SdlxliffExporter().export(
                original_bytes,
                {**text_map, **translation_maps["segment_id"]},
            )
        elif extension == ".txml":
            from app.services.adapters.txml_exporter import TxmlExporter

            content = TxmlExporter().export(original_bytes, text_map)
        elif extension == ".dxf":
            from app.services.adapters.dxf_exporter import DxfExporter

            content = DxfExporter().export(original_bytes, text_map)
        elif extension == ".idml":
            from app.services.adapters.idml_exporter import IdmlExporter

            content = IdmlExporter().export(original_bytes, text_map)
        elif extension == ".mif":
            from app.services.adapters.mif_exporter import MifExporter

            content = MifExporter().export(original_bytes, text_map)
        elif extension == ".zip":
            from app.services.adapters.zip_exporter import ZipExporter

            content = ZipExporter().export(original_bytes, text_map)
        elif extension == ".rar":
            from app.services.adapters.rar_exporter import RarExporter

            content = RarExporter().export(original_bytes, text_map)
        else:
            raise ValueError(f"Original export is not supported for {extension}.")

        return content, self._get_mime_type(extension), export_filename

    def _export_bilingual_original(
        self,
        extension: str,
        base_name: str,
        segments: list[dict[str, Any]],
        original_bytes: bytes | None,
        translation_maps: dict[str, dict[str, str]],
    ) -> tuple[bytes, str, str]:
        if original_bytes is None:
            raise ValueError("Bilingual export requires the original source file.")

        if extension in {".properties", ".po", ".pot", ".strings", ".html", ".htm", ".srt"}:
            if extension == ".properties":
                content = self._export_bilingual_properties(original_bytes, segments)
            elif extension in {".po", ".pot"}:
                content = self._export_bilingual_po(original_bytes, translation_maps["source_text"])
            elif extension == ".strings":
                content = self._export_bilingual_strings(original_bytes, segments)
            elif extension in {".html", ".htm"}:
                content = self._export_bilingual_html(original_bytes, segments)
            else:
                content = self._export_bilingual_srt(original_bytes, segments)
            return content, self._get_mime_type(extension), f"{base_name}-bilingual{extension}"

        return self._export_bilingual_txt(segments, base_name)

    def _export_bilingual_properties(
        self,
        original_bytes: bytes,
        segments: list[dict[str, Any]],
    ) -> bytes:
        from app.services.adapters.properties_exporter import PropertiesExporter

        exporter = PropertiesExporter()
        content = exporter._decode_content(original_bytes)
        lines = content.replace("\r\n", "\n").split("\n")
        source_to_target = self._build_source_to_target_map(segments)
        result_lines: list[str] = []
        for line in lines:
            if not line.strip() or line.lstrip().startswith(("#", "!")):
                result_lines.append(line)
                continue
            key, value, separator = exporter._parse_line(line)
            clean_value = value.strip()
            target = source_to_target.get(clean_value, "")
            bilingual_value = clean_value if not target else f"{clean_value} | {target}"
            result_lines.append(f"{key}{separator}{exporter._escape_value(bilingual_value)}")
        return "\n".join(result_lines).encode("utf-8")

    def _export_bilingual_po(
        self,
        original_bytes: bytes,
        translations: dict[str, str],
    ) -> bytes:
        from app.services.adapters.po_exporter import PoExporter

        return PoExporter().export(original_bytes, translations)

    def _export_bilingual_strings(
        self,
        original_bytes: bytes,
        segments: list[dict[str, Any]],
    ) -> bytes:
        from app.services.adapters.strings_exporter import StringsExporter

        exporter = StringsExporter()
        content = exporter._decode_content(original_bytes)
        source_to_target = self._build_source_to_target_map(segments)

        def replace_value(match):
            original_value = exporter._unescape(match.group(2))
            target = source_to_target.get(original_value, "")
            bilingual = original_value if not target else f"{original_value} | {target}"
            return f'"{match.group(1)}" = "{exporter._escape(bilingual)}";'

        pattern = r'"([^"\\]*(?:\\.[^"\\]*)*)"\s*=\s*"([^"\\]*(?:\\.[^"\\]*)*)"\s*;'
        return re.sub(pattern, replace_value, content).encode("utf-8")

    def _export_bilingual_html(
        self,
        original_bytes: bytes,
        segments: list[dict[str, Any]],
    ) -> bytes:
        import re

        from app.services.adapters.html_exporter import HtmlExporter

        exporter = HtmlExporter()
        content = exporter._decode_content(original_bytes)
        source_to_target = self._build_source_to_target_map(segments)

        def replace_text_node(match):
            text = match.group(1)
            stripped = text.strip()
            target = source_to_target.get(stripped, "")
            if not target:
                return match.group(0)
            leading = text[: len(text) - len(text.lstrip())]
            trailing = text[len(text.rstrip()) :]
            bilingual = f'{leading}{stripped}<br/><span style="color:#666;">{target}</span>{trailing}'
            return f">{bilingual}<"

        return re.sub(r">([^<]+)<", replace_text_node, content).encode("utf-8")

    def _export_bilingual_srt(
        self,
        original_bytes: bytes,
        segments: list[dict[str, Any]],
    ) -> bytes:
        import re

        from app.services.adapters.srt_exporter import TIMECODE_PATTERN, SrtExporter

        exporter = SrtExporter()
        content = exporter._decode_content(original_bytes).replace("\r\n", "\n").replace("\r", "\n")
        source_to_target = self._build_source_to_target_map(segments)
        blocks = re.split(r"\n\n+", content.strip())
        result_blocks: list[str] = []
        for block in blocks:
            lines = block.strip().split("\n")
            if len(lines) < 2 or not TIMECODE_PATTERN.match(lines[1]):
                result_blocks.append(block)
                continue
            original_text = "\n".join(lines[2:])
            clean_text = re.sub(r"<[^>]+>", "", original_text).strip()
            target = source_to_target.get(clean_text, "")
            if target:
                result_blocks.append(f"{lines[0]}\n{lines[1]}\n{original_text}\n{target}")
            else:
                result_blocks.append(block)
        return "\n\n".join(result_blocks).encode("utf-8")

    def _export_bilingual_docx(
        self,
        segments: list[dict[str, Any]],
        base_name: str,
    ) -> tuple[bytes, str, str]:
        from docx import Document
        from docx.enum.table import WD_TABLE_ALIGNMENT

        document = Document()
        document.add_heading("双语对照文档", level=1)
        table = document.add_table(rows=1, cols=2)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.rows[0].cells[0].text = "原文"
        table.rows[0].cells[1].text = "译文"

        for segment in segments:
            source_text = str(segment.get("source_text") or "")
            if not source_text:
                continue
            target_text = str(segment.get("target_text") or "")
            row = table.add_row()
            row.cells[0].text = source_text
            row.cells[1].text = target_text

        buffer = BytesIO()
        document.save(buffer)
        return (
            buffer.getvalue(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            f"{base_name}-bilingual.docx",
        )

    def _export_bilingual_txt(
        self,
        segments: list[dict[str, Any]],
        base_name: str,
    ) -> tuple[bytes, str, str]:
        lines: list[str] = []
        for index, segment in enumerate(segments, start=1):
            source_text = str(segment.get("source_text") or "")
            if not source_text:
                continue
            target_text = str(segment.get("target_text") or "")
            lines.append(f"[{index}] 原文: {source_text}")
            lines.append(f"[{index}] 译文: {target_text or '(未翻译)'}")
            lines.append("")

        return (
            "\n".join(lines).encode("utf-8"),
            "text/plain; charset=utf-8",
            f"{base_name}-bilingual.txt",
        )

    def _export_tmx(
        self,
        segments: list[dict[str, Any]],
        base_name: str,
    ) -> tuple[bytes, str, str]:
        content = self.tmx_exporter.export(segments, base_name)
        return content, "application/x-tmx+xml", f"{base_name}.tmx"

    def _export_xliff(
        self,
        segments: list[dict[str, Any]],
        filename: str,
        version: str,
    ) -> tuple[bytes, str, str]:
        extension = Path(filename).suffix.lower()
        format_map = {
            ".docx": "winword",
            ".pdf": "pdf",
            ".pptx": "powerpoint",
            ".txt": "plaintext",
            ".html": "html",
            ".htm": "html",
            ".xml": "xml",
            ".dita": "xml",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
        }
        self.xliff_exporter.version = version
        content = self.xliff_exporter.export(
            segments,
            filename,
            format_map.get(extension, "plaintext"),
        )
        return content, "application/xliff+xml", f"{Path(filename).stem or 'translated'}.xlf"

    def _build_text_translation_map(
        self,
        translation_maps: dict[str, dict[str, str]],
    ) -> dict[str, str]:
        return {
            **translation_maps["display_text"],
            **translation_maps["source_text"],
        }

    def _build_source_to_target_map(self, segments: list[dict[str, Any]]) -> dict[str, str]:
        result: dict[str, str] = {}
        for segment in segments:
            source_text = str(segment.get("source_text") or "").strip()
            target_text = str(segment.get("target_text") or "").strip()
            if source_text and target_text and source_text not in result:
                result[source_text] = target_text
        return result

    def _replace_plain_text(self, original_bytes: bytes, translations: dict[str, str]) -> bytes:
        content = self._decode_text_content(original_bytes)
        for source_text in sorted(translations.keys(), key=len, reverse=True):
            content = content.replace(source_text, translations[source_text])
        return content.encode("utf-8")

    def _export_json(
        self,
        original_bytes: bytes,
        text_map: dict[str, str],
        path_map: dict[str, str],
    ) -> bytes:
        payload = json.loads(self._decode_text_content(original_bytes))
        translated = self._translate_tree(payload, text_map, path_map)
        return json.dumps(translated, ensure_ascii=False, indent=2).encode("utf-8")

    def _export_yaml(
        self,
        original_bytes: bytes,
        text_map: dict[str, str],
        path_map: dict[str, str],
    ) -> bytes:
        payload = yaml.safe_load(self._decode_text_content(original_bytes))
        translated = self._translate_tree(payload, text_map, path_map)
        return yaml.safe_dump(
            translated,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ).encode("utf-8")

    def _translate_tree(
        self,
        payload: Any,
        text_map: dict[str, str],
        path_map: dict[str, str],
        path_parts: list[str] | None = None,
    ) -> Any:
        current_path_parts = path_parts or []
        current_path = "".join(current_path_parts) if current_path_parts and current_path_parts[0].startswith("[") else ".".join(current_path_parts)

        if isinstance(payload, str):
            if current_path and current_path in path_map:
                return path_map[current_path]
            return text_map.get(payload, payload)
        if isinstance(payload, list):
            result: list[Any] = []
            for index, item in enumerate(payload):
                next_part = f"[{index}]"
                next_path_parts = current_path_parts + [next_part]
                result.append(self._translate_tree(item, text_map, path_map, next_path_parts))
            return result
        if isinstance(payload, dict):
            result: dict[Any, Any] = {}
            for key, value in payload.items():
                if current_path_parts:
                    next_path_parts = current_path_parts + [str(key)]
                else:
                    next_path_parts = [str(key)]
                result[key] = self._translate_tree(value, text_map, path_map, next_path_parts)
            return result
        return payload

    def _decode_text_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "utf-16", "gb18030", "iso-8859-1", "cp1252"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
            except UnicodeError:
                continue
        return raw_bytes.decode("utf-8", errors="replace")

    def _build_translated_filename(self, filename: str, extension_override: str | None = None) -> str:
        path = Path(filename or "translated.txt")
        extension = extension_override or path.suffix or ".txt"
        return f"{path.stem or 'translated'}_translated{extension}"

    def _get_mime_type(self, extension: str) -> str:
        mime_map = {
            ".csv": "text/csv; charset=utf-8",
            ".dxf": "image/vnd.dxf",
            ".idml": "application/vnd.adobe.indesign-idml-package",
            ".json": "application/json; charset=utf-8",
            ".md": "text/markdown; charset=utf-8",
            ".markdown": "text/markdown; charset=utf-8",
            ".mif": "application/octet-stream",
            ".po": "text/x-gettext-translation; charset=utf-8",
            ".pot": "text/x-gettext-translation; charset=utf-8",
            ".properties": "text/plain; charset=utf-8",
            ".sdlxliff": "application/octet-stream",
            ".srt": "text/plain; charset=utf-8",
            ".strings": "text/plain; charset=utf-8",
            ".svg": "image/svg+xml",
            ".txml": "application/octet-stream",
            ".txt": "text/plain; charset=utf-8",
            ".yaml": "application/yaml; charset=utf-8",
            ".yml": "application/yaml; charset=utf-8",
            ".zip": "application/zip",
        }
        guessed = mimetypes.guess_type(f"file{extension}")[0]
        return mime_map.get(extension, guessed or "application/octet-stream")

    def _to_optional_int(self, value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


def get_export_options_for_file(filename: str) -> list[dict]:
    return MultiFormatExporter().get_available_exports(filename)


def export_file(
    export_type: str,
    segments: list[Any],
    filename: str,
    original_bytes: bytes | None = None,
    source_lang: str = "zh-CN",
    target_lang: str = "en-US",
) -> tuple[bytes, str, str]:
    exporter = MultiFormatExporter(source_lang, target_lang)
    return exporter.export(export_type, segments, filename, original_bytes)
