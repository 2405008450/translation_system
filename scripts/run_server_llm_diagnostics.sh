#!/usr/bin/env bash
# 在生产服务器上运行 LLM 诊断。
# 用法：
#   bash scripts/run_server_llm_diagnostics.sh
#   CONTAINER=ai-translation-app bash scripts/run_server_llm_diagnostics.sh --provider openrouter

set -u

REQUESTED_CONTAINER="${CONTAINER:-}"
DEFAULT_CONTAINER="ai-translation-app"
LOCAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${LOCAL_SCRIPT_DIR}/.." && pwd)"
LOCAL_SCRIPT="${LOCAL_SCRIPT_DIR}/diagnose_llm_runtime.py"
IN_IMAGE_SCRIPT="/app/scripts/diagnose_llm_runtime.py"
TMP_SCRIPT="/tmp/diagnose_llm_runtime.py"
DOCKER_EXEC_FLAGS=(-i)
DOCKER_CMD=(docker)

if [ -t 0 ] && [ -t 1 ]; then
  DOCKER_EXEC_FLAGS=(-it)
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "错误：当前服务器找不到 docker 命令。"
  exit 127
fi

if ! DOCKER_PS_OUTPUT="$(docker ps --format '{{.Names}}' 2>&1)"; then
  if command -v sudo >/dev/null 2>&1 && sudo -n docker ps --format '{{.Names}}' >/dev/null 2>&1; then
    DOCKER_CMD=(sudo docker)
    DOCKER_PS_OUTPUT="$(sudo docker ps --format '{{.Names}}' 2>&1)"
  else
    echo "错误：当前用户无法访问 Docker。"
    echo "${DOCKER_PS_OUTPUT}"
    echo "提示：请改用 sudo 运行，例如：sudo bash scripts/run_server_llm_diagnostics.sh --provider deepseek --model deepseek-chat"
    exit 1
  fi
fi

container_exists() {
  "${DOCKER_CMD[@]}" inspect "$1" >/dev/null 2>&1
}

find_app_container() {
  local candidate
  local compose_id

  if [ -n "${REQUESTED_CONTAINER}" ]; then
    if container_exists "${REQUESTED_CONTAINER}"; then
      echo "${REQUESTED_CONTAINER}"
      return 0
    fi
    return 1
  fi

  if container_exists "${DEFAULT_CONTAINER}"; then
    echo "${DEFAULT_CONTAINER}"
    return 0
  fi

  if [ -f "${PROJECT_ROOT}/docker-compose.prod.yml" ]; then
    compose_id="$(
      "${DOCKER_CMD[@]}" compose \
        --env-file "${PROJECT_ROOT}/.env.prod" \
        -f "${PROJECT_ROOT}/docker-compose.prod.yml" \
        ps -q app 2>/dev/null | head -n 1
    )"
    if [ -n "${compose_id}" ]; then
      candidate="$("${DOCKER_CMD[@]}" inspect -f '{{.Name}}' "${compose_id}" 2>/dev/null | sed 's#^/##')"
      if [ -n "${candidate}" ]; then
        echo "${candidate}"
        return 0
      fi
    fi
  fi

  candidate="$(
    "${DOCKER_CMD[@]}" ps \
      --filter 'label=com.docker.compose.project=ai-translation-system' \
      --filter 'label=com.docker.compose.service=app' \
      --format '{{.Names}}' | head -n 1
  )"
  if [ -n "${candidate}" ]; then
    echo "${candidate}"
    return 0
  fi

  candidate="$(
    "${DOCKER_CMD[@]}" ps \
      --filter 'label=com.docker.compose.service=app' \
      --format '{{.Names}}' | head -n 1
  )"
  if [ -n "${candidate}" ]; then
    echo "${candidate}"
    return 0
  fi

  candidate="$(
    "${DOCKER_CMD[@]}" ps \
      --filter 'ancestor=ai-translation-system:latest' \
      --format '{{.Names}}' | head -n 1
  )"
  if [ -n "${candidate}" ]; then
    echo "${candidate}"
    return 0
  fi

  candidate="$(
    "${DOCKER_CMD[@]}" ps --format '{{.Names}}\t{{.Ports}}' \
      | awk -F '\t' '$2 ~ /19013/ { print $1; exit }'
  )"
  if [ -n "${candidate}" ]; then
    echo "${candidate}"
    return 0
  fi

  return 1
}

CONTAINER="$(find_app_container || true)"
if [ -z "${CONTAINER}" ] || ! container_exists "${CONTAINER}"; then
  if [ -n "${REQUESTED_CONTAINER}" ]; then
    echo "错误：找不到你指定的容器 ${REQUESTED_CONTAINER}。"
  else
    echo "错误：没有自动找到应用容器。"
  fi
  echo "当前运行中的容器："
  "${DOCKER_CMD[@]}" ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  echo "提示：确认应用容器名称后运行：CONTAINER=容器名 bash scripts/run_server_llm_diagnostics.sh"
  exit 1
fi

echo "使用容器：${CONTAINER}"

if "${DOCKER_CMD[@]}" exec "${CONTAINER}" test -f "${IN_IMAGE_SCRIPT}" >/dev/null 2>&1; then
  "${DOCKER_CMD[@]}" exec "${DOCKER_EXEC_FLAGS[@]}" "${CONTAINER}" python "${IN_IMAGE_SCRIPT}" "$@"
  exit $?
fi

if [ ! -f "${LOCAL_SCRIPT}" ]; then
  echo "错误：本地找不到 ${LOCAL_SCRIPT}，无法拷贝到容器。"
  exit 1
fi

echo "镜像内没有 ${IN_IMAGE_SCRIPT}，临时拷贝脚本到容器 ${TMP_SCRIPT} ..."
"${DOCKER_CMD[@]}" cp "${LOCAL_SCRIPT}" "${CONTAINER}:${TMP_SCRIPT}" || exit $?
"${DOCKER_CMD[@]}" exec "${DOCKER_EXEC_FLAGS[@]}" "${CONTAINER}" python "${TMP_SCRIPT}" "$@"
