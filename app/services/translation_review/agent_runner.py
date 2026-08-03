"""
Agent 批次运行器 — 通用 LLM 批次调用 + seq+sid 校验 + 缺失补发重试

各 CategoryAgent 共用此模块；number_check_service 将来也可改用相同底层。

公开函数：
    run_ai_batch(payloads, prompt_builder, ...) → list[RawFinding]
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services.llm_service import (
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseValidationError,
    request_chat_completion,
)
from app.services.translation_review.llm_gate import llm_gate

logger = logging.getLogger(__name__)

_MAX_RETRIES = 1
_NO_ISSUE_HINTS = [
    "no error", "no issue", "没有发现", "未发现", "无错误", "all correct", "符合规范",
]


def _safe_parse_json(content: str) -> tuple[list[dict[str, Any]], str]:
    """解析模型返回的 JSON 数组，返回 (列表, 状态)。"""
    if not content or not content.strip():
        return [], "empty"

    stripped_lower = content.strip().lower()
    if any(hint in stripped_lower for hint in _NO_ISSUE_HINTS) and "{" not in content:
        return [], "ok"

    try:
        cleaned = re.sub(r"```json|```", "", content).strip()
        match = re.search(r"\[.*\]", cleaned, re.S)
        candidate = match.group() if match else cleaned
        parsed = json.loads(candidate)
        if isinstance(parsed, list):
            return parsed, "ok"
        if isinstance(parsed, dict):
            return [parsed], "ok"
        return [], "parse_failed"
    except Exception:  # noqa: BLE001
        return [], "parse_failed"


def _validate_seq_sid(
    parsed: list[dict],
    block: list[dict],
) -> tuple[dict[int, dict], list[str]]:
    """
    校验 seq + sid 映射一致性。
    返回 ({seq: parsed_item}, [丢弃的 sid 列表])。
    """
    expected: dict[int, str] = {p["seq"]: p["sid"] for p in block}
    result: dict[int, dict] = {}
    dropped: list[str] = []

    for item in parsed:
        if not isinstance(item, dict):
            continue
        seq = item.get("seq")
        sid = item.get("sid", "")
        if seq is None:
            continue
        try:
            seq = int(seq)
        except (TypeError, ValueError):
            continue

        expected_sid = expected.get(seq)
        if expected_sid is None:
            dropped.append(f"seq={seq} not in block")
            continue
        if str(sid) != str(expected_sid):
            dropped.append(f"seq={seq} sid mismatch: expected {expected_sid}, got {sid}")
            continue
        result[seq] = item

    return result, dropped


async def _call_llm_block(
    block: list[dict],
    prompt_builder: Any,
    provider: str,
    model: str | None,
    web_tools: list[dict] | None,
    extra_body: dict | None,
) -> tuple[dict[int, dict], str]:
    """
    调用 LLM 处理一个批次，返回 ({seq: result}, status)。
    prompt_builder(payloads) → list[dict]  构造 messages。

    当 web_tools 存在时（联网查证），强制使用 openrouter provider 并禁用 fallback，
    避免把 openrouter:web_search 发给不支持该字段的 provider（如 deepseek）。
    """
    messages = prompt_builder(block)
    # 联网工具仅 openrouter 支持，强制锁定 provider 避免 fallback 到其他 provider
    effective_provider = "openrouter" if web_tools else provider
    allow_fallback = not bool(web_tools)  # 有联网工具时不允许 fallback
    try:
        async with llm_gate():
            completion = await request_chat_completion(
                messages=messages,
                provider=effective_provider,
                model_override=model or None,
                temperature=0,
                tools=web_tools,
                extra_body=extra_body,
                allow_fallback=allow_fallback,
            )
    except (LLMConfigurationError, LLMRequestError, LLMResponseValidationError) as exc:
        logger.warning("translation_review llm call failed: %s", exc)
        status = "api_error"
        if hasattr(exc, "status_code") and exc.status_code == 429:
            status = "rate_limited"
        return {}, status
    except Exception as exc:  # noqa: BLE001
        logger.exception("translation_review llm call unexpected error: %s", exc)
        return {}, "api_error"

    parsed, parse_status = _safe_parse_json(completion.content)
    if parse_status != "ok":
        return {}, parse_status

    seq_map, dropped = _validate_seq_sid(parsed, block)
    if dropped:
        logger.warning(
            "translation_review seq/sid mismatch, dropped %d items: %s",
            len(dropped),
            dropped[:5],
        )
    return seq_map, "ok" if seq_map else "empty"


async def run_ai_batch(
    payloads: list[dict],
    *,
    prompt_builder: Any,
    batch_size: int = 12,
    provider: str = "auto",
    model: str | None = None,
    web_tools: list[dict] | None = None,
    extra_body: dict | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """
    对 payloads 分批调用 LLM，返回：
    - findings: list of {seq, sid, findings: [...]} 已验证条目
    - stats: {llm_request_count, retry_count, dropped_count, web_search_requests, status}

    payloads 每项须含 'seq'(int), 'sid'(str), 'source_text', 'target_text' 等。
    prompt_builder 接收 block: list[dict] → list[dict] (messages)。
    """
    blocks = [payloads[i:i + batch_size] for i in range(0, len(payloads), batch_size)]
    all_results: dict[int, dict] = {}
    llm_requests = 0
    retries = 0
    dropped = 0
    web_search_total = 0
    last_status = "ok"

    for block in blocks:
        seq_map, status = await _call_llm_block(
            block, prompt_builder, provider, model, web_tools, extra_body
        )
        llm_requests += 1
        last_status = status
        all_results.update(seq_map)

        # 缺失条目补发重试
        for _ in range(_MAX_RETRIES):
            missing = [p for p in block if p["seq"] not in all_results]
            if not missing:
                break
            retry_map, retry_status = await _call_llm_block(
                missing, prompt_builder, provider, model, web_tools, extra_body
            )
            llm_requests += 1
            retries += 1
            all_results.update(retry_map)
            last_status = retry_status

        # 仍缺失的标 missing
        for p in block:
            if p["seq"] not in all_results:
                all_results[p["seq"]] = {"seq": p["seq"], "sid": p["sid"],
                                          "has_issue": False, "findings": [],
                                          "_missing": True}
                dropped += 1

    findings: list[dict] = list(all_results.values())

    return findings, {
        "llm_request_count": llm_requests,
        "retry_count": retries,
        "dropped_count": dropped,
        "web_search_requests": web_search_total,
        "status": last_status,
    }
