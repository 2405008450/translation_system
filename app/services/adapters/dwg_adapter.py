"""
DWG 适配器 - 通过 ODA File Converter 将 DWG 转为临时 DXF 后复用 DxfAdapter。
"""
from __future__ import annotations

import logging
from typing import List

from app.config import get_settings
from app.services.adapters.base import FormatAdapter
from app.services.adapters.dwg_converter import (
    DwgConverterError,
    DwgConverterUnavailable,
    dwg_to_dxf,
)
from app.services.adapters.dxf_adapter import DxfAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import ParseResult


logger = logging.getLogger(__name__)


class DwgAdapter(FormatAdapter):
    """DWG 文件适配器（通过 ODA + ezdxf 处理）"""

    def __init__(self, max_file_size: int | None = None) -> None:
        super().__init__(max_file_size=max_file_size)
        self._dxf_adapter = DxfAdapter(max_file_size=max_file_size)

    def supported_extensions(self) -> List[str]:
        return [".dwg"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        return self._parse(raw_bytes, skip_non_translatable=False, filename="<unknown>")

    def parse_with_options(
        self,
        raw_bytes: bytes,
        filename: str = "<unknown>",
        options: dict | None = None,
    ) -> ParseResult:
        self.validate_file_size(raw_bytes, filename)
        return self._parse(
            raw_bytes,
            skip_non_translatable=bool((options or {}).get("skip_non_translatable", True)),
            filename=filename,
        )

    def _parse(self, raw_bytes: bytes, *, skip_non_translatable: bool, filename: str) -> ParseResult:
        settings = get_settings()
        if not raw_bytes:
            return self._dxf_adapter._parse_with_options(
                raw_bytes,
                skip_non_translatable=skip_non_translatable,
                filename=filename,
                extract_extra_entities=settings.dwg_handle_extra_entities,
                skip_dimension_like=settings.dwg_skip_dimension_like,
                enable_spatial_merge=settings.dwg_enable_spatial_merge,
            )
        try:
            dxf_bytes = dwg_to_dxf(raw_bytes)
        except DwgConverterUnavailable as exc:
            raise ParseError(
                filename=filename,
                reason=f"DWG 解析需要 ODA File Converter：{exc}",
            ) from exc
        except DwgConverterError as exc:
            raise ParseError(filename=filename, reason=f"DWG 转 DXF 失败：{exc}") from exc

        result = self._dxf_adapter._parse_with_options(
            dxf_bytes,
            skip_non_translatable=skip_non_translatable,
            filename=filename,
            extract_extra_entities=settings.dwg_handle_extra_entities,
            skip_dimension_like=settings.dwg_skip_dimension_like,
            enable_spatial_merge=settings.dwg_enable_spatial_merge,
        )
        result.ast.source_format = ".dwg"
        result.metadata = {**result.metadata, "converted_from": ".dwg"}
        return result
