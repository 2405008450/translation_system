"""
vlm_client.py —— 视觉复核：把整页截图 + 该页文本框清单发给多模态模型，判断溢出并回传建议。

改编自参考实现 app/文件/model.py::call_vlm_api_batch：
  - 不再使用独立 openai 客户端，改为复用系统 llm_service.request_chat_completion
    （其 messages 原样透传给 OpenAI 兼容接口，支持多模态 content 数组）。
  - 视觉模型通过 model_override 指定（前端传入或配置默认），走系统已配置的 provider。
提示词与返回 JSON schema 沿用参考实现。任何失败返回 None，交由上层启发式降级。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re

from app.config import get_settings
from app.services.llm_service import (
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseValidationError,
    request_chat_completion,
)

logger = logging.getLogger(__name__)

# both 模式专用的几何约束追加段：强调"贴着原位置扩展"，抑制大幅平移
_BOTH_MODE_GEO_CONSTRAINT = """
     ★对 new_left / new_top 有【额外强约束】（both 模式专用，非常重要）：
       · 必须尽量贴近当前 Left/Top，只做"贴着原框位置扩展"式的微调，
         而不是把文本框大幅搬移到页面的其他区域；
       · 默认应保持 new_left≈Left、new_top≈Top（允许的自然浮动幅度约为
         当前 Width/Height 的 30% 以内）；
       · 只有当原位置确实无法容纳时，才允许做小范围移动，且移动方向要有明确理由；
       · 主要应通过增大 new_width / new_height 来容纳溢出文字；
       · 严禁给出与原位置明显不同区域的坐标。
"""

_DEFAULT_MODE_GEO_CONSTRAINT = """
     不溢出不与相邻元素重叠的最佳文本框位置与大小（浮点，单位英寸）。
"""


def _normalize_boxtext(text: str | None, limit: int = 120) -> str:
    """把多行文本压成单行并截断，避免 prompt 过长。"""
    t = re.sub(r"\s+", " ", text or "").strip()
    return t[:limit] + ("…" if len(t) > limit else "")


def _build_prompt(boxes: list[dict], mode: str | None) -> str:
    lines = []
    for b in boxes:
        lines.append(
            f'- id="{b["id"]}" | Left:{b["left"]:.2f} Top:{b["top"]:.2f} '
            f'Width:{b["width"]:.2f} Height:{b["height"]:.2f}(寸) | '
            f'文本:"{_normalize_boxtext(b["text"])}"'
        )
    box_list_text = "\n".join(lines)
    geo_constraint = _BOTH_MODE_GEO_CONSTRAINT if mode == "both" else _DEFAULT_MODE_GEO_CONSTRAINT

    return f"""
你是一个精通 PPT 自动排版的设计师模型。这是一整页 PPT 的截图。
图中每个文本框都用蓝色线框标出，并在其左上角贴有一个【红字黄底的数字标签(id)】。

【重要判定规则】因为开启了文字强制全显，超出文本框的文字**不会被截断**，而是会溢出到蓝色框外部继续显示。
所以不要用"文字有没有被截断"来判断，而要仔细对比"文字实际渲染的边缘"与"该文本框蓝色线框的边界"：
理想状态是文字**完全落在蓝框内部并四周留有余白**。只要出现以下任一情况都算溢出 overflow=true：
  · 文字越过蓝框的底边/任一边界（延伸到框外）；
  · 文字虽未越界，但已经**紧贴、碰到或几乎顶到**蓝框的边线（没有余白）；
  · 蓝色框或框内文字被图片等遮挡或有重叠部分。
只有当文字四周都在蓝框内、且与边线之间留有明显空隙时，才算 overflow=false。

本页所有待检查文本框如下（id 与图中标签一一对应，单位：英寸）：
{box_list_text}

请你依据【图中标签id】逐个定位对应文本框并分析判断，每个框有三种互斥状态（overflow / underflow / 正常）：

一、若文字越界或贴边/顶边（视为溢出），overflow=true、underflow=false，并给出：
  1) overflow_ratio：文字实际渲染总高度 ÷ 框当前 Height，并按"留约10%余白"在测得值上乘 1.2 左右
     （浮点，仅 overflow=true 时 > 1.0）。
  2) new_left/new_top/new_width/new_height：字号不变时能完美容纳全部文字、不溢出不与相邻元素重叠的最佳位置与大小
     （浮点，单位英寸）。

二、若文字明显偏小、框内留有大面积空白，且放大字号后不会与相邻元素重叠，
    则 underflow=true、overflow=false、overflow_ratio=1.0，new_* 保持当前值（放大由程序计算）。
    ⚠只要放大后可能碰到相邻元素、或该框贴边/被遮挡，就【不要】判 underflow。

三、若文字完全在蓝框内、四周留有适中余白，则 overflow=false、underflow=false、overflow_ratio=1.0，new_* 保持当前值。
{geo_constraint}
必须为每个 id 都返回一条结果。严格只返回如下 JSON（不要 ```json 包裹、不要多余解释）：
{{"results": [{{"id": "对应标签id", "overflow": true/false, "underflow": true/false, "overflow_ratio": 浮点数, "new_left": 浮点数, "new_top": 浮点数, "new_width": 浮点数, "new_height": 浮点数, "reason": "简述"}}]}}
"""


def _balance_json(text: str) -> str:
    """修复被截断的 JSON：跟踪字符串/转义，补齐未闭合的引号与括号。

    只做"补尾"修复（模型常见的输出被 max_tokens 截断、少写结尾括号的情况）。
    若截断发生在 token 中间导致仍非法，调用方会捕获异常并回退启发式。
    """
    stack: list[str] = []
    in_str = False
    escaped = False
    for ch in text:
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack:
                stack.pop()
    suffix = ""
    if in_str:
        suffix += '"'
    suffix += "".join(reversed(stack))
    return text + suffix


import json
import re
from json_repair import repair_json

def _extract_json_payload(raw_content: str) -> dict:
    """从模型返回中稳健地提取 JSON 对象。

    依次尝试：
      1) 剥离 ```json 围栏、定位首个 { ；
      2) raw_decode 只解析开头首个完整 JSON 对象，忽略尾部多余字符（"Extra data"）；
      3) 若首个对象不完整（被 max_tokens 截断），补齐未闭合的引号/括号后再解析。
    """
    if not raw_content or not raw_content.strip():
        raise ValueError("模型返回为空。")
    cleaned = re.sub(r"```json|```", "", raw_content).strip()
    start = cleaned.find("{")
    if start > 0:
        cleaned = cleaned[start:]

    decoder = json.JSONDecoder()
    try:
        # raw_decode 从头解析首个 JSON 值，返回 (对象, 结束位置)，天然忽略尾部多余数据。
        obj, _end = decoder.raw_decode(cleaned)
        return obj
    except json.JSONDecodeError:
        pass
    # 截断修复：补齐未闭合的引号/括号后重试。
    return json.loads(_balance_json(cleaned))


def _parse_results(raw_content: str) -> dict[str, dict]:
    data = _extract_json_payload(raw_content)
    if not isinstance(data, dict):
        raise ValueError("模型返回的 JSON 顶层不是对象。")
    results: dict[str, dict] = {}
    for item in data.get("results", []):
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        is_over = bool(item.get("overflow"))
        is_under = bool(item.get("underflow")) and not is_over
        if not is_over and not is_under:
            continue
        entry: dict = {}
        if is_over:
            try:
                entry.update(
                    {
                        "left": float(item["new_left"]),
                        "top": float(item["new_top"]),
                        "width": float(item["new_width"]),
                        "height": float(item["new_height"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                pass
            try:
                ratio = float(item["overflow_ratio"])
                if ratio > 1.0:
                    entry["overflow_ratio"] = ratio
            except (KeyError, TypeError, ValueError):
                pass
        if is_under:
            entry["underflow"] = True
        if item.get("reason"):
            entry["reason"] = item["reason"]
        if entry:
            results[str(item["id"])] = entry
    return results


# 视觉复核需走支持多模态 image_url 的 provider（deepseek 等不支持）。
# provider / 可选模型列表 / 默认模型均可经环境变量配置（见 app/config.py）。
_FALLBACK_VLM_PROVIDER = "openrouter"
_FALLBACK_VLM_MODELS = (
    "google/gemini-3.1-pro-preview",
    "google/gemini-3.6-flash",
    "anthropic/claude-sonnet-5",
    "anthropic/claude-fable-5",
)


def _vlm_provider() -> str:
    provider = (getattr(get_settings(), "pptx_layout_vlm_provider", "") or "").strip()
    return provider or _FALLBACK_VLM_PROVIDER


def vlm_provider() -> str:
    """对外公开：当前视觉复核使用的 provider（供报告记录）。"""
    return _vlm_provider()


def _supported_vlm_models() -> tuple[str, ...]:
    models = getattr(get_settings(), "pptx_layout_vlm_models", None)
    if isinstance(models, (list, tuple)):
        cleaned = tuple(str(m).strip() for m in models if str(m).strip())
        if cleaned:
            return cleaned
    return _FALLBACK_VLM_MODELS


def _default_vlm_model() -> str:
    configured = (getattr(get_settings(), "pptx_layout_vlm_model", "") or "").strip()
    supported = _supported_vlm_models()
    if configured and configured in supported:
        return configured
    return supported[0]


def resolve_vlm_model(model: str | None) -> str:
    """把传入模型规整为受支持的视觉模型；非法/为空则用默认。"""
    value = (model or "").strip()
    return value if value in _supported_vlm_models() else _default_vlm_model()


def analyze_page_overflow(
    image_path: str,
    boxes: list[dict],
    *,
    mode: str | None = None,
    model: str | None = None,
) -> dict[str, dict] | None:
    """把整页截图 + 文本框清单发给多模态模型（provider/模型由配置决定），返回 {id: 结果}。

    失败（模型未配置/请求失败/解析失败/无视觉能力）返回 None，交由上层启发式降级。
    """
    if not boxes:
        return {}

    try:
        with open(image_path, "rb") as f:
            base64_image = base64.b64encode(f.read()).decode("utf-8")
    except OSError:
        logger.warning("读取页面截图失败：%s", image_path, exc_info=True)
        return None

    prompt = _build_prompt(boxes, mode)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
            ],
        }
    ]

    try:
        result = asyncio.run(
            request_chat_completion(
                messages=messages,
                provider=_vlm_provider(),
                model_override=resolve_vlm_model(model),
                response_format={"type": "json_object"},
                temperature=0,
                allow_fallback=False,
            )
        )
    except (LLMConfigurationError, LLMRequestError, LLMResponseValidationError) as exc:
        logger.warning("PPTX 版式视觉复核调用失败：%s", exc)
        return None
    except RuntimeError as exc:
        # asyncio.run 在已有事件循环中会抛 RuntimeError
        logger.warning("PPTX 版式视觉复核无法在当前上下文运行：%s", exc)
        return None
    except Exception:  # noqa: BLE001
        logger.warning("PPTX 版式视觉复核发生未预期错误。", exc_info=True)
        return None

    try:
        return _parse_results(result.content)
    except Exception:  # noqa: BLE001
        raw_preview = (result.content or "")[:2000]
        logger.warning(
            "PPTX 版式视觉复核返回内容解析失败。模型原始返回(前2000字符)：%s",
            raw_preview,
            exc_info=True,
        )
        return None
