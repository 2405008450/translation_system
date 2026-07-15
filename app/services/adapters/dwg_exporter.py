"""
DWG 导出器 - 复用 DxfExporter 完成文本替换，再可选用 ODA 转回 DWG。

默认行为（settings.dwg_export_to_dwg = False）：
    输入 DWG -> 转 DXF -> 译文回写 DXF -> 直接返回 DXF 字节
启用 dwg_export_to_dwg：
    输入 DWG -> 转 DXF -> 译文回写 DXF -> 再用 ODA 转回 DWG

空间合并导出（Spatial Merge Export）：
    当传入 merged_text_info 时，启用 MTEXT 重建模式：
    - 在主实体位置创建新的 MTEXT 实体承载完整译文
    - 清空所有被合并的原始 TEXT 实体，避免文本重叠
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

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
        merged_text_info: List[Dict] | None = None,
    ) -> bytes:
        """与其他 Exporter 接口对齐，仅返回字节流。

        实际产出格式由 settings.dwg_export_to_dwg 决定。需同时拿到扩展名时请用
        export_with_extension。
        
        Args:
            original_bytes: 原始 DWG 字节
            translations: 源文本 -> 目标文本的映射
            prefer_dwg: 是否优先输出 DWG 格式
            merged_text_info: 空间合并文本信息（用于 MTEXT 重建）
        """
        return self.export_with_extension(
            original_bytes,
            translations,
            prefer_dwg=prefer_dwg,
            merged_text_info=merged_text_info,
        ).content

    def export_with_extension(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        *,
        prefer_dwg: Optional[bool] = None,
        merged_text_info: List[Dict] | None = None,
    ) -> DwgExportResult:
        """导出翻译后的 DWG/DXF，同时返回扩展名。
        
        Args:
            original_bytes: 原始 DWG 字节
            translations: 源文本 -> 目标文本的映射
            prefer_dwg: 是否优先输出 DWG 格式
            merged_text_info: 空间合并文本信息（用于 MTEXT 重建），每项包含：
                - source_text: 原始合并后的源文本
                - target_text: 翻译后的目标文本
                - primary_handle: 主实体 handle
                - merged_handles: 所有被合并的实体 handle 列表
                - primary_x, primary_y, primary_height: 主实体位置和字高
                - layer: 图层名
        """
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

        # 判断是否有合并文本需要处理
        has_merged_groups = bool(
            merged_text_info and 
            any(len(info.get("merged_handles", [])) > 1 for info in merged_text_info)
        )

        dxf_options = DxfExportOptions(
            enable_overflow_shrink=settings.dwg_enable_overflow_shrink,
            min_width_factor=settings.dwg_min_width_factor,
            min_char_height_ratio=settings.dwg_min_char_height_ratio,
            handle_extra_entities=settings.dwg_handle_extra_entities,
            fix_shx_font_for_unicode=settings.dwg_fix_shx_font_for_unicode,
            unicode_font_name=settings.dwg_unicode_font_name,
            enable_spatial_merge_export=has_merged_groups,
        )
        translated_dxf = self._dxf_exporter.export(
            dxf_bytes,
            translations,
            options=dxf_options,
            merged_text_info=merged_text_info,
        )

        if not want_dwg:
            return DwgExportResult(content=translated_dxf, extension=".dxf", fallback_used=False)

        try:
            dwg_bytes = dxf_to_dwg(translated_dxf)
            return DwgExportResult(content=dwg_bytes, extension=".dwg", fallback_used=False)
        except (DwgConverterUnavailable, DwgConverterError) as exc:
            logger.warning("DXF 回写 DWG 失败，降级返回 DXF：%s", exc)
            return DwgExportResult(content=translated_dxf, extension=".dxf", fallback_used=True)
