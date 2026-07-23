"""
pptx_layout_integration.py —— 把 PPTX 版式优化接入系统导出流程。

对标 docx 的 style_export_integration：对外只暴露
  - pptx_layout_settings_enabled(settings)
  - apply_pptx_layout_settings(pptx_bytes, settings)

前端传入的设置结构（在导出样式设置字典里新增 pptx 子对象，均可选）：
    {
        ...docx 设置...,
        "pptx": {
            "enabled": true,           # 是否启用 PPTX 版式优化（默认关闭）
            "mode": "model_scale",     # model_scale / shrink / both / expand
            "model": "",               # 可选：视觉模型 id（复用系统 LLM provider）
        }
    }
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _pptx_settings(settings: Any) -> dict[str, Any] | None:
    if not isinstance(settings, dict):
        return None
    pptx = settings.get("pptx")
    return pptx if isinstance(pptx, dict) else None


def pptx_layout_settings_enabled(settings: Any) -> bool:
    """判断是否启用 PPTX 版式优化。"""
    pptx = _pptx_settings(settings)
    return bool(pptx and pptx.get("enabled"))


def apply_pptx_layout_settings(
    pptx_bytes: bytes,
    settings: Any,
    *,
    report_context: dict | None = None,
) -> bytes:
    """对导出的 PPTX 字节应用版式优化，返回调整后的字节。

    未启用、无有效配置或处理失败时原样返回输入字节，保证导出流程不被破坏。
    若提供 report_context，则把本次 AI 判断与调整结果落库（失败不影响导出）。
    """
    pptx = _pptx_settings(settings)
    if not pptx or not pptx.get("enabled"):
        return pptx_bytes

    mode = str(pptx.get("mode") or "").strip() or None
    model = str(pptx.get("model") or "").strip() or None

    try:
        # 延迟导入：避免包加载期引入 pptx/pdf2image 等硬依赖。
        from app.config import get_settings
        from app.services.export_settings.pptx_layout.pipeline import (
            DEFAULT_MODE,
            optimize_pptx_layout,
        )

        resolved_model = model or (getattr(get_settings(), "pptx_layout_vlm_model", "") or None)
        return optimize_pptx_layout(
            pptx_bytes,
            mode=mode or DEFAULT_MODE,
            model=resolved_model,
            report_context=report_context,
        )
    except Exception:  # noqa: BLE001
        logger.exception("应用 PPTX 版式优化失败，返回未调整的原始导出内容。")
        return pptx_bytes
