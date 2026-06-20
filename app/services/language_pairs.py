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
    LanguageOption("pt-PT", "葡萄牙语（葡萄牙）"),
    LanguageOption("it-IT", "意大利语"),
    LanguageOption("ru-RU", "俄语"),
    LanguageOption("pl-PL", "波兰语"),
    LanguageOption("nl-NL", "荷兰语"),
    LanguageOption("sv-SE", "瑞典语"),
    LanguageOption("da-DK", "丹麦语"),
    LanguageOption("fi-FI", "芬兰语"),
    LanguageOption("no-NO", "挪威语"),
    LanguageOption("tr-TR", "土耳其语"),
    LanguageOption("uk-UA", "乌克兰语"),
    LanguageOption("cs-CZ", "捷克语"),
    LanguageOption("sk-SK", "斯洛伐克语"),
    LanguageOption("ro-RO", "罗马尼亚语"),
    LanguageOption("hu-HU", "匈牙利语"),
    LanguageOption("el-GR", "希腊语"),
    LanguageOption("bg-BG", "保加利亚语"),
    LanguageOption("hr-HR", "克罗地亚语"),
    LanguageOption("sr-RS", "塞尔维亚语"),
    LanguageOption("sl-SI", "斯洛文尼亚语"),
    LanguageOption("lt-LT", "立陶宛语"),
    LanguageOption("lv-LV", "拉脱维亚语"),
    LanguageOption("et-EE", "爱沙尼亚语"),
    LanguageOption("ar-SA", "阿拉伯语"),
    LanguageOption("he-IL", "希伯来语"),
    LanguageOption("fa-IR", "波斯语"),
    LanguageOption("ur-PK", "乌尔都语"),
    LanguageOption("hi-IN", "印地语"),
    LanguageOption("bn-BD", "孟加拉语"),
    LanguageOption("id-ID", "印尼语"),
    LanguageOption("ms-MY", "马来语"),
    LanguageOption("th-TH", "泰语"),
    LanguageOption("vi-VN", "越南语"),
    LanguageOption("fil-PH", "菲律宾语"),
    LanguageOption("my-MM", "缅甸语"),
    LanguageOption("km-KH", "高棉语"),
    LanguageOption("lo-LA", "老挝语"),
    LanguageOption("sw-KE", "斯瓦希里语"),
)

LANGUAGE_LABELS = {option.code: option.label for option in LANGUAGE_OPTIONS}

LANGUAGE_ALIASES: dict[str, str] = {option.code.lower(): option.code for option in LANGUAGE_OPTIONS}
LANGUAGE_ALIASES.update(
    {
        "zh": "zh-CN",
        "zh-hans": "zh-CN",
        "zh-hant": "zh-TW",
        "zh-hant-hk": "zh-HK",
        "zh-hant-mo": "zh-MO",
        "en": "en-US",
        "ja": "ja-JP",
        "ko": "ko-KR",
        "fr": "fr-FR",
        "de": "de-DE",
        "es": "es-ES",
        "pt": "pt-BR",
        "it": "it-IT",
        "ru": "ru-RU",
        "pl": "pl-PL",
        "nl": "nl-NL",
        "sv": "sv-SE",
        "da": "da-DK",
        "fi": "fi-FI",
        "no": "no-NO",
        "nb": "no-NO",
        "nb-no": "no-NO",
        "tr": "tr-TR",
        "uk": "uk-UA",
        "cs": "cs-CZ",
        "sk": "sk-SK",
        "ro": "ro-RO",
        "hu": "hu-HU",
        "el": "el-GR",
        "bg": "bg-BG",
        "hr": "hr-HR",
        "sr": "sr-RS",
        "sl": "sl-SI",
        "lt": "lt-LT",
        "lv": "lv-LV",
        "et": "et-EE",
        "ar": "ar-SA",
        "he": "he-IL",
        "iw": "he-IL",
        "fa": "fa-IR",
        "ur": "ur-PK",
        "hi": "hi-IN",
        "bn": "bn-BD",
        "id": "id-ID",
        "in": "id-ID",
        "ms": "ms-MY",
        "th": "th-TH",
        "vi": "vi-VN",
        "fil": "fil-PH",
        "tl": "fil-PH",
        "my": "my-MM",
        "km": "km-KH",
        "lo": "lo-LA",
        "sw": "sw-KE",
    }
)


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
