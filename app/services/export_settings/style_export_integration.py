"""
style_export_integration.py
把 export_settings 目录下的 direct_style_edit / style_adjuster 能力接入系统导出流程。

设计约束：
  - 不修改 export_settings 目录内原有的任何脚本（direct_style_edit.py / style_adjuster.py
    等），它们使用的是"顶层裸导入"（如 ``from style_adjuster import _q``），因此这里
    通过把 export_settings 目录临时加入 sys.path 后再导入，保持原脚本零改动。
  - 对外只暴露两个函数：
      build_style_adjuster(settings)      —— 由前端传来的设置字典构造 StyleAdjuster
      apply_export_style_settings(bytes, settings) —— 对导出的 docx 字节应用样式设置

前端传入的设置字典结构（所有字段均可选，缺省即不修改原文档对应项）：
    {
        "enabled": true,
        "defaults": { "font_ascii": "Times New Roman", "font_size": 10.5, ... },
        "styles": {
            "Normal":     { "font_east_asia": "宋体", "font_size": 10.5, ... },
            "heading 1":  { ... },
            "Table Grid": { "tbl_para_alignment": "center", ... }
        },
        "hyphenation": {
            "auto": true, "consecutive_limit": 2,
            "zone_pt": 18, "do_not_hyphenate_caps": true
        }
    }
"""
from __future__ import annotations

import inspect
import logging
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_EXPORT_SETTINGS_DIR = str(Path(__file__).resolve().parent)
_IMPORT_LOCK = threading.Lock()
_MODULES: dict[str, Any] | None = None

# ── 各字段的目标类型，用于把前端传来的字符串安全地转换成正确的 Python 类型 ──
_BOOL_KEYS = frozenset({
    "bold", "italic", "strike", "dstrike", "small_caps", "all_caps", "vanish",
    "keep_next", "keep_lines", "page_break_before", "tbl_run_bold",
    # hyphenation
    "auto", "do_not_hyphenate_caps",
})
_INT_KEYS = frozenset({
    "char_scale", "tbl_width_pct", "tbl_border_size",
    # hyphenation
    "consecutive_limit",
})
_FLOAT_KEYS = frozenset({
    "font_size", "font_size_cs", "char_spacing", "kern", "position",
    "line_spacing", "space_before", "space_after",
    "indent_left", "indent_right", "indent_first_line", "indent_hanging",
    "tbl_indent", "tbl_cell_margin_top", "tbl_cell_margin_bottom",
    "tbl_cell_margin_left", "tbl_cell_margin_right",
    "tbl_para_line_spacing", "tbl_para_space_before", "tbl_para_space_after",
    "tbl_run_font_size", "tbl_run_char_spacing",
    # hyphenation
    "zone_pt",
})
_HYPHENATION_KEYS = frozenset({"auto", "consecutive_limit", "zone_pt", "do_not_hyphenate_caps"})


def _load_modules() -> dict[str, Any]:
    """把 export_settings 目录加入 sys.path 后导入原脚本（只做一次）。"""
    global _MODULES
    if _MODULES is not None:
        return _MODULES
    with _IMPORT_LOCK:
        if _MODULES is not None:
            return _MODULES
        if _EXPORT_SETTINGS_DIR not in sys.path:
            sys.path.insert(0, _EXPORT_SETTINGS_DIR)
        import style_adjuster  # type: ignore  # noqa: E402
        import direct_style_edit  # type: ignore  # noqa: E402

        _MODULES = {
            "StyleAdjuster": style_adjuster.StyleAdjuster,
            "apply_style_in_place": direct_style_edit.apply_style_in_place,
            "valid_style_keys": frozenset(
                name
                for name in inspect.signature(style_adjuster.StyleRule.__init__).parameters
                if name != "self"
            ),
        }
    return _MODULES


def _coerce(key: str, value: Any) -> Any:
    """把单个字段值转换为 StyleRule 期望的类型；无法转换或为空时返回 None。"""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    if key in _BOOL_KEYS:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if key in _INT_KEYS:
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return None
    if key in _FLOAT_KEYS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    # 其余按字符串处理（字体名、颜色、对齐方式等）
    return str(value)


def _clean_kwargs(raw: Any, valid_keys: frozenset[str]) -> dict[str, Any]:
    """过滤出合法字段并做类型转换，剔除 None。"""
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in valid_keys:
            continue
        coerced = _coerce(key, value)
        if coerced is not None:
            cleaned[key] = coerced
    return cleaned


def style_settings_enabled(settings: Any) -> bool:
    """判断设置字典是否启用且包含至少一项有效配置。"""
    if not isinstance(settings, dict):
        return False
    if not settings.get("enabled", False):
        return False
    if settings.get("defaults"):
        return True
    styles = settings.get("styles")
    if isinstance(styles, dict) and any(isinstance(v, dict) and v for v in styles.values()):
        return True
    hyphenation = settings.get("hyphenation")
    if isinstance(hyphenation, dict) and any(v not in (None, "") for v in hyphenation.values()):
        return True
    return False


def build_style_adjuster(settings: dict[str, Any]) -> Any:
    """由前端设置字典构造 StyleAdjuster 实例。"""
    modules = _load_modules()
    adjuster = modules["StyleAdjuster"]()
    valid_keys = modules["valid_style_keys"]

    defaults = _clean_kwargs(settings.get("defaults"), valid_keys)
    if defaults:
        adjuster.set_defaults(**defaults)

    styles = settings.get("styles")
    if isinstance(styles, dict):
        for style_name, raw in styles.items():
            kwargs = _clean_kwargs(raw, valid_keys)
            if kwargs:
                adjuster.set_style(str(style_name), **kwargs)

    hyphenation = settings.get("hyphenation")
    if isinstance(hyphenation, dict):
        hyph_kwargs = _clean_kwargs(hyphenation, _HYPHENATION_KEYS)
        if hyph_kwargs:
            adjuster.set_hyphenation(**hyph_kwargs)

    return adjuster


def apply_export_style_settings(docx_bytes: bytes, settings: dict[str, Any]) -> bytes:
    """
    对导出的 docx 字节应用样式设置，返回调整后的字节。
    若设置未启用、无有效配置或处理失败，则原样返回输入字节，保证导出流程不被破坏。
    """
    if not style_settings_enabled(settings):
        return docx_bytes

    try:
        modules = _load_modules()
        adjuster = build_style_adjuster(settings)
    except Exception:  # noqa: BLE001
        logger.exception("构造导出样式调整器失败，跳过样式调整。")
        return docx_bytes

    tmp_dir = Path(tempfile.mkdtemp(prefix="export_style_"))
    input_path = tmp_dir / "input.docx"
    output_path = tmp_dir / "output.docx"
    try:
        input_path.write_bytes(docx_bytes)
        modules["apply_style_in_place"](input_path, output_path, adjuster)
        if output_path.is_file():
            return output_path.read_bytes()
        return docx_bytes
    except Exception:  # noqa: BLE001
        logger.exception("应用导出样式设置失败，返回未调整的原始导出内容。")
        return docx_bytes
    finally:
        for path in (input_path, output_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
