"""
pptx_layout —— PPTX 版式优化（导出后处理）子包。

功能：对导出的 PPTX 字节做"文本溢出修复"——因翻译导致文字变长溢出文本框时，
借助多模态视觉模型（复用系统 LLM provider）判断溢出并回写框尺寸/字号缩放。

设计约束：
  - 复用系统能力：LibreOffice(渲染截图) + llm_service(视觉复核)，不引入独立 openai 客户端。
  - 全链路优雅降级：渲染/视觉模型不可用时回退到启发式估算；任何异常都返回原始字节，
    绝不中断导出、绝不抛 HTTP 异常。

对外仅暴露两个函数：
  - pptx_layout_settings_enabled(settings) -> bool
  - apply_pptx_layout_settings(pptx_bytes, settings) -> bytes
"""
from __future__ import annotations

from app.services.export_settings.pptx_layout.pptx_layout_integration import (
    apply_pptx_layout_settings,
    pptx_layout_settings_enabled,
)

__all__ = ["apply_pptx_layout_settings", "pptx_layout_settings_enabled"]
