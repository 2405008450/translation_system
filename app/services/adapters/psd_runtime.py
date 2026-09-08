from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.services.adapters.psd_ocr import (
    PsdOcrError,
    get_psd_ocr_health,
    recognize_psd_candidates,
)


_WINDOWS_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_DEFAULT_TIMEOUT_SECONDS = 120.0


class PsdRuntimeError(RuntimeError):
    """本地 PSD Node 运行时调用失败。"""


def parse_psd_document(
    raw_bytes: bytes,
    *,
    source_language: str | None = None,
) -> dict[str, Any]:
    """读取 PSD 文档结构，并使用本地 PaddleOCR 识别安全像素层候选。"""
    with tempfile.TemporaryDirectory(prefix="translation-psd-parse-") as temp_dir:
        working_dir = Path(temp_dir)
        input_path = working_dir / "source.psd"
        candidate_directory = working_dir / "ocr-candidates"
        input_path.write_bytes(raw_bytes)
        document = _run_bridge("parse", input_path, candidate_directory)
        raw_candidates = document.pop("image_text_candidates", [])
        candidates = [item for item in raw_candidates if isinstance(item, dict)]
        try:
            image_text_layers = recognize_psd_candidates(
                candidate_directory,
                candidates,
                source_language=source_language,
            )
        except PsdOcrError as exc:
            raise PsdRuntimeError(str(exc)) from exc
        document["image_text_layers"] = image_text_layers
        document["image_text_layer_count"] = len(image_text_layers)
        return document


def export_psd_document(
    raw_bytes: bytes,
    translations: list[dict[str, Any]],
) -> tuple[bytes, dict[str, Any]]:
    """将译文写回 PSD 文本图层，并返回已校验的原格式内容。"""
    with tempfile.TemporaryDirectory(prefix="translation-psd-export-") as temp_dir:
        working_dir = Path(temp_dir)
        input_path = working_dir / "source.psd"
        translations_path = working_dir / "translations.json"
        output_path = working_dir / "translated.psd"
        input_path.write_bytes(raw_bytes)
        translations_path.write_text(
            json.dumps({"translations": translations}, ensure_ascii=False),
            encoding="utf-8",
        )
        result = _run_bridge("export", input_path, translations_path, output_path)
        if not output_path.is_file():
            raise PsdRuntimeError("PSD 运行时未生成输出文件")
        return output_path.read_bytes(), result


def get_psd_runtime_health() -> dict[str, Any]:
    """返回 PSD 与 PaddleOCR 运行时版本信息，用于部署诊断。"""
    result = _run_bridge("health")
    result["ocr"] = get_psd_ocr_health()
    return result


def _run_bridge(command: str, *paths: Path) -> dict[str, Any]:
    node_path = _find_node_executable()
    bridge_path = _find_bridge_path()
    process_command = [
        str(node_path),
        f"--max-old-space-size={_get_node_max_old_space_mb()}",
        str(bridge_path),
        command,
        *(str(path) for path in paths),
    ]

    try:
        result = subprocess.run(
            process_command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_get_timeout_seconds(),
            check=False,
            creationflags=_WINDOWS_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise PsdRuntimeError(f"PSD 处理超时（{_get_timeout_seconds():g} 秒）") from exc
    except OSError as exc:
        raise PsdRuntimeError(f"无法启动 PSD 运行时：{exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"退出码 {result.returncode}").strip()
        raise PsdRuntimeError(detail)

    payload = _parse_last_json_line(result.stdout)
    if not isinstance(payload, dict):
        raise PsdRuntimeError("PSD 运行时返回了无效结果")
    return payload


def _find_node_executable() -> Path:
    configured = os.getenv("PSD_NODE_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return path
        raise PsdRuntimeError(f"PSD_NODE_PATH 指向的文件不存在：{path}")

    discovered = shutil.which("node")
    if discovered:
        return Path(discovered)
    raise PsdRuntimeError("未找到 Node.js，无法解析 PSD；请安装 Node.js 20 或配置 PSD_NODE_PATH")


def _find_bridge_path() -> Path:
    configured = os.getenv("PSD_BRIDGE_PATH", "").strip()
    path = (
        Path(configured).expanduser()
        if configured
        else Path(__file__).resolve().parents[3] / "psd_runtime" / "bridge.cjs"
    )
    if not path.is_file():
        raise PsdRuntimeError(f"PSD bridge 文件不存在：{path}")
    return path


def _get_timeout_seconds() -> float:
    configured = os.getenv("PSD_PROCESS_TIMEOUT_SECONDS", "").strip()
    if not configured:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        return max(float(configured), 1.0)
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS


def _get_node_max_old_space_mb() -> int:
    configured = os.getenv("PSD_NODE_MAX_OLD_SPACE_MB", "").strip()
    if not configured:
        return 512
    try:
        return min(max(int(configured), 128), 2048)
    except ValueError:
        return 512


def _parse_last_json_line(output: str) -> Any:
    for line in reversed((output or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None
