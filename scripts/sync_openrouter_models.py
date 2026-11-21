"""同步现有翻译系列的模型目录与公开现行价格：python scripts/sync_openrouter_models.py。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MODELS_URL = "https://openrouter.ai/api/v1/models"
# 保留已有模型，同时补齐这些系列当前可用的主要新型号；不混入 Batch 或多模态生成模型。
ADDITIONS = [
    "google/gemini-4-neon", "google/gemini-4-hydrogen", "google/gemini-4-carbon", "google/gemini-3.5-flash-lite",
    "openai/gpt-6.2-astra", "openai/gpt-6.2-astra-pro", "openai/gpt-6.2-sol", "openai/gpt-6.2-sol-pro",
    "openai/gpt-6.1-terra", "openai/gpt-6.1-terra-pro", "openai/gpt-6.1-sol-pro",
    "openai/gpt-6-luna-pro", "openai/gpt-6-sol-pro", "openai/gpt-6-astra-pro",
    "openai/gpt-5.6-sol", "openai/gpt-5.6-sol-pro",
    "deepseek/deepseek-v4.1-flash-1017", "deepseek/deepseek-v4.1-flash",
    "deepseek/deepseek-v4-pro-0813", "deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash",
]


def sync():
    with urlopen(Request(MODELS_URL, headers={"User-Agent": "TranslationPlatform-ModelSync/1.0"}), timeout=60) as response:
        catalog = json.load(response)["data"]
    by_id = {model["id"]: model for model in catalog}
    path = ROOT / "frontend/src/constants/llm.ts"
    source = path.read_text(encoding="utf-8")
    pattern = r"export const llmModelOptions: LLMModelOption\[\] = \[.*?\n\]"
    original = re.search(pattern, source, re.S).group()
    old_ids = re.findall(r"id: '([^']+)'", original)
    # DeepSeek 直连保留原入口，不将 OpenRouter 报价贴到直连调用上。
    direct_id = "deepseek/deepseek-chat"
    requested = set(old_ids + ADDITIONS) - {direct_id}
    selected = [model for model in catalog if model["id"] in requested]
    missing = requested - {model["id"] for model in selected}
    if missing:
        raise ValueError(f"目录缺少模型，请人工确认后更新清单：{sorted(missing)}")
    entries, rates, release_dates = [], [], []
    for model in selected:
        model_id = model["id"]
        release_date = datetime.fromtimestamp(model["created"], timezone.utc).date().isoformat()
        release_dates.append(f"  '{model_id}': '{release_date}',")
        if "text" not in model["architecture"]["output_modalities"] or ":batch" in model_id:
            raise ValueError(f"不是同步文本生成模型：{model_id}")
        family = "gemini" if model_id.startswith("google/") else "gpt" if model_id.startswith("openai/") else "deepseek"
        name = model["name"].split(": ", 1)[-1]
        entries.append(f"  {{ id: '{model_id}', name: {json.dumps(name)}, family: '{family}', provider: 'openrouter' }},")
        price = model["pricing"]
        values = [Decimal(price[key]) * 1_000_000 for key in ("prompt", "completion")]
        if any(not value.is_finite() or value < 0 for value in values):
            raise ValueError(f"无效价格：{model_id}")
        rates.append(f"  '{model_id}': {{ input: {values[0]:f}, output: {values[1]:f} }},")
    entries.append("  { id: 'deepseek/deepseek-chat', name: 'DeepSeek Chat（直连）', family: 'deepseek', provider: 'deepseek' },")
    source = re.sub(pattern, "export const llmModelOptions: LLMModelOption[] = [\n" + "\n".join(entries) + "\n]", source, flags=re.S)
    source = source.replace("'google/gemini-3.5-flash': 'Gemini 3.5',", "'google/gemini-3.5-flash': 'Gemini 3.5 Flash',")
    pricing = "\n".join([
        "// OpenRouter 标准同步调用的现行单价（已包含公开优惠），美元 / 百万 token。",
        "// pricing.prompt/completion 已为当前价格，不再二次打折；不使用 Batch/Flex 或缓存命中价格替代。",
        f"export const LLM_PRICING_UPDATED_AT = '{datetime.now(timezone.utc).date().isoformat()}'",
        "export const LLM_PRICING_SOURCE_URL = 'https://openrouter.ai/models'",
        "",
        "export const openRouterModelPricing: Record<string, { input: number; output: number }> = {",
        *rates, "}", "",
        "// 使用 UTC 发布日期，同日发布的型号可按现行价格排序。",
        "export const openRouterModelReleaseDates: Record<string, string> = {",
        *release_dates, "}", "",
    ])
    path.write_text(source, encoding="utf-8")
    (ROOT / "frontend/src/constants/llmPricing.ts").write_text(pricing, encoding="utf-8")
    print(f"已同步 {len(selected)} 个 OpenRouter 模型及现行报价，保留 1 个 DeepSeek 直连入口。")


if __name__ == "__main__":
    sync()
