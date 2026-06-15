#!/usr/bin/env bash
# 下载 Clash/Mihomo 订阅配置，并规范成本项目 Docker 内网使用的入站端口。
# 用法：
#   MIHOMO_SUBSCRIPTION_URL='https://example.com/sub' bash scripts/prepare_mihomo_config.sh
#   MIHOMO_OPENROUTER_POLICY='日本节点名或美国节点名' MIHOMO_SUBSCRIPTION_URL='https://example.com/sub' bash scripts/prepare_mihomo_config.sh
#   bash scripts/prepare_mihomo_config.sh 'https://example.com/sub' docker/mihomo/config.yaml

set -euo pipefail

SUBSCRIPTION_URL="${1:-${MIHOMO_SUBSCRIPTION_URL:-}}"
OUTPUT_PATH="${2:-docker/mihomo/config.yaml}"
SUBSCRIPTION_USER_AGENT="${MIHOMO_SUBSCRIPTION_USER_AGENT:-Clash.Meta}"
OPENROUTER_POLICY="${MIHOMO_OPENROUTER_POLICY:-}"

if [ -z "${SUBSCRIPTION_URL}" ]; then
  echo "错误：请通过第一个参数或 MIHOMO_SUBSCRIPTION_URL 提供 Clash 订阅链接。"
  exit 2
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "错误：当前服务器找不到 curl 命令。"
  exit 127
fi

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
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

"${PYTHON_BIN}" - "${TMP_FILE}" "${OUTPUT_PATH}" "${OPENROUTER_POLICY}" <<'PY'
from __future__ import annotations

import re
import sys
import base64
import binascii
from pathlib import Path

source_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
openrouter_policy = sys.argv[3].strip()

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

if openrouter_policy:
    openrouter_rule = f"  - DOMAIN-SUFFIX,openrouter.ai,{openrouter_policy}"
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
PY

echo "已生成 ${OUTPUT_PATH}"
if [ -n "${OPENROUTER_POLICY}" ]; then
  echo "已将 openrouter.ai 固定到策略：${OPENROUTER_POLICY}"
fi
echo "下一步：docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.proxy.yml up -d"
