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
from app.services.language_pairs import LANGUAGE_LABELS
from app.services.normalizer import normalize_text

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover - 允许在无 httpx 环境下走标准库后备路径
    httpx = None


logger = logging.getLogger(__name__)

LLMProvider = Literal["auto", "deepseek", "openrouter"]
LLMScope = Literal["fuzzy_only", "none_only", "empty_target_only", "all", "all_with_exact"]


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
    source_language: str | None = None
    target_language: str | None = None
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
class LLMChatCompletionResult:
    content: str
    provider: str
    model: str


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api_key: str
    base_url: str
    model: str


BATCH_MAX_ITEMS = 15
BATCH_MAX_SOURCE_CHARS = 3000
BATCH_ITEM_RE = re.compile(r"^\[(\d+)\]\s*", re.MULTILINE)


@dataclass
class TaskGroup:
    tasks: list[LLMTranslationTask]
    group_type: str  # "normal" | "fuzzy" | "numeric"


NUMERIC_LIKE_FRAGMENT_RE = re.compile(r"^[0-9\s,.\-+/%()（）$€¥￥£:：]+$")
MATH_PLACEHOLDER_RE = re.compile(r"⟦MATH_\d+⟧")
SYMBOL_VALIDATION_ERROR_MESSAGE = "复选框或特殊符号未按原文原样保留。"
STRICT_PRESERVE_SYMBOLS = frozenset(
    {
        "□",
        "☐",
        "☑",
        "☒",
        "",
        "",
        "✓",
        "✔",
        "✗",
        "✘",
        "•",
        "◦",
        "·",
        "●",
        "○",
        "■",
        "▪",
        "▫",
        "◆",
        "◇",
        "▶",
        "▷",
        "→",
        "←",
        "↑",
        "↓",
        "★",
        "☆",
    }
)
STRICT_PRESERVE_SYMBOL_CLASS = "".join(re.escape(char) for char in sorted(STRICT_PRESERVE_SYMBOLS))
LINE_PREFIX_SYMBOL_RE = re.compile(rf"^(\s*(?:[{STRICT_PRESERVE_SYMBOL_CLASS}]\s*)+)(.*)$")
UNSAFE_LOCALIZED_LIST_PREFIX_SYMBOLS = frozenset({"-", "–", "—", "*", "・", "∙", "⁃", "‣", "◾", "◽"})


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
    translation_guidelines: str = "",
    settings: Settings | None = None,
) -> AsyncIterator[LLMTranslationResult | LLMTranslationFailure]:
    config = settings or get_settings()
    providers = validate_provider_choice(provider=provider, settings=config)

    if not tasks:
        return

    max_concurrency = max(int(config.llm_max_concurrency), 1)
    semaphore = asyncio.Semaphore(max_concurrency)
    use_batch = bool(translation_guidelines)

    async def _run_single_mode(clients: dict[str, "httpx.AsyncClient" | None]):
        async def run_single(task: LLMTranslationTask):
            async with semaphore:
                try:
                    return await _translate_single_task(
                        task=task,
                        providers=providers,
                        clients=clients,
                        settings=config,
                        translation_guidelines=translation_guidelines,
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

    async def _run_batch_mode(clients: dict[str, "httpx.AsyncClient" | None]):
        groups = _group_tasks_for_batch(tasks)
        logger.info(
            "batch translate: %d tasks -> %d groups (guidelines length=%d)",
            len(tasks),
            len(groups),
            len(translation_guidelines),
        )

        async def run_group(group: TaskGroup):
            async with semaphore:
                return await _translate_batch_group(
                    group=group,
                    providers=providers,
                    clients=clients,
                    settings=config,
                    translation_guidelines=translation_guidelines,
                )

        futures = [asyncio.create_task(run_group(g)) for g in groups]
        try:
            for future in asyncio.as_completed(futures):
                group_results = await future
                for item in group_results:
                    yield item
        finally:
            for future in futures:
                if not future.done():
                    future.cancel()

    run_fn = _run_batch_mode if use_batch else _run_single_mode

    if httpx is None:
        async for item in run_fn({pc.name: None for pc in providers}):
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
        async for item in run_fn(clients):
            yield item

async def _translate_single_task(
    task: LLMTranslationTask,
    providers: list[ProviderConfig],
    clients: dict[str, "httpx.AsyncClient" | None],
    settings: Settings,
    translation_guidelines: str = "",
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
                translation_guidelines=translation_guidelines,
            )
            try:
                translated_text = await _request_translation(
                    client=clients.get(provider.name),
                    provider=provider,
                    messages=messages,
                    temperature=current_temperature,
                    timeout_seconds=settings.llm_timeout_seconds,
                )
                translated_text = _validate_or_repair_translation_output(task, translated_text)
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
    model_override: str | None = None,
    response_format: dict | None = None,
) -> str:
    payload = {
        "model": model_override or provider.model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if response_format:
        payload["response_format"] = response_format
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    if provider.name == "openrouter":
        headers["HTTP-Referer"] = "AI Translation System"
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


async def request_chat_completion(
    messages: list[dict[str, str]],
    provider: LLMProvider = "auto",
    *,
    model_override: str | None = None,
    response_format: dict | None = None,
    temperature: float | None = None,
    settings: Settings | None = None,
    allow_fallback: bool = True,
) -> LLMChatCompletionResult:
    config = settings or get_settings()
    providers = validate_provider_choice(provider=provider, settings=config)
    if not allow_fallback:
        providers = providers[:1]

    last_error: Exception | None = None
    request_temperature = config.llm_temperature if temperature is None else temperature
    retry_attempts = max(int(getattr(config, "llm_retry_attempts_per_provider", 2)), 1)

    async def _run_with_clients(clients: dict[str, "httpx.AsyncClient" | None]) -> LLMChatCompletionResult:
        nonlocal last_error
        for item in providers:
            for attempt_index in range(retry_attempts):
                try:
                    content = await _request_translation(
                        client=clients.get(item.name),
                        provider=item,
                        messages=messages,
                        temperature=request_temperature,
                        timeout_seconds=config.llm_timeout_seconds,
                        model_override=model_override,
                        response_format=response_format,
                    )
                    return LLMChatCompletionResult(
                        content=content,
                        provider=item.name,
                        model=model_override or item.model,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.warning(
                        "llm chat completion failed provider=%s model=%s attempt=%s error=%s",
                        item.name,
                        model_override or item.model,
                        attempt_index + 1,
                        exc,
                    )
                    if attempt_index + 1 < retry_attempts:
                        await asyncio.sleep(min(0.5 * (attempt_index + 1), 2.0))
            if not allow_fallback:
                break
        raise LLMRequestError(str(last_error) if last_error else "LLM 请求失败。")

    if httpx is None:
        return await _run_with_clients({item.name: None for item in providers})

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
        return await _run_with_clients(clients)


def _build_messages(
    task: LLMTranslationTask,
    strict_retry: bool = False,
    retry_reason: str | None = None,
    translation_guidelines: str = "",
) -> list[dict[str, str]]:
    source_label = _format_language_for_prompt(task.source_language, "源语言")
    target_label = _format_language_for_prompt(task.target_language, "目标语言")
    language_pair = f"{source_label} -> {target_label}"
    system_prompt = (
        f"你是专业的文档翻译专家，当前任务语言对为：{language_pair}。"
        f"请将内容从{source_label}翻译为{target_label}。"
        "请保持术语一致，保留数字、单位、专有名词和格式。"
        "如果原文包含复选框、勾选框、项目符号、箭头、对错标记或特殊符号（如 □、☐、☑、☒、、✓、✗、•、○、●、→ 等），必须按原样保留这些符号本身及其顺序，不得新增、删除、替换，也不得自行改变其选中状态。"
        "不要把这些文档符号翻译成文字，也不要替换成目标语言环境中的近似符号；只本地化可翻译文字和目标语言需要的普通标点。"
        "序号、编号、变量、占位符、路径、URL、邮箱、代码片段、单位和表格结构标记应尽量保持原格式。"
        "如果原文包含形如 ⟦MATH_1⟧ 的占位符，这代表一个数学公式，必须原样保留该占位符本身。"
        "占位符的顺序和数量必须与原文一致，不得翻译、改写、删除、重排，也不得增删空格、括号或引号。"
        f"只输出最终{target_label}译文，不要解释，不要引号，不要额外添加项目符号。"
    )
    if translation_guidelines:
        system_prompt += (
            "\n\n以下是本项目的翻译细则，请在翻译时严格遵守：\n"
            + translation_guidelines
        )
    retry_instruction = ""
    if strict_retry:
        retry_instruction = (
            "\n这是一次纠错重试。"
            "请更严格地保留原文中的数字、格式、复选框、项目符号、箭头和特殊符号，禁止擅自补充勾选状态或改写符号样式。"
            "请同时原样保留所有数学公式占位符 ⟦MATH_n⟧。"
        )
        if retry_reason:
            retry_instruction += f"\n上一次结果的问题：{retry_reason}"

    if task.status == "fuzzy":
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"请基于翻译记忆库参考译文，修正当前句子的{target_label}翻译。\n"
                    "这不是从零重译，而是以翻译记忆库译文为底稿进行定向修改。\n\n"
                    f"当前原文：{task.source_text}\n"
                    f"翻译记忆库匹配到的原文：{task.matched_source_text or '无'}\n"
                    f"翻译记忆库的译文：{task.tm_target_text or '无'}\n"
                    f"两个原文之间的差异：{_describe_diff(task.source_text, task.matched_source_text or '')}\n\n"
                    "请严格遵守以下要求：\n"
                    "1. 把“翻译记忆库的译文”当作基础译文，优先保留其中仍然适用的表达、术语、句式和语气。\n"
                    "2. 重点根据两个原文之间的差异，对基础译文做对应修改，而不是忽略基础译文直接整句重译。\n"
                    "3. 必须让结果完整表达“当前原文”的全部含义；如果当前原文比记忆库原文多了信息，就补全；如果少了信息，就删去不再适用的内容；如果有替换，就准确改写对应部分。\n"
                    "4. 保留数字、单位、标点风格、专有名词、复选框/勾选框/项目符号/箭头/特殊符号和已存在的专业术语一致性，不得擅自增删或替换符号。\n"
                    f"5. 输出必须是最终可直接使用的完整{target_label}译文，只输出译文本身。\n\n"
                    f"请输出基于记忆库译文修订后的最终{target_label}译文。{retry_instruction}"
                ),
            },
        ]

    if _is_numeric_like_fragment(task.source_text):
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"请处理以下内容并输出{target_label}目标文本。\n"
                    f"如果内容只是数字、金额、百分比、日期、编号或符号，并且在{target_label}中通常可直接沿用，请原样输出。\n"
                    "如果内容中包含可翻译文字，只翻译文字部分，并尽量保留数字和原有排版格式。\n"
                    "除非原文明确体现需要按目标语言转换的数字单位或本地格式，否则不要擅自改动千分位、小数点、货币符号、编号格式或特殊符号。\n\n"
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
                f"请将以下内容作为独立片段从{source_label}翻译为{target_label}。"
                "不要补充未提供的上下文，也不要参考前后句。"
                "\n"
                f"原文：{task.source_text}\n\n"
                f"请严格保留原文中的数字、复选框、勾选框、项目符号、箭头和特殊符号，不得擅自新增、替换或改变其状态。\n\n只输出{target_label}译文。{retry_instruction}"
            ),
        },
    ]


def _format_language_for_prompt(language_code: str | None, fallback: str) -> str:
    if not language_code:
        return fallback
    label = LANGUAGE_LABELS.get(language_code, language_code)
    return f"{label}（{language_code}）"


def _is_numeric_like_fragment(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    return bool(NUMERIC_LIKE_FRAGMENT_RE.fullmatch(normalized))

def _extract_preserved_symbol_sequence(text: str) -> list[str]:
    return [char for char in text if char in STRICT_PRESERVE_SYMBOLS]


def _extract_math_placeholder_sequence(text: str) -> list[str]:
    return MATH_PLACEHOLDER_RE.findall(text)


def _validate_or_repair_translation_output(task: LLMTranslationTask, translated_text: str) -> str:
    try:
        _validate_translation_output(task, translated_text)
        return translated_text
    except LLMResponseValidationError as exc:
        if str(exc) != SYMBOL_VALIDATION_ERROR_MESSAGE:
            raise

        repaired_text = _repair_preserved_symbols(task.source_text, translated_text)
        if repaired_text is None or repaired_text == translated_text:
            raise

        _validate_translation_output(task, repaired_text)
        logger.info("llm output symbols repaired sentence_id=%s", task.sentence_id)
        return repaired_text


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
            raise LLMResponseValidationError(SYMBOL_VALIDATION_ERROR_MESSAGE)

    source_math_placeholders = _extract_math_placeholder_sequence(task.source_text)
    if source_math_placeholders:
        output_math_placeholders = _extract_math_placeholder_sequence(translated_text)
        if output_math_placeholders != source_math_placeholders:
            raise LLMResponseValidationError("数学公式占位符未按原文原样保留。")


def _repair_preserved_symbols(source_text: str, translated_text: str) -> str | None:
    source_symbols = _extract_preserved_symbol_sequence(source_text)
    if not source_symbols:
        return None

    output_symbols = _extract_preserved_symbol_sequence(translated_text)
    if len(output_symbols) == len(source_symbols):
        source_iter = iter(source_symbols)
        return "".join(
            next(source_iter) if char in STRICT_PRESERVE_SYMBOLS else char
            for char in translated_text
        )

    return _repair_line_prefix_symbols(source_text, translated_text)


def _repair_line_prefix_symbols(source_text: str, translated_text: str) -> str | None:
    source_lines = source_text.splitlines()
    translated_lines = translated_text.splitlines(keepends=True)
    if len(source_lines) < 2 or len(source_lines) != len(translated_lines):
        return None

    prefixes: list[str] = []
    for source_line in source_lines:
        if not source_line.strip():
            return None
        match = LINE_PREFIX_SYMBOL_RE.match(source_line)
        if not match or not _extract_preserved_symbol_sequence(match.group(1)):
            return None
        prefixes.append(match.group(1))

    repaired_lines: list[str] = []
    for prefix, translated_line in zip(prefixes, translated_lines):
        body, line_ending = _split_line_ending(translated_line)
        stripped_body = body.lstrip()
        if stripped_body and stripped_body[0] in UNSAFE_LOCALIZED_LIST_PREFIX_SYMBOLS:
            return None

        match = LINE_PREFIX_SYMBOL_RE.match(body)
        translated_body = match.group(2).lstrip() if match else stripped_body
        if not translated_body:
            return None
        repaired_lines.append(prefix + translated_body + line_ending)

    return "".join(repaired_lines)


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1]
    return line, ""


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


# ---------------------------------------------------------------------------
# Stage 2: Grouped batch translation
# ---------------------------------------------------------------------------


def _group_tasks_for_batch(
    tasks: list[LLMTranslationTask],
) -> list[TaskGroup]:
    """Split tasks into groups by type, respecting size limits."""
    buckets: dict[str, list[LLMTranslationTask]] = {
        "fuzzy": [],
        "numeric": [],
        "normal": [],
    }
    for task in tasks:
        if task.status == "fuzzy":
            buckets["fuzzy"].append(task)
        elif _is_numeric_like_fragment(task.source_text):
            buckets["numeric"].append(task)
        else:
            buckets["normal"].append(task)

    groups: list[TaskGroup] = []
    for group_type, bucket in buckets.items():
        current: list[LLMTranslationTask] = []
        current_chars = 0
        for task in bucket:
            task_chars = len(task.source_text)
            if current and (
                len(current) >= BATCH_MAX_ITEMS
                or current_chars + task_chars > BATCH_MAX_SOURCE_CHARS
            ):
                groups.append(TaskGroup(tasks=current, group_type=group_type))
                current = []
                current_chars = 0
            current.append(task)
            current_chars += task_chars
        if current:
            groups.append(TaskGroup(tasks=current, group_type=group_type))

    return groups


def _build_batch_messages(
    group: TaskGroup,
    translation_guidelines: str = "",
    strict_retry: bool = False,
    retry_reason: str | None = None,
) -> list[dict[str, str]]:
    """Build a single prompt for multiple source segments."""
    first = group.tasks[0]
    source_label = _format_language_for_prompt(first.source_language, "源语言")
    target_label = _format_language_for_prompt(first.target_language, "目标语言")
    language_pair = f"{source_label} -> {target_label}"

    system_prompt = (
        f"你是专业的文档翻译专家，当前任务语言对为：{language_pair}。"
        f"请将内容从{source_label}翻译为{target_label}。"
        "请保持术语一致，保留数字、单位、专有名词和格式。"
        "如果原文包含复选框、勾选框、项目符号、箭头、对错标记或特殊符号（如 □、☐、☑、☒、✓、✗、•、○、●、→ 等），必须按原样保留这些符号本身及其顺序，不得新增、删除、替换，也不得自行改变其选中状态。"
        "不要把这些文档符号翻译成文字，也不要替换成目标语言环境中的近似符号；只本地化可翻译文字和目标语言需要的普通标点。"
        "序号、编号、变量、占位符、路径、URL、邮箱、代码片段、单位和表格结构标记应尽量保持原格式。"
        "如果原文包含形如 ⟦MATH_1⟧ 的占位符，这代表一个数学公式，必须原样保留该占位符本身。"
        "占位符的顺序和数量必须与原文一致，不得翻译、改写、删除、重排，也不得增删空格、括号或引号。"
        f"只输出最终{target_label}译文，不要解释，不要引号，不要额外添加项目符号。"
    )
    if translation_guidelines:
        system_prompt += (
            "\n\n以下是本项目的翻译细则，请在翻译时严格遵守：\n"
            + translation_guidelines
        )

    retry_instruction = ""
    if strict_retry:
        retry_instruction = (
            "\n这是一次纠错重试。请更严格地遵守所有保留规则。"
        )
        if retry_reason:
            retry_instruction += f"\n上一次结果的问题：{retry_reason}"

    if group.group_type == "fuzzy":
        lines: list[str] = []
        for idx, task in enumerate(group.tasks, 1):
            diff = _describe_diff(task.source_text, task.matched_source_text or "")
            lines.append(
                f"[{idx}] 当前原文：{task.source_text}\n"
                f"    记忆库原文：{task.matched_source_text or '无'}\n"
                f"    记忆库译文：{task.tm_target_text or '无'}\n"
                f"    差异：{diff}"
            )
        user_content = (
            f"请基于翻译记忆库参考译文，逐条修正以下句子的{target_label}翻译。\n"
            "以翻译记忆库译文为底稿进行定向修改，而不是整句重译。\n\n"
            + "\n\n".join(lines)
            + f"\n\n请严格按以下格式逐条输出修订后的最终{target_label}译文，每条只输出译文本身：\n"
            + "\n".join(f"[{i}] 译文" for i in range(1, len(group.tasks) + 1))
            + retry_instruction
        )
    elif group.group_type == "numeric":
        lines = [f"[{idx}] {task.source_text}" for idx, task in enumerate(group.tasks, 1)]
        user_content = (
            f"请处理以下内容并输出{target_label}目标文本。\n"
            f"如果内容只是数字、金额、百分比、日期、编号或符号，并且在{target_label}中通常可直接沿用，请原样输出。\n"
            "如果内容中包含可翻译文字，只翻译文字部分，并尽量保留数字和原有排版格式。\n\n"
            + "\n".join(lines)
            + f"\n\n请严格按以下格式逐条输出结果：\n"
            + "\n".join(f"[{i}] 结果" for i in range(1, len(group.tasks) + 1))
            + retry_instruction
        )
    else:
        lines = [f"[{idx}] {task.source_text}" for idx, task in enumerate(group.tasks, 1)]
        user_content = (
            f"请将以下编号片段从{source_label}翻译为{target_label}。\n"
            "逐条独立翻译，不要补充未提供的上下文。\n"
            "请严格保留原文中的数字、复选框、勾选框、项目符号、箭头和特殊符号。\n\n"
            + "\n".join(lines)
            + f"\n\n请严格按以下格式逐条输出{target_label}译文，每条只输出译文本身：\n"
            + "\n".join(f"[{i}] 译文" for i in range(1, len(group.tasks) + 1))
            + retry_instruction
        )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def _parse_batch_response(
    raw_text: str,
    expected_count: int,
) -> list[str] | None:
    """Parse numbered batch response. Returns list of translations or None on failure."""
    text = _normalize_response_content(raw_text) if raw_text else ""
    if not text:
        return None

    items: dict[int, str] = {}
    matches = list(BATCH_ITEM_RE.finditer(text))

    if not matches:
        if expected_count == 1:
            return [text.strip()]
        return None

    for i, match in enumerate(matches):
        idx = int(match.group(1))
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        items[idx] = text[start:end].strip()

    result: list[str] = []
    for i in range(1, expected_count + 1):
        if i not in items:
            return None
        val = items[i].strip().strip("\"'""")
        if not val:
            return None
        result.append(val)

    return result


async def _translate_batch_group(
    group: TaskGroup,
    providers: list[ProviderConfig],
    clients: dict[str, "httpx.AsyncClient" | None],
    settings: Settings,
    translation_guidelines: str = "",
) -> list[LLMTranslationResult | LLMTranslationFailure]:
    """Translate a group of tasks in a single LLM call with fallback to per-task."""
    last_error: Exception | None = None
    retry_attempts = max(int(getattr(settings, "llm_retry_attempts_per_provider", 2)), 1)

    for provider_index, provider in enumerate(providers):
        for attempt_index in range(retry_attempts):
            strict_retry = attempt_index > 0 or provider_index > 0
            current_temperature = 0.0 if strict_retry else settings.llm_temperature
            messages = _build_batch_messages(
                group,
                translation_guidelines=translation_guidelines,
                strict_retry=strict_retry,
                retry_reason=str(last_error) if strict_retry and last_error else None,
            )
            try:
                raw_text = await _request_translation(
                    client=clients.get(provider.name),
                    provider=provider,
                    messages=messages,
                    temperature=current_temperature,
                    timeout_seconds=settings.llm_timeout_seconds,
                )
                translations = _parse_batch_response(raw_text, len(group.tasks))
                if translations is None:
                    raise LLMResponseValidationError(
                        f"批量翻译结果解析失败，期望 {len(group.tasks)} 条，无法正确提取。"
                    )

                results: list[LLMTranslationResult | LLMTranslationFailure] = []
                all_valid = True
                for task, translated_text in zip(group.tasks, translations):
                    try:
                        translated_text = _validate_or_repair_translation_output(task, translated_text)
                        results.append(LLMTranslationResult(
                            sentence_id=task.sentence_id,
                            translated_text=translated_text,
                            status=task.status,
                            provider=provider.name,
                            model=provider.model,
                        ))
                    except LLMResponseValidationError as ve:
                        all_valid = False
                        results.append(LLMTranslationFailure(
                            sentence_id=task.sentence_id,
                            status=task.status,
                            error_message=str(ve),
                        ))

                if all_valid or attempt_index == retry_attempts - 1:
                    return results

                last_error = LLMResponseValidationError("部分片段验证失败")
                continue

            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "batch translate failed group_type=%s provider=%s attempt=%s error=%s",
                    group.group_type,
                    provider.name,
                    attempt_index + 1,
                    exc,
                )

    logger.info(
        "batch translate exhausted retries, falling back to per-task for %d items",
        len(group.tasks),
    )
    fallback_results: list[LLMTranslationResult | LLMTranslationFailure] = []
    for task in group.tasks:
        try:
            result = await _translate_single_task(
                task=task,
                providers=providers,
                clients=clients,
                settings=settings,
                translation_guidelines=translation_guidelines,
            )
            fallback_results.append(result)
        except Exception as exc:  # noqa: BLE001
            fallback_results.append(LLMTranslationFailure(
                sentence_id=task.sentence_id,
                status=task.status,
                error_message=str(exc),
            ))
    return fallback_results
