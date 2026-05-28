from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Iterable

from app.config import Settings, get_settings
from app.services.language_pairs import LANGUAGE_LABELS
from app.services.llm_service import (
    LLMConfigurationError,
    LLMProvider,
    LLMRequestError,
    LLMResponseValidationError,
    request_chat_completion,
)
from app.services.normalizer import normalize_match_text, normalize_text

logger = logging.getLogger(__name__)

TERM_EXTRACTION_MODEL = "google/gemini-3.5-flash"
TERM_EXTRACTION_MODEL_OPTIONS = (
    "google/gemini-3.5-flash",
    "google/gemini-3.1-pro-preview",
    "google/gemini-3.1-flash-lite",
    "google/gemini-3-flash-preview",
    "google/gemini-2.5-pro",
    "google/gemini-2.5-flash",
    "google/gemini-2.5-flash-lite",
    "openai/gpt-chat-latest",
    "openai/gpt-5.4",
    "openai/gpt-5.4-mini",
    "openai/gpt-5.4-nano",
    "openai/gpt-5.3-chat",
    "openai/gpt-5.2",
    "openai/gpt-5.2-chat",
    "openai/gpt-5.1",
    "openai/gpt-5.1-chat",
    "openai/gpt-5-mini",
    "openai/gpt-5-nano",
    "deepseek/deepseek-chat",
)
TERM_EXTRACTION_CHUNK_MAX_CHARS = 8000
TERM_EXTRACTION_CHUNK_MAX_TERMS = 40
TERM_EXTRACTION_PROMPT_MAX_CHARS = 4000
PURE_NUMBER_RE = re.compile(r"^[\d\s,.\-+/%()（）$€¥￥£:：]+$")


class TermExtractionError(RuntimeError):
    """术语提取失败。"""


@dataclass(frozen=True)
class ExtractedTerm:
    source_text: str
    target_text: str
    source_normalized: str


@dataclass(frozen=True)
class TermExtractionResult:
    terms: list[ExtractedTerm]
    provider: str
    model: str


@dataclass(frozen=True)
class TermExtractionModelAttempt:
    provider: LLMProvider
    model: str


def _language_label(language_code: str | None, fallback: str) -> str:
    if not language_code:
        return fallback
    label = LANGUAGE_LABELS.get(language_code, language_code)
    return f"{label}（{language_code}）"


def _collect_source_texts(segments: Iterable[object]) -> list[str]:
    source_texts: list[str] = []
    for segment in segments:
        source_text = normalize_text(str(getattr(segment, "source_text", "") or ""))
        if source_text:
            source_texts.append(source_text)
    return source_texts


def _chunk_source_texts(source_texts: list[str]) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    current_chars = 0

    for source_text in source_texts:
        next_chars = len(source_text) + 1
        if current and current_chars + next_chars > TERM_EXTRACTION_CHUNK_MAX_CHARS:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(source_text)
        current_chars += next_chars

    if current:
        chunks.append(current)
    return chunks


def _build_term_extraction_messages(
    chunk_texts: list[str],
    source_language: str,
    target_language: str,
    max_terms: int,
    extraction_prompt: str = "",
) -> list[dict[str, str]]:
    source_label = _language_label(source_language, "源语言")
    target_label = _language_label(target_language, "目标语言")
    numbered_text = "\n".join(
        f"[{index}] {source_text}"
        for index, source_text in enumerate(chunk_texts, start=1)
    )
    system_prompt = (
        "你是专业翻译项目的术语抽取专家。"
        "你必须从给定源文中抽取适合作为术语库条目的名词、专有名词、产品名、组织名、功能名、技术名词和固定短语，"
        "并给出对应目标语言译文。"
        "只输出一个 JSON 对象，不要输出 Markdown、解释、注释或额外字段。"
        "JSON 格式必须严格为：{\"terms\":[{\"source_text\":\"...\",\"target_text\":\"...\"}]}。"
    )
    prompt_text = normalize_text(extraction_prompt)[:TERM_EXTRACTION_PROMPT_MAX_CHARS]
    prompt_section = ""
    if prompt_text:
        prompt_section = (
            "行业、文章类型和应用场景补充：\n"
            f"{prompt_text}\n"
            "请优先识别该场景中真正需要统一译法的专业术语、缩略语、系统名、产品名、功能名、流程名和固定表达。\n\n"
        )
    user_prompt = (
        f"语言对：{source_label} -> {target_label}\n"
        f"最多输出 {max_terms} 条术语。\n"
        f"{prompt_section}"
        "要求：\n"
        "1. source_text 必须是源文中真实出现的连续文本，不要改写、概括或翻译 source_text。\n"
        "2. target_text 必须是对应的目标语言术语译文，不要输出解释。\n"
        "3. 不要抽取完整句子、纯数字、日期、孤立标点、过长段落或泛泛词语。\n"
        "4. 去除重复术语；同义或大小写差异保留最规范的一条。\n"
        "5. 如果没有可靠术语，输出 {\"terms\":[]}。\n\n"
        "源文：\n"
        f"{numbered_text}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _strip_json_fence(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    return text


def _parse_term_json(raw_text: str) -> list[dict]:
    text = _strip_json_fence(raw_text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise LLMResponseValidationError("术语提取结果不是有效 JSON。") from None
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMResponseValidationError("术语提取结果不是有效 JSON。") from exc

    terms = payload.get("terms") if isinstance(payload, dict) else None
    if terms is None and isinstance(payload, list):
        terms = payload
    if not isinstance(terms, list):
        raise LLMResponseValidationError("术语提取结果缺少 terms 数组。")
    return [item for item in terms if isinstance(item, dict)]


def _term_appears_in_source(source_text: str, source_corpus: str) -> bool:
    if source_text.casefold() in source_corpus.casefold():
        return True
    return normalize_text(source_text).casefold() in normalize_text(source_corpus).casefold()


def _filter_terms(
    raw_terms: list[dict],
    source_texts: list[str],
    existing_keys: set[str],
    remaining_limit: int,
) -> list[ExtractedTerm]:
    source_corpus = "\n".join(source_texts)
    full_segment_keys = {normalize_match_text(text) or normalize_text(text) for text in source_texts}
    filtered: list[ExtractedTerm] = []

    for item in raw_terms:
        source_text = normalize_text(str(item.get("source_text") or ""))
        target_text = normalize_text(str(item.get("target_text") or ""))
        source_key = normalize_match_text(source_text) or source_text

        if not source_text or not target_text or not source_key:
            continue
        if source_key in existing_keys:
            continue
        if PURE_NUMBER_RE.fullmatch(source_text):
            continue
        if "\n" in source_text or len(source_text) > 120:
            continue
        if source_key in full_segment_keys and len(source_text) > 20:
            continue
        if not _term_appears_in_source(source_text, source_corpus):
            continue

        filtered.append(ExtractedTerm(
            source_text=source_text,
            target_text=target_text,
            source_normalized=source_key,
        ))
        existing_keys.add(source_key)
        if len(filtered) >= remaining_limit:
            break

    return filtered


def merge_extracted_terms(results: Iterable[TermExtractionResult], max_terms: int = 150) -> list[ExtractedTerm]:
    safe_max_terms = min(max(int(max_terms or 150), 1), 300)
    merged: list[ExtractedTerm] = []
    seen_keys: set[str] = set()

    for result in results:
        for term in result.terms:
            source_key = term.source_normalized or normalize_match_text(term.source_text) or term.source_text
            if not source_key or source_key in seen_keys:
                continue
            merged.append(term)
            seen_keys.add(source_key)
            if len(merged) >= safe_max_terms:
                return merged
    return merged


def _resolve_deepseek_model(model: str, settings: Settings) -> str:
    if model.startswith("deepseek/"):
        return normalize_text(model.split("/", 1)[1]) or settings.deepseek_model
    return model


def _build_model_attempts(model: str, settings: Settings) -> list[TermExtractionModelAttempt]:
    selected_model = normalize_text(model) or TERM_EXTRACTION_MODEL
    attempts: list[TermExtractionModelAttempt] = []

    # 前端的 deepseek/deepseek-chat 是 OpenRouter 模型 ID；直连 DeepSeek 时要使用 deepseek-chat。
    if selected_model.startswith("deepseek/"):
        if settings.deepseek_api_key:
            attempts.append(TermExtractionModelAttempt(
                provider="deepseek",
                model=_resolve_deepseek_model(selected_model, settings),
            ))
        if settings.openrouter_api_key:
            attempts.append(TermExtractionModelAttempt(
                provider="openrouter",
                model=selected_model,
            ))
        return attempts or [TermExtractionModelAttempt(provider="deepseek", model=_resolve_deepseek_model(selected_model, settings))]

    if selected_model == settings.deepseek_model and settings.deepseek_api_key:
        attempts.append(TermExtractionModelAttempt(provider="deepseek", model=selected_model))
    if settings.openrouter_api_key:
        attempts.append(TermExtractionModelAttempt(provider="openrouter", model=selected_model))

    return attempts or [TermExtractionModelAttempt(provider="openrouter", model=selected_model)]


async def _request_term_extraction_completion(
    messages: list[dict[str, str]],
    attempts: list[TermExtractionModelAttempt],
    *,
    settings: Settings,
):
    last_error: Exception | None = None
    for attempt in attempts:
        try:
            return await request_chat_completion(
                messages=messages,
                provider=attempt.provider,
                model_override=attempt.model,
                response_format={"type": "json_object"},
                temperature=0.0,
                settings=settings,
                allow_fallback=False,
            )
        except (LLMConfigurationError, LLMRequestError) as exc:
            last_error = exc
            logger.warning(
                "term extraction model attempt failed provider=%s model=%s error=%s",
                attempt.provider,
                attempt.model,
                exc,
            )

    if last_error is not None:
        raise last_error
    raise LLMRequestError("术语提取 LLM 请求失败。")


async def extract_terms_from_segments(
    segments: Iterable[object],
    source_language: str,
    target_language: str,
    *,
    max_terms: int = 150,
    model: str = TERM_EXTRACTION_MODEL,
    extraction_prompt: str = "",
    settings: Settings | None = None,
) -> TermExtractionResult:
    config = settings or get_settings()
    source_texts = _collect_source_texts(segments)
    if not source_texts:
        raise TermExtractionError("当前文件没有可用于术语提取的源文句段。")

    safe_max_terms = min(max(int(max_terms or 150), 1), 300)
    chunks = _chunk_source_texts(source_texts)
    extracted_terms: list[ExtractedTerm] = []
    seen_keys: set[str] = set()
    selected_model = normalize_text(model) or TERM_EXTRACTION_MODEL
    attempts = _build_model_attempts(selected_model, config)
    provider = attempts[0].provider
    resolved_model = selected_model

    for chunk in chunks:
        remaining_limit = safe_max_terms - len(extracted_terms)
        if remaining_limit <= 0:
            break

        messages = _build_term_extraction_messages(
            chunk_texts=chunk,
            source_language=source_language,
            target_language=target_language,
            max_terms=min(TERM_EXTRACTION_CHUNK_MAX_TERMS, remaining_limit),
            extraction_prompt=extraction_prompt,
        )
        try:
            completion = await _request_term_extraction_completion(
                messages=messages,
                attempts=attempts,
                settings=config,
            )
        except (LLMConfigurationError, LLMRequestError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise TermExtractionError(str(exc)) from exc

        provider = completion.provider
        resolved_model = completion.model
        raw_terms = _parse_term_json(completion.content)
        extracted_terms.extend(_filter_terms(
            raw_terms=raw_terms,
            source_texts=source_texts,
            existing_keys=seen_keys,
            remaining_limit=remaining_limit,
        ))

    return TermExtractionResult(
        terms=extracted_terms,
        provider=provider,
        model=resolved_model,
    )
