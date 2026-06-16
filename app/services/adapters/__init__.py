from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import (
    AdapterError,
    ExportError,
    FileTooLargeError,
    OCRRequiredError,
    ParseError,
    UnsupportedFormatError,
)
from app.services.adapters.export_service import ExportService
from app.services.adapters.export_formats import (
    EXPORT_OPTIONS,
    FORMAT_EXPORT_SUPPORT,
    get_export_option,
    get_supported_exports,
)
from app.services.adapters.models import (
    BlockNode,
    DocumentAST,
    NodeType,
    ParseResult,
    Segment,
    TMEntry,
    TMImportResult,
)
from app.services.adapters.multi_format_exporter import (
    MultiFormatExporter,
    export_file,
    get_export_options_for_file,
)
from app.services.adapters.registry import AdapterRegistry, get_registry, register_adapter
from app.services.adapters.segment_extractor import SegmentExtractor, extract_segments


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _AdapterSpec:
    module_path: str
    class_name: str


_DEFAULT_ADAPTER_SPECS = (
    _AdapterSpec("app.services.adapters.txt_adapter", "TxtAdapter"),
    _AdapterSpec("app.services.adapters.docx_adapter", "DocxAdapter"),
    _AdapterSpec("app.services.adapters.xlsx_adapter", "XlsxAdapter"),
    _AdapterSpec("app.services.adapters.pdf_adapter", "PdfAdapter"),
    _AdapterSpec("app.services.adapters.pptx_adapter", "PptxAdapter"),
    _AdapterSpec("app.services.adapters.dita_adapter", "DitaAdapter"),
    _AdapterSpec("app.services.adapters.svg_adapter", "SvgAdapter"),
    _AdapterSpec("app.services.adapters.yaml_adapter", "YamlAdapter"),
    _AdapterSpec("app.services.adapters.json_adapter", "JsonAdapter"),
    _AdapterSpec("app.services.adapters.php_adapter", "PhpAdapter"),
    _AdapterSpec("app.services.adapters.html_adapter", "HtmlAdapter"),
    _AdapterSpec("app.services.adapters.properties_adapter", "PropertiesAdapter"),
    _AdapterSpec("app.services.adapters.po_adapter", "PoAdapter"),
    _AdapterSpec("app.services.adapters.strings_adapter", "StringsAdapter"),
    _AdapterSpec("app.services.adapters.markdown_adapter", "MarkdownAdapter"),
    _AdapterSpec("app.services.adapters.srt_adapter", "SrtAdapter"),
    _AdapterSpec("app.services.adapters.csv_adapter", "CsvAdapter"),
    _AdapterSpec("app.services.adapters.sdlxliff_adapter", "SdlxliffAdapter"),
    _AdapterSpec("app.services.adapters.txml_adapter", "TxmlAdapter"),
    _AdapterSpec("app.services.adapters.dxf_adapter", "DxfAdapter"),
    _AdapterSpec("app.services.adapters.dwg_adapter", "DwgAdapter"),
    _AdapterSpec("app.services.adapters.zip_adapter", "ZipAdapter"),
    _AdapterSpec("app.services.adapters.idml_adapter", "IdmlAdapter"),
    _AdapterSpec("app.services.adapters.mif_adapter", "MifAdapter"),
    _AdapterSpec("app.services.adapters.rar_adapter", "RarAdapter"),
)

_initialized = False


def _load_adapter_class(spec: _AdapterSpec) -> type[FormatAdapter] | None:
    try:
        module = importlib.import_module(spec.module_path)
    except ModuleNotFoundError as exc:
        logger.warning(
            "Skipping adapter %s because dependency %s is unavailable.",
            spec.class_name,
            exc.name,
        )
        return None
    except Exception:
        logger.exception("Failed to import adapter module %s.", spec.module_path)
        return None

    adapter_class = getattr(module, spec.class_name, None)
    if adapter_class is None:
        logger.warning(
            "Skipping adapter %s because it is not defined in %s.",
            spec.class_name,
            spec.module_path,
        )
        return None

    return adapter_class


def _register_default_adapters() -> None:
    global _initialized
    if _initialized:
        return

    registry = get_registry()
    for spec in _DEFAULT_ADAPTER_SPECS:
        adapter_class = _load_adapter_class(spec)
        if adapter_class is None:
            continue

        try:
            registry.register(adapter_class())
        except Exception:
            logger.exception("Failed to register adapter %s.", spec.class_name)

    _initialized = True


def ensure_default_adapters_registered() -> AdapterRegistry:
    _register_default_adapters()
    return get_registry()


ensure_default_adapters_registered()


__all__ = [
    "AdapterError",
    "AdapterRegistry",
    "BlockNode",
    "DocumentAST",
    "EXPORT_OPTIONS",
    "ExportService",
    "ExportError",
    "FORMAT_EXPORT_SUPPORT",
    "FileTooLargeError",
    "FormatAdapter",
    "MultiFormatExporter",
    "NodeType",
    "OCRRequiredError",
    "ParseError",
    "ParseResult",
    "Segment",
    "SegmentExtractor",
    "TMEntry",
    "TMImportResult",
    "UnsupportedFormatError",
    "ensure_default_adapters_registered",
    "export_file",
    "extract_segments",
    "get_export_option",
    "get_export_options_for_file",
    "get_registry",
    "get_supported_exports",
    "register_adapter",
]
