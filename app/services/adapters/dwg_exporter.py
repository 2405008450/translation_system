"""
DWG 导出器 - 复用 DxfExporter 完成文本替换，再可选用 ODA 转回 DWG。

默认行为（settings.dwg_export_to_dwg = False）：
    输入 DWG -> 转 DXF -> 译文回写 DXF -> 直接返回 DXF 字节
启用 dwg_export_to_dwg：
    输入 DWG -> 转 DXF -> 译文回写 DXF -> 再用 ODA 转回 DWG
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

from app.config import get_settings
from app.services.adapters.dwg_converter import (
    DwgConverterError,
    DwgConverterUnavailable,
    dwg_to_dxf,
    dxf_to_dwg,
)
from app.services.adapters.dxf_exporter import DxfExporter, DxfExportOptions


logger = logging.getLogger(__name__)


@dataclass
class DwgExportResult:
    content: bytes
    extension: str  # ".dwg" 或 ".dxf"
    fallback_used: bool  # 是否因故 downgrade 到 DXF


class DwgExporter:
    """DWG 导出器"""

    def __init__(self) -> None:
        self._dxf_exporter = DxfExporter()

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        *,
        prefer_dwg: Optional[bool] = None,
    ) -> bytes:
        """与其他 Exporter 接口对齐，仅返回字节流。

        实际产出格式由 settings.dwg_export_to_dwg 决定。需同时拿到扩展名时请用
        export_with_extension。
        """
        return self.export_with_extension(
            original_bytes,
            translations,
            prefer_dwg=prefer_dwg,
        ).content

    def export_with_extension(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        *,
        prefer_dwg: Optional[bool] = None,
    ) -> DwgExportResult:
        if not original_bytes:
            return DwgExportResult(content=original_bytes, extension=".dwg", fallback_used=False)

        settings = get_settings()
        want_dwg = settings.dwg_export_to_dwg if prefer_dwg is None else bool(prefer_dwg)

        try:
            dxf_bytes = dwg_to_dxf(original_bytes)
        except DwgConverterUnavailable as exc:
            raise RuntimeError(f"DWG 导出需要 ODA File Converter：{exc}") from exc
        except DwgConverterError as exc:
            raise RuntimeError(f"DWG 转 DXF 失败：{exc}") from exc

        dxf_options = DxfExportOptions(
            enable_overflow_shrink=settings.dwg_enable_overflow_shrink,
            min_width_factor=settings.dwg_min_width_factor,
            min_char_height_ratio=settings.dwg_min_char_height_ratio,
            handle_extra_entities=settings.dwg_handle_extra_entities,
            fix_shx_font_for_unicode=settings.dwg_fix_shx_font_for_unicode,
            unicode_font_name=settings.dwg_unicode_font_name,
        )
        translated_dxf = self._dxf_exporter.export(
            dxf_bytes,
            translations,
            options=dxf_options,
        )

        if not want_dwg:
            return DwgExportResult(content=translated_dxf, extension=".dxf", fallback_used=False)

        try:
            dwg_bytes = dxf_to_dwg(translated_dxf)
            return DwgExportResult(content=dwg_bytes, extension=".dwg", fallback_used=False)
        except (DwgConverterUnavailable, DwgConverterError) as exc:
            logger.warning("DXF 回写 DWG 失败，降级返回 DXF：%s", exc)
            return DwgExportResult(content=translated_dxf, extension=".dxf", fallback_used=True)
