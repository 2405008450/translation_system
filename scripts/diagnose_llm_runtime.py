#!/usr/bin/env python3
"""诊断生产容器里的 LLM 运行环境。

这个脚本只使用 Python 标准库，适合直接在 Docker 容器里执行。
它会检查环境变量、DNS/TCP 连通性，并向 OpenAI 兼容的
`/chat/completions` 接口发送一次最小请求。输出会自动隐藏 API Key。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from dataclasses import dataclass


DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class Provider:
    name: str
    key_env: str
    base_url_env: str
    model_env: str
    default_base_url: str
    default_model: str


PROVIDERS = {
    "deepseek": Provider(
        name="deepseek",
        key_env="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
        model_env="DEEPSEEK_MODEL",
        default_base_url="https://api.deepseek.com",
        default_model="deepseek-chat",
    ),
    "openrouter": Provider(
        name="openrouter",
        key_env="OPENROUTER_API_KEY",
        base_url_env="OPENROUTER_BASE_URL",
        model_env="OPENROUTER_MODEL",
        default_base_url="https://openrouter.ai/api/v1",
        default_model="google/gemini-3.5-flash",
    ),
}


def mask_secret(value: str | None) -> str:
    if not value:
        return "未配置"
    if len(value) <= 10:
        return f"已配置，长度 {len(value)}"
    return f"{value[:4]}...{value[-4:]}，长度 {len(value)}"


def read_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[WARN] {name}={raw!r} 不是数字，改用默认值 {default}")
        return default


def normalize_base_url(value: str) -> str:
    return value.rstrip("/")


def chat_completions_url(base_url: str) -> str:
    return f"{normalize_base_url(base_url)}/chat/completions"


def print_runtime_info() -> None:
    print("== 运行环境 ==")
    print(f"Python: {sys.version.split()[0]}")
    print(f"系统: {platform.platform()}")
    print(f"主机名: {socket.gethostname()}")
    print(f"当前目录: {os.getcwd()}")
    print(f"时区: TZ={os.getenv('TZ') or '未设置'}")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        value = os.getenv(name) or os.getenv(name.lower())
        print(f"{name}: {'已设置' if value else '未设置'}")
    print()


def provider_config(provider: Provider, model_override: str | None) -> dict[str, str | None]:
    configured_model = os.getenv(provider.model_env) or provider.default_model
    return {
        "api_key": os.getenv(provider.key_env),
        "base_url": normalize_base_url(os.getenv(provider.base_url_env) or provider.default_base_url),
        "configured_model": configured_model,
        "model": model_override or configured_model,
        "model_source": "命令行覆盖" if model_override else ("环境变量" if os.getenv(provider.model_env) else "脚本默认值"),
    }


def print_provider_info(provider: Provider, config: dict[str, str | None]) -> None:
    print(f"== Provider: {provider.name} ==")
    print(f"{provider.key_env}: {mask_secret(config['api_key'])}")
    print(f"{provider.base_url_env}: {config['base_url']}")
    print(f"{provider.model_env}: {config['configured_model']}")
    if config["model"] != config["configured_model"]:
        print(f"本次测试模型: {config['model']}（{config['model_source']}）")


def parse_host_port(base_url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(base_url)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError(f"base_url 格式不正确：{base_url}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.hostname, port


def check_dns(host: str) -> bool:
    print(f"[1/3] DNS 解析 {host} ...", end=" ", flush=True)
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        print(f"失败：{exc}")
        return False
    addresses = sorted({item[4][0] for item in infos})
    print("成功：" + ", ".join(addresses[:5]))
    return True


def check_tcp(host: str, port: int, timeout: float) -> bool:
    print(f"[2/3] TCP 连接 {host}:{port} ...", end=" ", flush=True)
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as exc:
        print(f"失败：{exc}")
        return False
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    print(f"成功，耗时 {elapsed_ms} ms")
    return True


def sanitize_text(text: str, api_key: str | None) -> str:
    if api_key:
        text = text.replace(api_key, "[REDACTED_API_KEY]")
    return text


def extract_response_summary(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:800]

    if isinstance(payload, dict) and payload.get("error"):
        return json.dumps(payload["error"], ensure_ascii=False)[:1200]

    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not choices:
        return json.dumps(payload, ensure_ascii=False)[:1200]

    message = choices[0].get("message") or {}
    content = message.get("content")
    return str(content or "").strip()[:800]


def check_chat_completion(
    provider: Provider,
    config: dict[str, str | None],
    timeout: float,
    prompt: str,
) -> bool:
    api_key = config["api_key"]
    base_url = str(config["base_url"])
    model = str(config["model"])

    if not api_key:
        print("[3/3] API 请求跳过：API Key 未配置")
        return False

    url = chat_completions_url(base_url)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a connectivity test. Reply with exactly OK.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if provider.name == "openrouter":
        # 与应用代码保持一致，方便复现真实请求路径。
        headers["HTTP-Referer"] = "AI Translation System"
        headers["X-Title"] = "AI Translation System"

    print(f"[3/3] POST {url} model={model} ...", flush=True)
    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            body = response.read().decode("utf-8", errors="replace")
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            print(f"API 请求成功：HTTP {response.status}，耗时 {elapsed_ms} ms")
            print("返回摘要：" + extract_response_summary(sanitize_text(body, api_key)))
            return True
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"API 请求失败：HTTP {exc.code}")
        print("错误摘要：" + extract_response_summary(sanitize_text(body, api_key)))
        return False
    except urllib.error.URLError as exc:
        print(f"API 请求失败：{exc.reason}")
        return False
    except TimeoutError:
        print(f"API 请求失败：超过 {timeout:g} 秒未返回")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"API 请求失败：{type(exc).__name__}: {exc}")
        return False


def ensure_app_import_path() -> None:
    candidates = [Path.cwd(), Path("/app")]
    for item in candidates:
        if (item / "app").is_dir() and str(item) not in sys.path:
            sys.path.insert(0, str(item))


async def run_app_translation_check(
    provider_name: str,
    model_override: str | None,
    translation_unit: str,
    source_text: str,
    source_language: str,
    target_language: str,
) -> bool:
    ensure_app_import_path()
    try:
        from app.config import get_settings
        from app.services.llm_service import (
            LLMTranslationFailure,
            LLMTranslationTask,
            iter_batch_translate,
        )
    except Exception as exc:  # noqa: BLE001
        print("== 应用翻译链路 ==")
        print(f"无法导入应用代码：{type(exc).__name__}: {exc}")
        print("提示：请在应用容器内执行，或确认 /app/app 目录存在。")
        return False

    settings = get_settings()
    print("== 应用翻译链路 ==")
    print(f"provider: {provider_name}")
    print(f"model_override: {model_override or '未指定，使用应用配置'}")
    print(f"translation_unit: {translation_unit}")
    print(f"应用读取 DEEPSEEK_MODEL: {settings.deepseek_model}")
    print(f"应用读取 OPENROUTER_MODEL: {settings.openrouter_model}")
    print(f"应用读取 LLM_TIMEOUT_SECONDS: {settings.llm_timeout_seconds}")
    print(f"应用读取 LLM_STALL_TIMEOUT_SECONDS: {settings.llm_stall_timeout_seconds}")

    task = LLMTranslationTask(
        sentence_id="diagnose-1",
        status="none",
        source_text=source_text,
        source_language=source_language,
        target_language=target_language,
        block_type="paragraph",
        block_index=1,
    )

    try:
        async for item in iter_batch_translate(
            [task],
            provider=provider_name,  # type: ignore[arg-type]
            translation_unit=translation_unit,  # type: ignore[arg-type]
            model_override=model_override,
        ):
            if isinstance(item, LLMTranslationFailure):
                print("应用翻译失败：")
                print(f"sentence_id: {item.sentence_id}")
                print(f"status: {item.status}")
                print(f"error_message: {item.error_message}")
                return False
            print("应用翻译成功：")
            print(f"provider: {item.provider}")
            print(f"model: {item.model}")
            print(f"translated_text: {item.translated_text}")
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"应用翻译异常：{type(exc).__name__}: {exc}")
        return False

    print("应用翻译没有返回任何结果。")
    return False


def choose_providers(name: str) -> list[Provider]:
    if name == "auto":
        return [PROVIDERS["deepseek"], PROVIDERS["openrouter"]]
    return [PROVIDERS[name]]


def main() -> int:
    parser = argparse.ArgumentParser(description="诊断 Docker 生产环境的 LLM 配置和出站连通性")
    parser.add_argument(
        "--provider",
        choices=["auto", "deepseek", "openrouter"],
        default="auto",
        help="要测试的 provider，默认 auto 会按应用顺序测试 deepseek 和 openrouter",
    )
    parser.add_argument("--model", default=None, help="临时覆盖模型名，便于测试某个模型是否可用")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="请求超时时间，默认读取 LLM_TIMEOUT_SECONDS，没有配置则为 30 秒",
    )
    parser.add_argument(
        "--prompt",
        default="请只回复 OK",
        help="用于测试的最小 prompt",
    )
    parser.add_argument(
        "--app-translation-check",
        action="store_true",
        help="额外调用项目内 llm_service，复现 AI 修正使用的翻译链路",
    )
    parser.add_argument(
        "--translation-unit",
        choices=["paragraph", "sentence"],
        default="paragraph",
        help="应用翻译链路的模式；页面默认是 paragraph",
    )
    parser.add_argument(
        "--source-text",
        default="Hello world.",
        help="应用翻译链路测试用原文",
    )
    parser.add_argument(
        "--source-language",
        default="en",
        help="应用翻译链路测试用源语言",
    )
    parser.add_argument(
        "--target-language",
        default="zh-CN",
        help="应用翻译链路测试用目标语言",
    )
    args = parser.parse_args()

    timeout = args.timeout or read_float_env("LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    timeout = max(timeout, 1.0)
    print_runtime_info()
    print(f"请求超时: {timeout:g} 秒")
    print()

    any_success = False
    any_configured = False
    for provider in choose_providers(args.provider):
        config = provider_config(provider, args.model)
        print_provider_info(provider, config)
        if config["api_key"]:
            any_configured = True

        try:
            host, port = parse_host_port(str(config["base_url"]))
        except ValueError as exc:
            print(f"配置错误：{exc}")
            print()
            continue

        check_dns(host)
        check_tcp(host, port, min(timeout, 10.0))
        if check_chat_completion(provider, config, timeout, args.prompt):
            any_success = True
        print()

    app_success = True
    if args.app_translation_check:
        import asyncio

        app_success = asyncio.run(
            run_app_translation_check(
                provider_name=args.provider,
                model_override=args.model,
                translation_unit=args.translation_unit,
                source_text=args.source_text,
                source_language=args.source_language,
                target_language=args.target_language,
            )
        )
        print()

    if args.app_translation_check and app_success:
        if any_success:
            print("结论：基础 LLM 请求和应用翻译链路都可用。")
        else:
            print("结论：应用翻译链路可用；基础直连未成功，可能是环境变量与应用配置加载方式不同。")
        return 0

    if any_success and app_success:
        if args.app_translation_check:
            print("结论：基础 LLM 请求和应用翻译链路都可用。")
        else:
            print("结论：至少一个 LLM provider 在当前运行环境中可用。")
        return 0
    if not any_configured:
        print("结论：当前容器没有读到 DEEPSEEK_API_KEY 或 OPENROUTER_API_KEY。")
        return 2
    if any_success and not app_success:
        print("结论：基础 LLM 请求可用，但应用翻译链路失败。请看“应用翻译链路”的错误。")
        return 3
    print("结论：容器读到了 API Key，但 LLM 请求没有成功。请优先查看上面的 HTTP/DNS/TCP 错误摘要。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
