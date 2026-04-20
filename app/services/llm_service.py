from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import AsyncIterator, Literal
from urllib import error as urllib_error
from urllib import request as urllib_request

from app.config import Settings, get_settings
from app.services.normalizer import normalize_text

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover - 允许在无 httpx 环境下走标准库后备路径
    httpx = None


logger = logging.getLogger(__name__)

LLMProvider = Literal["auto", "deepseek", "openrouter"]
LLMScope = Literal["fuzzy_only", "none_only", "all", "all_with_exact"]


class LLMServiceError(RuntimeError):
    """LLM 服务异常基类。"""


class LLMConfigurationError(LLMServiceError):
    """LLM 配置异常。"""


class LLMRequestError(LLMServiceError):
    """LLM 请求异常。"""


class LLMResponseValidationError(LLMServiceError):
    """LLM 返回内容不符合约束。"""


@dataclass(frozen=True)
class LLMTranslationTask:
    sentence_id: str
    status: str
    source_text: str
    block_type: str = "paragraph"
    matched_source_text: str | None = None
    tm_target_text: str | None = None


@dataclass(frozen=True)
class LLMTranslationResult:
    sentence_id: str
    translated_text: str
    status: str
    provider: str
    model: str


@dataclass(frozen=True)
class LLMTranslationFailure:
    sentence_id: str
    status: str
    error_message: str


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api_key: str
    base_url: str
    model: str


NUMERIC_LIKE_FRAGMENT_RE = re.compile(r"^[0-9\s,.\-+/%()（）$€¥￥£:：]+$")
STRICT_PRESERVE_SYMBOLS = frozenset({"□", "☐", "☑", "", "", "✓", "✔", "✗", "✘"})


def validate_provider_choice(
    provider: LLMProvider = "auto",
    settings: Settings | None = None,
) -> list[ProviderConfig]:
    config = settings or get_settings()
    available_providers: dict[str, ProviderConfig] = {}

    if config.deepseek_api_key:
        available_providers["deepseek"] = ProviderConfig(
            name="deepseek",
            api_key=config.deepseek_api_key,
            base_url=config.deepseek_base_url.rstrip("/"),
            model=config.deepseek_model,
        )

    if config.openrouter_api_key:
        available_providers["openrouter"] = ProviderConfig(
            name="openrouter",
            api_key=config.openrouter_api_key,
            base_url=config.openrouter_base_url.rstrip("/"),
            model=config.openrouter_model,
        )

    if provider == "auto":
        ordered_names = ["deepseek", "openrouter"]
    elif provider == "deepseek":
        if "deepseek" not in available_providers:
            raise LLMConfigurationError("未配置 DEEPSEEK_API_KEY，无法使用 DeepSeek。")
        ordered_names = ["deepseek", "openrouter"]
    else:
        if "openrouter" not in available_providers:
            raise LLMConfigurationError("未配置 OPENROUTER_API_KEY，无法使用 OpenRouter。")
        ordered_names = ["openrouter", "deepseek"]

    providers = [available_providers[name] for name in ordered_names if name in available_providers]

    if providers:
        return providers

    raise LLMConfigurationError("未配置可用的 LLM API key。")

async def iter_batch_translate(
    tasks: list[LLMTranslationTask],
    provider: LLMProvider = "auto",
    settings: Settings | None = None,
) -> AsyncIterator[LLMTranslationResult | LLMTranslationFailure]:
    config = settings or get_settings()
    providers = validate_provider_choice(provider=provider, settings=config)

    if not tasks:
        return

    max_concurrency = max(int(config.llm_max_concurrency), 1)
    semaphore = asyncio.Semaphore(max_concurrency)

    async def run_with_clients(clients: dict[str, "httpx.AsyncClient" | None]):
        async def run_single(task: LLMTranslationTask):
            async with semaphore:
                try:
                    return await _translate_single_task(
                        task=task,
                        providers=providers,
                        clients=clients,
                        settings=config,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "llm translate failed sentence_id=%s provider=%s error=%s",
                        task.sentence_id,
                        provider,
                        exc,
                    )
                    return LLMTranslationFailure(
                        sentence_id=task.sentence_id,
                        status=task.status,
                        error_message=str(exc),
                    )

        futures = [asyncio.create_task(run_single(task)) for task in tasks]
        try:
            for future in asyncio.as_completed(futures):
                yield await future
        finally:
            for future in futures:
                if not future.done():
                    future.cancel()

    if httpx is None:
        async for item in run_with_clients({provider_config.name: None for provider_config in providers}):
            yield item
        return

    async with AsyncExitStack() as stack:
        clients = {
            item.name: await stack.enter_async_context(
                httpx.AsyncClient(
                    base_url=item.base_url,
                    timeout=httpx.Timeout(config.llm_timeout_seconds),
                )
            )
            for item in providers
        }
        async for item in run_with_clients(clients):
            yield item

async def _translate_single_task(
    task: LLMTranslationTask,
    providers: list[ProviderConfig],
    clients: dict[str, "httpx.AsyncClient" | None],
    settings: Settings,
) -> LLMTranslationResult:
    last_error: Exception | None = None
    retry_attempts = max(int(getattr(settings, "llm_retry_attempts_per_provider", 2)), 1)

    for provider_index, provider in enumerate(providers):
        for attempt_index in range(retry_attempts):
            strict_retry = attempt_index > 0 or provider_index > 0
            current_temperature = 0.0 if strict_retry else settings.llm_temperature
            messages = _build_messages(
                task,
                strict_retry=strict_retry,
                retry_reason=str(last_error) if strict_retry and last_error else None,
            )
            try:
                translated_text = await _request_translation(
                    client=clients.get(provider.name),
                    provider=provider,
                    messages=messages,
                    temperature=current_temperature,
                    timeout_seconds=settings.llm_timeout_seconds,
                )
                _validate_translation_output(task, translated_text)
                return LLMTranslationResult(
                    sentence_id=task.sentence_id,
                    translated_text=translated_text,
                    status=task.status,
                    provider=provider.name,
                    model=provider.model,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "llm provider failed sentence_id=%s provider=%s attempt=%s error=%s",
                    task.sentence_id,
                    provider.name,
                    attempt_index + 1,
                    exc,
                )

    raise LLMRequestError(str(last_error) if last_error else "LLM 请求失败。")


async def _request_translation(
    client: "httpx.AsyncClient" | None,
    provider: ProviderConfig,
    messages: list[dict[str, str]],
    temperature: float,
    timeout_seconds: float,
) -> str:
    payload = {
        "model": provider.model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    if provider.name == "openrouter":
        headers["HTTP-Referer"] = "http://localhost"
        headers["X-Title"] = "AI Translation System"

    if client is None:
        return await asyncio.to_thread(
            _request_translation_with_urllib,
            provider,
            headers,
            payload,
            timeout_seconds,
        )

    response = await client.post("/chat/completions", headers=headers, json=payload)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise LLMRequestError(f"{provider.name} 返回错误：{exc.response.text}") from exc

    return _extract_translation_from_payload(response.json(), provider.name)


def _build_messages(
    task: LLMTranslationTask,
    strict_retry: bool = False,
    retry_reason: str | None = None,
) -> list[dict[str, str]]:
    system_prompt = (
        "你是专业的中英翻译专家。"
        "请保持术语一致，保留数字、单位、专有名词和格式。"
        "如果原文包含复选框、勾选框、项目符号或特殊符号（如 □、☐、☑、、✓ 等），必须按原样保留这些符号本身及其顺序，不得新增、删除、替换，也不得自行改变其选中状态。"
        "只输出最终英文译文，不要解释，不要引号，不要项目符号。"
    )
    retry_instruction = ""
    if strict_retry:
        retry_instruction = (
            "\n这是一次纠错重试。"
            "请更严格地保留原文中的数字、格式、复选框和特殊符号，禁止擅自补充勾选状态或改写符号样式。"
        )
        if retry_reason:
            retry_instruction += f"\n上一次结果的问题：{retry_reason}"

    if task.status == "fuzzy":
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "请基于翻译记忆库参考译文，修正当前句子的英文翻译。\n"
                    "这不是从零重译，而是以翻译记忆库译文为底稿进行定向修改。\n\n"
                    f"当前原文：{task.source_text}\n"
                    f"翻译记忆库匹配到的原文：{task.matched_source_text or '无'}\n"
                    f"翻译记忆库的译文：{task.tm_target_text or '无'}\n"
                    f"两个原文之间的差异：{_describe_diff(task.source_text, task.matched_source_text or '')}\n\n"
                    "请严格遵守以下要求：\n"
                    "1. 把“翻译记忆库的译文”当作基础译文，优先保留其中仍然适用的表达、术语、句式和语气。\n"
                    "2. 重点根据两个原文之间的差异，对基础译文做对应修改，而不是忽略基础译文直接整句重译。\n"
                    "3. 必须让结果完整表达“当前原文”的全部含义；如果当前原文比记忆库原文多了信息，就补全；如果少了信息，就删去不再适用的内容；如果有替换，就准确改写对应部分。\n"
                    "4. 保留数字、单位、标点风格、专有名词、复选框/勾选框/特殊符号和已存在的专业术语一致性，不得擅自增删或替换符号。\n"
                    "5. 输出必须是最终可直接使用的完整英文译文，只输出译文本身。\n\n"
                    f"请输出基于记忆库译文修订后的最终英文译文。{retry_instruction}"
                ),
            },
        ]

    if _is_numeric_like_fragment(task.source_text):
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "请处理以下内容并输出英文目标文本。\n"
                    "如果内容只是数字、金额、百分比、日期、编号或符号，并且在英文中通常可直接沿用，请原样输出。\n"
                    "如果内容中包含可翻译文字，只翻译文字部分，并尽量保留数字和原有排版格式。\n"
                    "除非原文明确体现需要转换的中文数字单位或本地格式，否则不要擅自改动千分位、小数点、货币符号、编号格式或特殊符号。\n\n"
                    f"原文：{task.source_text}\n\n"
                    f"只输出最终结果。{retry_instruction}"
                ),
            },
        ]

    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "请将以下内容作为独立片段翻译为英文。"
                "不要补充未提供的上下文，也不要参考前后句。"
                "\n"
                f"原文：{task.source_text}\n\n"
                f"请严格保留原文中的数字、复选框、勾选框和特殊符号，不得擅自新增或改变其状态。\n\n只输出英文译文。{retry_instruction}"
            ),
        },
    ]


def _is_numeric_like_fragment(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    return bool(NUMERIC_LIKE_FRAGMENT_RE.fullmatch(normalized))

def _extract_preserved_symbol_sequence(text: str) -> list[str]:
    return [char for char in text if char in STRICT_PRESERVE_SYMBOLS]


def _validate_translation_output(task: LLMTranslationTask, translated_text: str) -> None:
    normalized_output = normalize_text(translated_text)
    if not normalized_output:
        raise LLMResponseValidationError("LLM 返回空译文。")

    if _is_numeric_like_fragment(task.source_text):
        if normalized_output != normalize_text(task.source_text):
            raise LLMResponseValidationError("数字或符号片段未按原文保留。")

    source_symbols = _extract_preserved_symbol_sequence(task.source_text)
    if source_symbols:
        output_symbols = _extract_preserved_symbol_sequence(translated_text)
        if output_symbols != source_symbols:
            raise LLMResponseValidationError("复选框或特殊符号未按原文原样保留。")


def _describe_diff(source_text: str, matched_source_text: str) -> str:
    if not matched_source_text:
        return "无可用参考原文。"

    pieces: list[str] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(
        None,
        matched_source_text,
        source_text,
    ).get_opcodes():
        if tag == "equal":
            continue

        old_text = matched_source_text[i1:i2].strip()
        new_text = source_text[j1:j2].strip()

        if tag == "replace" and old_text and new_text:
            pieces.append(f"将“{old_text}”替换为“{new_text}”")
        elif tag == "delete" and old_text:
            pieces.append(f"删除“{old_text}”")
        elif tag == "insert" and new_text:
            pieces.append(f"新增“{new_text}”")

        if len(pieces) >= 6:
            break

    if not pieces:
        return "表述高度接近，请按当前原文微调。"

    return "；".join(pieces)


def _normalize_response_content(content) -> str:
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
            elif isinstance(item, dict) and item.get("text"):
                text_parts.append(str(item["text"]))
        content = "".join(text_parts)

    text = str(content or "").strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    return text.strip().strip("\"'“”")


def _request_translation_with_urllib(
    provider: ProviderConfig,
    headers: dict[str, str],
    payload: dict,
    timeout_seconds: float,
) -> str:
    request = urllib_request.Request(
        url=f"{provider.base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise LLMRequestError(f"{provider.name} 返回错误：{body}") from exc
    except urllib_error.URLError as exc:
        raise LLMRequestError(f"{provider.name} 请求失败：{exc.reason}") from exc

    try:
        parsed_payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise LLMRequestError(f"{provider.name} 返回了无法解析的响应。") from exc

    return _extract_translation_from_payload(parsed_payload, provider.name)


def _extract_translation_from_payload(payload: dict, provider_name: str) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise LLMRequestError(f"{provider_name} 未返回可用结果。")

    message = choices[0].get("message") or {}
    content = message.get("content")
    translated_text = _normalize_response_content(content)
    if not translated_text:
        raise LLMRequestError(f"{provider_name} 返回空译文。")

    return translated_text
