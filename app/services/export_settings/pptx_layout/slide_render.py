"""
slide_render.py —— 把指定页幻灯片渲染为图片（供视觉模型读取）。

改编自参考实现 app/文件/model.py 的渲染部分：
  - 服务端仅保留 LibreOffice 路径（去掉 Windows COM 分支）。
  - soffice 可执行文件复用 libreoffice_service 的发现逻辑，不自造常量。
  - pdf -> 图片使用 pdf2image(依赖系统 poppler)。
全部失败路径返回 False，不抛异常，交由上层走启发式降级。
"""
from __future__ import annotations

import logging
import os
import platform
import subprocess
from pathlib import Path

from app.config import get_settings
from app.services.libreoffice_service import find_libreoffice_soffice

logger = logging.getLogger(__name__)

_DEFAULT_RENDER_DPI = 200
_CONVERT_TIMEOUT_SECONDS = 180


def _resolve_render_dpi() -> int:
    dpi = getattr(get_settings(), "pptx_layout_render_dpi", _DEFAULT_RENDER_DPI)
    try:
        value = int(dpi)
    except (TypeError, ValueError):
        return _DEFAULT_RENDER_DPI
    return value if value > 0 else _DEFAULT_RENDER_DPI


def export_slide_to_image(pptx_path: str | Path, slide_index: int, output_image_path: str | Path) -> bool:
    """把指定页幻灯片渲染为 JPEG，成功返回 True；失败返回 False（不抛异常）。

    平台自动识别：
      - Windows：优先调用本地 PowerPoint(comtypes COM) 导出；不可用/失败再回退 LibreOffice。
      - 其他平台：使用 LibreOffice 转 PDF + pdf2image。
    """
    pptx_path = Path(pptx_path)
    output_image_path = Path(output_image_path)

    if platform.system() == "Windows":
        if _export_via_powerpoint(pptx_path, slide_index, output_image_path):
            return True
        # 本地 PowerPoint 不可用/失败 → 继续尝试 LibreOffice
    return _export_via_libreoffice(pptx_path, slide_index, output_image_path)


def _export_via_powerpoint(pptx_path: Path, slide_index: int, output_image_path: Path) -> bool:
    """Windows：调用本地 PowerPoint 把指定页导出为 JPG。缺少 comtypes/PowerPoint 或失败返回 False。"""
    try:
        import comtypes.client  # 仅 Windows 且安装了 comtypes 时可用
    except ImportError:
        logger.info("未安装 comtypes 或非 Windows，跳过本地 PowerPoint 渲染，尝试 LibreOffice。")
        return False

    powerpoint = None
    presentation = None
    try:
        output_image_path.parent.mkdir(parents=True, exist_ok=True)
        powerpoint = comtypes.client.CreateObject("PowerPoint.Application")
        try:
            powerpoint.Visible = 1
        except Exception:  # noqa: BLE001
            pass
        presentation = powerpoint.Presentations.Open(str(pptx_path.resolve()))
        slide = presentation.Slides(slide_index + 1)  # PowerPoint 索引从 1 开始
        slide.Export(str(output_image_path.resolve()), "JPG", 1920, 1080)
        return output_image_path.is_file()
    except Exception:  # noqa: BLE001
        logger.warning("本地 PowerPoint 渲染失败，回退 LibreOffice。", exc_info=True)
        return False
    finally:
        try:
            if presentation is not None:
                presentation.Close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if powerpoint is not None:
                powerpoint.Quit()
        except Exception:  # noqa: BLE001
            pass


def _export_via_libreoffice(pptx_path: Path, slide_index: int, output_image_path: Path) -> bool:
    """用 LibreOffice 把整份 pptx 转 PDF，再用 pdf2image 取指定页存为 JPEG。"""
    try:
        soffice_path = find_libreoffice_soffice()
    except Exception:  # noqa: BLE001
        logger.warning("未找到 LibreOffice(soffice)，且本地 PowerPoint 不可用，跳过 PPTX 页面渲染。")
        return False

    out_dir = output_image_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / (pptx_path.stem + ".pdf")

    try:
        subprocess.run(
            [
                str(soffice_path),
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(pptx_path.resolve()),
            ],
            check=True,
            timeout=_CONVERT_TIMEOUT_SECONDS,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.warning("LibreOffice 可执行文件不可用，跳过渲染。", exc_info=True)
        return False
    except subprocess.CalledProcessError as exc:
        logger.warning("LibreOffice 转 PDF 失败：%s", exc.stderr.decode(errors="ignore") if exc.stderr else exc)
        return False
    except subprocess.TimeoutExpired:
        logger.warning("LibreOffice 转 PDF 超时。")
        return False

    if not pdf_path.is_file():
        logger.warning("LibreOffice 未生成预期 PDF：%s", pdf_path)
        return False

    try:
        from pdf2image import convert_from_path  # 延迟导入，缺失时优雅降级
    except ImportError:
        logger.warning("未安装 pdf2image（或缺少系统 poppler），跳过 PPTX 页面渲染。", exc_info=True)
        _safe_unlink(pdf_path)
        return False

    try:
        pages = convert_from_path(
            str(pdf_path),
            dpi=_resolve_render_dpi(),
            first_page=slide_index + 1,
            last_page=slide_index + 1,
        )
        if not pages:
            logger.warning("PDF 中未找到第 %s 页。", slide_index + 1)
            return False
        pages[0].save(str(output_image_path), "JPEG")
        return True
    except Exception:  # noqa: BLE001
        logger.warning("PDF 转图片失败，跳过该页渲染。", exc_info=True)
        return False
    finally:
        _safe_unlink(pdf_path)


def _safe_unlink(path: Path) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
