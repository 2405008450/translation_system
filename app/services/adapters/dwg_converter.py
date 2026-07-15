"""
DWG <-> DXF 转换桥接

通过 ODA File Converter（Open Design Alliance，免费）调用，跨 Windows / Linux 可用。
用户需自行安装并在 settings.oda_converter_path 中给出可执行文件绝对路径。

ODA File Converter 的 CLI 接口（无 Python 包装）：
    ODAFileConverter <inputDir> <outputDir> <outputVer> <outputFmt> <recurse> <audit> [<filter>]
- outputVer 例：ACAD2018 / ACAD2013 / ACAD2010 / ACAD2007 / ACAD2004
- outputFmt：DWG | DXF | DXB
- recurse / audit：0 或 1
- filter：可选通配符，如 "*.dwg"
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from app.config import get_settings


logger = logging.getLogger(__name__)


def _silent_subprocess_kwargs() -> dict:
    """构造让 ODA 在 Windows 上不弹窗的 subprocess 参数。"""
    if sys.platform != "win32":
        return {}
    # CREATE_NO_WINDOW 屏蔽控制台，STARTUPINFO + SW_HIDE 隐藏 Qt 主窗口
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE
    return {"creationflags": creationflags, "startupinfo": startupinfo}


class DwgConverterError(RuntimeError):
    """DWG/DXF 转换失败。"""


class DwgConverterUnavailable(DwgConverterError):
    """未配置或找不到 ODA File Converter。"""


def _resolve_executable() -> str:
    settings = get_settings()
    candidate = (settings.oda_converter_path or os.environ.get("ODA_CONVERTER_PATH") or "").strip()
    if not candidate:
        raise DwgConverterUnavailable(
            "未配置 ODA File Converter，请安装后将路径写入 settings.oda_converter_path 或环境变量 ODA_CONVERTER_PATH。"
        )
    path = Path(candidate)
    if not path.is_file():
        # 也允许传裸名（已加入 PATH）
        resolved = shutil.which(candidate)
        if not resolved:
            raise DwgConverterUnavailable(f"未找到 ODA File Converter 可执行文件：{candidate}")
        return resolved
    return str(path)


def _run_converter(
    input_dir: Path,
    output_dir: Path,
    *,
    target_format: Literal["DWG", "DXF"],
    target_version: str,
    file_filter: str,
) -> None:
    settings = get_settings()
    executable = _resolve_executable()
    cmd = [
        executable,
        str(input_dir),
        str(output_dir),
        target_version,
        target_format,
        "0",  # recurse
        "1",  # audit
        file_filter,
    ]
    logger.debug("Invoking ODA File Converter: %s", cmd)
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            timeout=settings.oda_converter_timeout_seconds,
            **_silent_subprocess_kwargs(),
        )
    except FileNotFoundError as exc:
        raise DwgConverterUnavailable(f"ODA File Converter 不可用：{exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DwgConverterError(
            f"ODA File Converter 执行超时（>{settings.oda_converter_timeout_seconds}s）。"
        ) from exc

    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        stdout = (result.stdout or b"").decode("utf-8", errors="replace").strip()
        raise DwgConverterError(
            f"ODA File Converter 返回非零状态 {result.returncode}：{stderr or stdout}"
        )


def dwg_to_dxf(dwg_bytes: bytes, *, target_version: str | None = None) -> bytes:
    """DWG 字节流转 DXF 字节流。"""
    if not dwg_bytes:
        raise DwgConverterError("DWG 内容为空。")

    settings = get_settings()
    version = target_version or settings.oda_converter_dxf_version

    with tempfile.TemporaryDirectory(prefix="dwg2dxf_") as tmp:
        in_dir = Path(tmp) / "in"
        out_dir = Path(tmp) / "out"
        in_dir.mkdir()
        out_dir.mkdir()

        stem = f"src_{uuid.uuid4().hex}"
        in_path = in_dir / f"{stem}.dwg"
        in_path.write_bytes(dwg_bytes)

        _run_converter(
            in_dir,
            out_dir,
            target_format="DXF",
            target_version=version,
            file_filter="*.dwg",
        )

        out_path = out_dir / f"{stem}.dxf"
        if not out_path.is_file():
            # ODA 有时会在文件名后追加版本号或保留原扩展，做一次兜底扫描
            for candidate in out_dir.glob(f"{stem}*"):
                if candidate.suffix.lower() == ".dxf":
                    return candidate.read_bytes()
            raise DwgConverterError("ODA File Converter 未生成 DXF 输出。")
        return out_path.read_bytes()


def dxf_to_dwg(dxf_bytes: bytes, *, target_version: str | None = None) -> bytes:
    """DXF 字节流转 DWG 字节流。"""
    if not dxf_bytes:
        raise DwgConverterError("DXF 内容为空。")

    settings = get_settings()
    version = target_version or settings.oda_converter_dxf_version

    with tempfile.TemporaryDirectory(prefix="dxf2dwg_") as tmp:
        in_dir = Path(tmp) / "in"
        out_dir = Path(tmp) / "out"
        in_dir.mkdir()
        out_dir.mkdir()

        stem = f"src_{uuid.uuid4().hex}"
        in_path = in_dir / f"{stem}.dxf"
        in_path.write_bytes(dxf_bytes)

        _run_converter(
            in_dir,
            out_dir,
            target_format="DWG",
            target_version=version,
            file_filter="*.dxf",
        )

        out_path = out_dir / f"{stem}.dwg"
        if not out_path.is_file():
            for candidate in out_dir.glob(f"{stem}*"):
                if candidate.suffix.lower() == ".dwg":
                    return candidate.read_bytes()
            raise DwgConverterError("ODA File Converter 未生成 DWG 输出。")
        return out_path.read_bytes()


def is_available() -> bool:
    """探测 ODA File Converter 是否可用，供前端能力探测使用。"""
    try:
        _resolve_executable()
        return True
    except DwgConverterUnavailable:
        return False
