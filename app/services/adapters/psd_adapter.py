from __future__ import annotations

import re
from typing import Any, List

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import BlockNode, DocumentAST, NodeType, ParseResult, Segment
from app.services.adapters.psd_runtime import PsdRuntimeError, parse_psd_document


class PsdAdapter(FormatAdapter):
    """PSD 可编辑文本图层适配器。"""

    def supported_extensions(self) -> List[str]:
        return [".psd"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        return self._parse(raw_bytes, filename="<unknown>", options={})

    def parse_with_options(
        self,
        raw_bytes: bytes,
        filename: str = "<unknown>",
        options: dict | None = None,
    ) -> ParseResult:
        self.validate_file_size(raw_bytes, filename)
        return self._parse(raw_bytes, filename=filename, options=options or {})

    def _parse(
        self,
        raw_bytes: bytes,
        *,
        filename: str,
        options: dict[str, Any],
    ) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".psd"),
                segments=[],
                metadata={"text_layer_count": 0},
            )

        try:
            document = parse_psd_document(
                raw_bytes,
                source_language=str(options.get("source_language") or ""),
            )
        except PsdRuntimeError as exc:
            raise ParseError(filename=filename, reason=str(exc)) from exc

        include_hidden = bool(options.get("psd_translate_hidden_layers", False))
        include_locked = bool(options.get("psd_translate_locked_layers", False))
        nodes: list[BlockNode] = []
        segments: list[Segment] = []
        skipped_hidden = 0
        skipped_locked = 0
        skipped_vertical = 0

        translated_text_layers = 0
        translated_image_text_layers = 0
        layer_groups = (
            ("PSD_TEXT", document.get("text_layers", [])),
            ("PSD_IMAGE_TEXT", document.get("image_text_layers", [])),
        )
        for default_entity_type, layers in layer_groups:
            for layer in layers:
                if not isinstance(layer, dict):
                    continue
                display_text = str(layer.get("text") or "")
                source_text = _normalize_source_text(display_text)
                if not source_text:
                    continue
                if layer.get("hidden") and not include_hidden:
                    skipped_hidden += 1
                    continue
                if layer.get("locked") and not include_locked:
                    skipped_locked += 1
                    continue

                entity_type = str(layer.get("entity_type") or default_entity_type)
                if (
                    entity_type == "PSD_TEXT"
                    and str(layer.get("orientation") or "horizontal") == "vertical"
                ):
                    skipped_vertical += 1
                    continue

                metadata = {
                    "entity_type": entity_type,
                    "layer_path": str(layer.get("layer_path") or ""),
                    "layer_id": layer.get("layer_id"),
                    "layer_name": str(layer.get("layer_name") or ""),
                    "layer_names": list(layer.get("layer_names") or []),
                    "hidden": bool(layer.get("hidden")),
                    "locked": bool(layer.get("locked")),
                    "bounds": dict(layer.get("bounds") or {}),
                    "text_bounds": layer.get("text_bounds"),
                    "orientation": str(layer.get("orientation") or "horizontal"),
                    "shape_type": str(layer.get("shape_type") or "point"),
                    "transform": layer.get("transform"),
                    "font_name": str(layer.get("font_name") or ""),
                    "font_size": layer.get("font_size"),
                    "fill_color": layer.get("fill_color"),
                    "justification": str(layer.get("justification") or ""),
                    "has_mixed_styles": bool(layer.get("has_mixed_styles")),
                    "ocr_bounds": layer.get("ocr_bounds"),
                    "ocr_confidence": layer.get("ocr_confidence"),
                    "ocr_engine": str(layer.get("ocr_engine") or ""),
                    "ocr_model": str(layer.get("ocr_model") or ""),
                    "ocr_source_language": str(layer.get("ocr_source_language") or ""),
                    "background_color": layer.get("background_color"),
                    "foreground_color": layer.get("foreground_color"),
                }
                position = len(segments)
                block_path = str(position)
                nodes.append(
                    BlockNode(
                        node_type=NodeType.PARAGRAPH,
                        text_content=display_text,
                        metadata=metadata,
                    )
                )
                segments.append(
                    Segment(
                        segment_id=f"seg-{position + 1:06d}",
                        source_text=source_text,
                        display_text=display_text,
                        block_path=block_path,
                        position=position,
                        metadata=metadata,
                    )
                )
                if entity_type == "PSD_IMAGE_TEXT":
                    translated_image_text_layers += 1
                else:
                    translated_text_layers += 1

        document_metadata = {
            "width": int(document.get("width") or 0),
            "height": int(document.get("height") or 0),
            "bits_per_channel": int(document.get("bits_per_channel") or 8),
            "color_mode": int(document.get("color_mode") or 3),
            "source_color_mode": int(
                document.get("source_color_mode") or document.get("color_mode") or 3
            ),
            "color_mode_converted": bool(document.get("color_mode_converted", False)),
            "text_layer_count": int(document.get("text_layer_count") or 0),
            "image_text_layer_count": int(document.get("image_text_layer_count") or 0),
            "translated_text_layer_count": translated_text_layers,
            "translated_image_text_layer_count": translated_image_text_layers,
            "skipped_hidden_text_layers": skipped_hidden,
            "skipped_locked_text_layers": skipped_locked,
            "skipped_vertical_text_layers": skipped_vertical,
        }
        ast = DocumentAST(nodes=nodes, source_format=".psd", metadata=document_metadata)
        return ParseResult(ast=ast, segments=segments, metadata=document_metadata)


def _normalize_source_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
