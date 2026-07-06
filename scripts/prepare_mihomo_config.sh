#!/usr/bin/env bash
# 下载 Clash/Mihomo 订阅配置，并规范成本项目 Docker 内网使用的入站端口。
# 用法：
#   MIHOMO_SUBSCRIPTION_URL='https://example.com/sub' bash scripts/prepare_mihomo_config.sh
#   MIHOMO_OPENROUTER_POLICY='日本节点名或美国节点名' MIHOMO_SUBSCRIPTION_URL='https://example.com/sub' bash scripts/prepare_mihomo_config.sh
#   MIHOMO_OPENROUTER_FALLBACK=true MIHOMO_SUBSCRIPTION_URL='https://example.com/sub' bash scripts/prepare_mihomo_config.sh
#   bash scripts/prepare_mihomo_config.sh 'https://example.com/sub' docker/mihomo/config.yaml

set -euo pipefail

SUBSCRIPTION_URL="${1:-${MIHOMO_SUBSCRIPTION_URL:-}}"
OUTPUT_PATH="${2:-docker/mihomo/config.yaml}"
SUBSCRIPTION_USER_AGENT="${MIHOMO_SUBSCRIPTION_USER_AGENT:-Clash.Meta}"
OPENROUTER_POLICY="${MIHOMO_OPENROUTER_POLICY:-}"
OPENROUTER_FALLBACK="${MIHOMO_OPENROUTER_FALLBACK:-}"
OPENROUTER_FALLBACK_GROUP="${MIHOMO_OPENROUTER_FALLBACK_GROUP:-OpenRouter-Fallback}"
OPENROUTER_FALLBACK_URL="${MIHOMO_OPENROUTER_FALLBACK_URL:-https://openrouter.ai/api/v1/models}"
OPENROUTER_FALLBACK_INTERVAL="${MIHOMO_OPENROUTER_FALLBACK_INTERVAL:-30}"
OPENROUTER_FALLBACK_TIMEOUT="${MIHOMO_OPENROUTER_FALLBACK_TIMEOUT:-2000}"

if [ -z "${SUBSCRIPTION_URL}" ]; then
  echo "错误：请通过第一个参数或 MIHOMO_SUBSCRIPTION_URL 提供 Clash 订阅链接。"
  exit 2
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "错误：当前服务器找不到 curl 命令。"
  exit 127
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -n "${PYTHON_BIN}" ]; then
  if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "错误：PYTHON_BIN=${PYTHON_BIN} 不可执行。"
    exit 127
  fi
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "错误：当前服务器找不到 python3 或 python 命令。"
  exit 127
fi

mkdir -p "$(dirname "${OUTPUT_PATH}")"
TMP_FILE="$(mktemp)"
trap 'rm -f "${TMP_FILE}"' EXIT

echo "正在下载 Clash 订阅配置..."
curl -fsSL \
  -A "${SUBSCRIPTION_USER_AGENT}" \
  -H "Accept: text/yaml,application/yaml,text/plain,*/*" \
  "${SUBSCRIPTION_URL}" \
  -o "${TMP_FILE}"

if [ ! -s "${TMP_FILE}" ]; then
  echo "错误：订阅下载结果为空。"
  exit 1
fi

"${PYTHON_BIN}" - "${TMP_FILE}" "${OUTPUT_PATH}" "${OPENROUTER_POLICY}" "${OPENROUTER_FALLBACK}" "${OPENROUTER_FALLBACK_GROUP}" "${OPENROUTER_FALLBACK_URL}" "${OPENROUTER_FALLBACK_INTERVAL}" "${OPENROUTER_FALLBACK_TIMEOUT}" <<'PY'
from __future__ import annotations

import re
import sys
import base64
import binascii
from pathlib import Path

source_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
openrouter_policy = sys.argv[3].strip()
openrouter_fallback_enabled = sys.argv[4].strip().lower() in {"1", "true", "yes", "on"}
openrouter_fallback_group = sys.argv[5].strip() or "OpenRouter-Fallback"
openrouter_fallback_url = sys.argv[6].strip() or "https://openrouter.ai/api/v1/models"
openrouter_fallback_interval = sys.argv[7].strip() or "30"
openrouter_fallback_timeout = sys.argv[8].strip() or "2000"

raw_text = source_path.read_text(encoding="utf-8-sig")
if "proxies:" not in raw_text and "proxy-providers:" not in raw_text:
    sample = raw_text.strip()[:4096]
    reason = "下载内容不像 Clash YAML 配置。"
    if sample.startswith("<!DOCTYPE") or sample.startswith("<html") or "<html" in sample[:200].lower():
        reason = "订阅地址返回了 HTML 页面，可能是链接失效、被防火墙拦截或需要登录。"
    elif sample.startswith("{") or sample.startswith("["):
        reason = "订阅地址返回了 JSON，而不是 Clash YAML。"
    else:
        compact = re.sub(r"\s+", "", sample)
        if compact and re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact):
            try:
                decoded = base64.b64decode(compact + "=" * (-len(compact) % 4), validate=False).decode(
                    "utf-8",
                    errors="ignore",
                )
            except (binascii.Error, UnicodeDecodeError):
                decoded = ""
            if any(marker in decoded for marker in ("vmess://", "vless://", "trojan://", "ss://", "ssr://")):
                reason = "订阅地址返回了通用 base64 节点订阅，不是 Clash YAML。请在机场后台复制 Clash/Mihomo 专用订阅，或使用订阅转换后再运行。"
    raise SystemExit(f"错误：{reason}")

# 移除常见顶层入站字段，避免订阅自带端口与本项目的 mixed-port 冲突。
top_level_override_re = re.compile(
    r"^(?:mixed-port|port|socks-port|redir-port|tproxy-port|allow-lan|bind-address|mode|log-level|external-controller)\s*:"
)
filtered_lines = [
    line for line in raw_text.splitlines()
    if not top_level_override_re.match(line)
]

def _strip_yaml_scalar(value: str) -> str:
    value = value.strip()
    if value.startswith(("'", '"')) and value.endswith(value[0]):
        return value[1:-1]
    return value


def _quote_yaml_scalar(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _extract_proxy_names(lines: list[str]) -> list[str]:
    names: list[str] = []
    in_proxies = False
    for line in lines:
        if re.match(r"^proxies\s*:\s*$", line):
            in_proxies = True
            continue
        if in_proxies and re.match(r"^[A-Za-z0-9_-][A-Za-z0-9_-]*\s*:", line):
            break
        if not in_proxies:
            continue

        block_match = re.match(r"^\s*-\s*name\s*:\s*(.+?)\s*$", line)
        inline_match = re.match(r"^\s*-\s*\{\s*name\s*:\s*([^,}]+)", line)
        raw_name = None
        if block_match:
            raw_name = block_match.group(1)
        elif inline_match:
            raw_name = inline_match.group(1)
        if raw_name:
            names.append(_strip_yaml_scalar(raw_name))
    return names


def _is_jp_us_proxy(name: str) -> bool:
    region_markers = (
        "日本",
        "东京",
        "大阪",
        "🇯🇵",
        "JP",
        "Japan",
        "美国",
        "美國",
        "🇺🇸",
        "US",
        "USA",
        "United States",
        "洛杉矶",
        "洛杉磯",
        "硅谷",
        "圣何塞",
    )
    metadata_markers = ("剩余", "套餐", "到期", "重置")
    return any(marker in name for marker in region_markers) and not any(
        marker in name for marker in metadata_markers
    )


def _fallback_priority(name: str) -> tuple[int, str]:
    if ("日本" in name or "🇯🇵" in name or "JP" in name or "Japan" in name) and "专线" in name:
        return (0, name)
    if ("美国" in name or "美國" in name or "🇺🇸" in name or "US" in name or "USA" in name) and "流媒体" in name:
        return (1, name)
    if "美国" in name or "美國" in name or "🇺🇸" in name or "US" in name or "USA" in name:
        return (2, name)
    if "日本" in name or "🇯🇵" in name or "JP" in name or "Japan" in name:
        return (3, name)
    return (9, name)


def _insert_fallback_group(lines: list[str], group_name: str) -> tuple[list[str], int]:
    candidates = sorted(
        [name for name in _extract_proxy_names(lines) if _is_jp_us_proxy(name)],
        key=_fallback_priority,
    )
    if not candidates:
        raise SystemExit("错误：启用了 MIHOMO_OPENROUTER_FALLBACK，但没有在订阅中找到日本或美国节点。")

    group_block = [
        f"  - name: {_quote_yaml_scalar(group_name)}",
        "    type: fallback",
        "    proxies:",
        *[f"      - {_quote_yaml_scalar(name)}" for name in candidates],
        f"    url: {_quote_yaml_scalar(openrouter_fallback_url)}",
        f"    interval: {openrouter_fallback_interval}",
        f"    timeout: {openrouter_fallback_timeout}",
        "    lazy: false",
    ]

    result: list[str] = []
    inserted = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.match(r"^proxy-groups\s*:\s*$", line):
            result.append(line)
            result.extend(group_block)
            inserted = True
            index += 1
            continue
        result.append(line)
        index += 1

    if not inserted:
        result.extend(["", "proxy-groups:", *group_block])

    return result, len(candidates)


fallback_count = 0
if openrouter_fallback_enabled:
    filtered_lines, fallback_count = _insert_fallback_group(filtered_lines, openrouter_fallback_group)
    openrouter_policy = openrouter_fallback_group

if openrouter_policy:
    openrouter_rule = f"  - {_quote_yaml_scalar(f'DOMAIN-SUFFIX,openrouter.ai,{openrouter_policy}')}"
    filtered_lines = [
        line
        for line in filtered_lines
        if not (line.lstrip().startswith("-") and "openrouter.ai" in line)
    ]
    if openrouter_rule not in filtered_lines:
        for index, line in enumerate(filtered_lines):
            if line.strip() == "rules:":
                filtered_lines.insert(index + 1, openrouter_rule)
                break
        else:
            filtered_lines.extend(["", "rules:", openrouter_rule])

prefix = [
    "# 由 scripts/prepare_mihomo_config.sh 生成。",
    "# 本文件可能包含订阅节点和凭据，不要提交到 Git。",
    "mixed-port: 7890",
    "allow-lan: true",
    'bind-address: "*"',
    "mode: rule",
    "log-level: info",
    "external-controller: 127.0.0.1:9090",
    "",
]

output_path.write_text("\n".join(prefix + filtered_lines).rstrip() + "\n", encoding="utf-8")

if fallback_count:
    print(f"已生成 {openrouter_fallback_group}，包含 {fallback_count} 个日本/美国候选节点。")
PY

echo "已生成 ${OUTPUT_PATH}"
if [ -n "${OPENROUTER_POLICY}" ]; then
  echo "已将 openrouter.ai 固定到策略：${OPENROUTER_POLICY}"
fi
if [ -n "${OPENROUTER_FALLBACK}" ]; then
  echo "已启用 OpenRouter 故障转移策略：${OPENROUTER_FALLBACK_GROUP}"
  echo "健康检查：${OPENROUTER_FALLBACK_URL}，间隔 ${OPENROUTER_FALLBACK_INTERVAL}s，超时 ${OPENROUTER_FALLBACK_TIMEOUT}ms"
fi
echo "下一步：docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.proxy.yml up -d"
