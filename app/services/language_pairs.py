from __future__ import annotations

from dataclasses import dataclass

from app.services.normalizer import normalize_text


@dataclass(frozen=True)
class LanguageOption:
    code: str
    label: str


LANGUAGE_OPTIONS: tuple[LanguageOption, ...] = (
    LanguageOption("zh-CN", "中文（简体）"),
    LanguageOption("zh-TW", "中文（繁体）"),
    LanguageOption("zh-HK", "中文（香港）"),
    LanguageOption("zh-MO", "中文（繁体澳门）"),
    LanguageOption("en-US", "英语（美国）"),
    LanguageOption("en-GB", "英语（英国）"),
    LanguageOption("ja-JP", "日语"),
    LanguageOption("ko-KR", "韩语"),
    LanguageOption("fr-FR", "法语"),
    LanguageOption("de-DE", "德语"),
    LanguageOption("es-ES", "西班牙语"),
    LanguageOption("pt-BR", "葡萄牙语（巴西）"),
    LanguageOption("it-IT", "意大利语"),
    LanguageOption("ru-RU", "俄语"),
    LanguageOption("ar-SA", "阿拉伯语"),
    LanguageOption("th-TH", "泰语"),
    LanguageOption("vi-VN", "越南语"),
)

LANGUAGE_LABELS = {option.code: option.label for option in LANGUAGE_OPTIONS}
LANGUAGE_ALIASES = {
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-CN",
    "zh-tw": "zh-TW",
    "zh-hant": "zh-TW",
    "zh-hk": "zh-HK",
    "zh-mo": "zh-MO",
    "zh-hant-mo": "zh-MO",
    "en": "en-US",
    "en-us": "en-US",
    "en-gb": "en-GB",
    "ja": "ja-JP",
    "ja-jp": "ja-JP",
    "ko": "ko-KR",
    "ko-kr": "ko-KR",
    "fr": "fr-FR",
    "fr-fr": "fr-FR",
    "de": "de-DE",
    "de-de": "de-DE",
    "es": "es-ES",
    "es-es": "es-ES",
    "pt": "pt-BR",
    "pt-br": "pt-BR",
    "it": "it-IT",
    "it-it": "it-IT",
    "ru": "ru-RU",
    "ru-ru": "ru-RU",
    "ar": "ar-SA",
    "ar-sa": "ar-SA",
    "th": "th-TH",
    "th-th": "th-TH",
    "vi": "vi-VN",
    "vi-vn": "vi-VN",
}


def serialize_language_options() -> list[dict[str, str]]:
    return [{"code": option.code, "label": option.label} for option in LANGUAGE_OPTIONS]


def normalize_language_code(value: str | None, *, field_label: str) -> str | None:
    cleaned = normalize_text(value or "")
    if not cleaned:
        return None

    normalized = LANGUAGE_ALIASES.get(cleaned.lower())
    if normalized is None:
        raise ValueError(f"{field_label}不在当前支持的语言列表中。")
    return normalized


def require_language_pair(
    source_language: str | None,
    target_language: str | None,
) -> tuple[str, str]:
    normalized_source = normalize_language_code(source_language, field_label="源语言")
    normalized_target = normalize_language_code(target_language, field_label="目标语言")
    if not normalized_source or not normalized_target:
        raise ValueError("请先选择源语言和目标语言。")
    if normalized_source == normalized_target:
        raise ValueError("源语言和目标语言不能相同。")
    return normalized_source, normalized_target


def format_language_pair(
    source_language: str | None,
    target_language: str | None,
) -> str:
    if not source_language or not target_language:
        return "未设置语言对"
    source_label = LANGUAGE_LABELS.get(source_language, source_language)
    target_label = LANGUAGE_LABELS.get(target_language, target_language)
    return f"{source_label} -> {target_label}"
