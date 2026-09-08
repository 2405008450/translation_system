from __future__ import annotations

import logging
import os
import statistics
import threading
from pathlib import Path
from typing import Any


_logger = logging.getLogger(__name__)
_OCR_INSTANCE: Any | None = None
_OCR_INSTANCE_LOCK = threading.Lock()
_OCR_PREDICT_LOCK = threading.Lock()
_DEFAULT_MIN_CONFIDENCE = 0.70


class PsdOcrError(RuntimeError):
    """PSD PaddleOCR 初始化或识别失败。"""


def recognize_psd_candidates(
    candidate_directory: Path,
    candidates: list[dict[str, Any]],
    *,
    source_language: str | None = None,
) -> list[dict[str, Any]]:
    """使用本地 PP-OCRv6 识别 Node runtime 导出的安全像素层候选。"""
    if not candidates or not _ocr_enabled():
        return []

    candidate_directory = candidate_directory.resolve()
    ocr = _get_ocr_instance()
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        filename = str(candidate.get("candidate_file") or "")
        image_path = (candidate_directory / filename).resolve()
        if not filename or image_path.parent != candidate_directory or not image_path.is_file():
            raise PsdOcrError("PSD OCR 候选图片路径无效")

        try:
            with _OCR_PREDICT_LOCK:
                predictions = list(ocr.predict(str(image_path)))
        except Exception as exc:  # Paddle 内部错误类型在版本间不稳定
            raise PsdOcrError(
                f"PaddleOCR 识别失败（图层 {candidate.get('layer_path', '')}）：{exc}"
            ) from exc

        recognized = _parse_predictions(
            predictions,
            scale=_positive_float(candidate.get("ocr_scale"), default=1.0),
            image_width=_positive_int(candidate.get("image_width")),
            image_height=_positive_int(candidate.get("image_height")),
        )
        if recognized is None:
            continue
        results.append(
            {
                **{
                    key: value
                    for key, value in candidate.items()
                    if key not in {"candidate_file", "ocr_scale", "image_width", "image_height"}
                },
                **recognized,
                "ocr_engine": "paddleocr",
                "ocr_model": "PP-OCRv6",
                "ocr_source_language": str(source_language or ""),
            }
        )
    return results


def get_psd_ocr_health() -> dict[str, Any]:
    """返回 OCR 配置，不为健康检查加载大型模型。"""
    try:
        import paddle
        import paddleocr
    except ImportError:
        return {"engine": "paddleocr", "available": False}
    return {
        "engine": "paddleocr",
        "available": True,
        "paddleocr_version": paddleocr.__version__,
        "paddle_version": paddle.__version__,
        "model": "PP-OCRv6",
        "device": os.getenv("PSD_OCR_DEVICE", "cpu").strip() or "cpu",
    }


def _get_ocr_instance() -> Any:
    global _OCR_INSTANCE
    if _OCR_INSTANCE is not None:
        return _OCR_INSTANCE
    with _OCR_INSTANCE_LOCK:
        if _OCR_INSTANCE is not None:
            return _OCR_INSTANCE
        try:
            from paddleocr import PaddleOCR

            _OCR_INSTANCE = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_mkldnn=False,
                device=os.getenv("PSD_OCR_DEVICE", "cpu").strip() or "cpu",
            )
        except Exception as exc:
            raise PsdOcrError(
                "PaddleOCR 初始化失败；请确认 PP-OCRv6 模型已部署到本地缓存："
                f"{exc}"
            ) from exc
    return _OCR_INSTANCE


def _parse_predictions(
    predictions: list[Any],
    *,
    scale: float,
    image_width: int,
    image_height: int,
) -> dict[str, Any] | None:
    lines: list[tuple[str, float, tuple[float, float, float, float]]] = []
    for prediction in predictions:
        payload = getattr(prediction, "json", prediction)
        if callable(payload):
            payload = payload()
        if not isinstance(payload, dict):
            continue
        result = payload.get("res", payload)
        if not isinstance(result, dict):
            continue
        texts = result.get("rec_texts") or []
        scores = result.get("rec_scores") or []
        boxes = result.get("rec_boxes") or []
        polygons = result.get("rec_polys") or []
        for index, raw_text in enumerate(texts):
            text = _normalize_text(raw_text)
            score = _finite_float(scores[index] if index < len(scores) else 0.0)
            if not text or score < _minimum_confidence() or not _contains_meaningful_text(text):
                continue
            raw_box = boxes[index] if index < len(boxes) else None
            raw_polygon = polygons[index] if index < len(polygons) else None
            bounds = _read_bounds(raw_box, raw_polygon)
            if bounds is None:
                continue
            lines.append((text, score, bounds))

    if not lines:
        return None
    lines.sort(key=lambda item: (item[2][1], item[2][0]))
    left = max(min(item[2][0] for item in lines) / scale, 0.0)
    top = max(min(item[2][1] for item in lines) / scale, 0.0)
    right = min(max(item[2][2] for item in lines) / scale, float(image_width))
    bottom = min(max(item[2][3] for item in lines) / scale, float(image_height))
    if right <= left or bottom <= top:
        return None

    heights = [(item[2][3] - item[2][1]) / scale for item in lines]
    weighted_length = sum(max(len(item[0]), 1) for item in lines)
    confidence = sum(item[1] * max(len(item[0]), 1) for item in lines) / weighted_length
    return {
        "text": " ".join(item[0] for item in lines),
        "ocr_bounds": {
            "left": int(left),
            "top": int(top),
            "right": int(min(max(round(right), 0), image_width)),
            "bottom": int(min(max(round(bottom), 0), image_height)),
        },
        "ocr_confidence": round(confidence * 100, 2),
        "font_size": min(max(round(statistics.median(heights)), 8), 128),
    }


def _read_bounds(raw_box: Any, raw_polygon: Any) -> tuple[float, float, float, float] | None:
    values = _to_float_list(raw_box)
    if len(values) >= 4:
        left, top, right, bottom = values[:4]
        if right > left and bottom > top:
            return left, top, right, bottom

    points = raw_polygon.tolist() if hasattr(raw_polygon, "tolist") else raw_polygon
    if not isinstance(points, (list, tuple)):
        return None
    coordinates = [_to_float_list(point) for point in points]
    coordinates = [point for point in coordinates if len(point) >= 2]
    if not coordinates:
        return None
    return (
        min(point[0] for point in coordinates),
        min(point[1] for point in coordinates),
        max(point[0] for point in coordinates),
        max(point[1] for point in coordinates),
    )


def _to_float_list(value: Any) -> list[float]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        return []
    result: list[float] = []
    for item in value:
        number = _finite_float(item)
        result.append(number)
    return result


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", "\n").split())


def _contains_meaningful_text(value: str) -> bool:
    return sum(character.isalnum() for character in value) >= 2


def _minimum_confidence() -> float:
    raw_value = os.getenv("PSD_OCR_MIN_CONFIDENCE", "").strip()
    try:
        return min(max(float(raw_value), 0.0), 1.0) if raw_value else _DEFAULT_MIN_CONFIDENCE
    except ValueError:
        return _DEFAULT_MIN_CONFIDENCE


def _ocr_enabled() -> bool:
    return os.getenv("PSD_OCR_ENABLED", "").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def _finite_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and abs(number) != float("inf") else 0.0


def _positive_float(value: Any, *, default: float) -> float:
    number = _finite_float(value)
    return number if number > 0 else default


def _positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(number, 0)
