from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from app.config import get_settings
from app.services.llm_service import request_chat_completion

SOURCE_DOMAIN_MAP = {
    "wikipedia": ["wikipedia.org"],
    "iate": ["iate.europa.eu"],
    "linguee": ["linguee.com"],
}


def _parse_json_items(content: str) -> list[dict[str, Any]]:
    cleaned = re.sub(r"```(?:json)?|```", "", content or "").strip()
    match = re.search(r"\[.*\]", cleaned, re.S)
    if not match:
        return []
    try:
        payload = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _source_name(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    return hostname.removeprefix("www.") or "Web"


def _normalize_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    source_text = str(item.get("source_text") or "").strip()
    target_text = str(item.get("target_text") or "").strip()
    source_url = str(item.get("source_url") or "").strip()
    if not source_text or not target_text or not source_url.startswith(("http://", "https://")):
        return None
    try:
        confidence = float(item.get("confidence", 0.6))
    except (TypeError, ValueError):
        confidence = 0.6
    return {
        "source_text": source_text[:500],
        "target_text": target_text[:1000],
        "source_name": str(item.get("source_name") or _source_name(source_url))[:100],
        "source_url": source_url[:2000],
        "confidence": max(0.0, min(confidence, 1.0)),
        "note": str(item.get("note") or "")[:500],
    }


async def query_online_terms(
    source_text: str,
    source_language: str,
    target_language: str,
    sources: list[str] | None = None,
) -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.openrouter_api_key:
        raise RuntimeError("未配置 OPENROUTER_API_KEY，无法进行联网术语查询。")

    domains = [
        domain
        for source in sources or []
        for domain in SOURCE_DOMAIN_MAP.get(source, [])
    ]
    domain_hint = f"只优先使用这些网站：{', '.join(domains)}。" if domains else "优先使用权威词典、标准术语库或官方资料。"
    messages = [
        {
            "role": "system",
            "content": (
                "你是翻译术语检索助手。必须先使用联网搜索查证，再返回结果。"
                "只返回 JSON 数组，不要 Markdown，不要解释。每项字段为："
                "source_text、target_text、source_name、source_url、confidence、note。"
                "source_url 必须是实际搜索结果中的完整 URL，不要编造链接；"
                "confidence 是 0 到 1 的数字。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"请为源语言 {source_language} 到目标语言 {target_language} 检索术语。"
                f"当前句段或查询词：{source_text}\n{domain_hint}"
                "最多返回 8 个最可靠的术语候选；如果无法确认术语，返回空数组。"
            ),
        },
    ]
    tool_parameters: dict[str, Any] = {
        "engine": settings.online_term_search_engine,
        "max_results": max(1, min(settings.online_term_search_max_results, 10)),
        "max_uses": max(1, min(settings.online_term_search_max_uses, 3)),
        "max_total_results": max(1, min(settings.online_term_search_max_results * 2, 20)),
    }
    if domains:
        tool_parameters["allowed_domains"] = domains

    completion = await request_chat_completion(
        messages=messages,
        provider="openrouter",
        model_override=settings.online_term_search_model or None,
        temperature=0,
        allow_fallback=False,
        tools=[{"type": "openrouter:web_search", "parameters": tool_parameters}],
        extra_body={"max_tool_calls": max(2, min(settings.online_term_search_max_uses + 1, 4))},
    )
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_item in _parse_json_items(completion.content):
        item = _normalize_item(raw_item)
        if item is None:
            continue
        key = (item["source_text"].casefold(), item["target_text"].casefold())
        if key not in seen:
            seen.add(key)
            results.append(item)
    return results[:8]
