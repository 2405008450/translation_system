#!/usr/bin/env bash
# 在生产服务器上运行 LLM 诊断。
# 用法：
#   bash scripts/run_server_llm_diagnostics.sh
#   CONTAINER=ai-translation-app bash scripts/run_server_llm_diagnostics.sh --provider openrouter

set -u

CONTAINER="${CONTAINER:-ai-translation-app}"
LOCAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_SCRIPT="${LOCAL_SCRIPT_DIR}/diagnose_llm_runtime.py"
IN_IMAGE_SCRIPT="/app/scripts/diagnose_llm_runtime.py"
TMP_SCRIPT="/tmp/diagnose_llm_runtime.py"
DOCKER_EXEC_FLAGS=(-i)

if [ -t 0 ] && [ -t 1 ]; then
  DOCKER_EXEC_FLAGS=(-it)
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "错误：当前服务器找不到 docker 命令。"
  exit 127
fi

if ! docker inspect "${CONTAINER}" >/dev/null 2>&1; then
  echo "错误：找不到容器 ${CONTAINER}。"
  echo "提示：先执行 docker ps，确认应用容器名称；必要时用 CONTAINER=容器名 覆盖。"
  exit 1
fi

if docker exec "${CONTAINER}" test -f "${IN_IMAGE_SCRIPT}" >/dev/null 2>&1; then
  docker exec "${DOCKER_EXEC_FLAGS[@]}" "${CONTAINER}" python "${IN_IMAGE_SCRIPT}" "$@"
  exit $?
fi

if [ ! -f "${LOCAL_SCRIPT}" ]; then
  echo "错误：本地找不到 ${LOCAL_SCRIPT}，无法拷贝到容器。"
  exit 1
fi

echo "镜像内没有 ${IN_IMAGE_SCRIPT}，临时拷贝脚本到容器 ${TMP_SCRIPT} ..."
docker cp "${LOCAL_SCRIPT}" "${CONTAINER}:${TMP_SCRIPT}" || exit $?
docker exec "${DOCKER_EXEC_FLAGS[@]}" "${CONTAINER}" python "${TMP_SCRIPT}" "$@"
