#!/usr/bin/env bash
# 在生产服务器上测试 PostgreSQL / PgBouncer 密码是否一致。
#
# 用法：
#   cd ~/opt/translation_system
#   bash scripts/test_prod_db_connection.sh
#   bash scripts/test_prod_db_connection.sh --password 'change-me-postgres-password'
#
# 可选：若直连 Postgres 成功但 PgBouncer 失败，可重建 pgbouncer：
#   sudo docker-compose --env-file .env.prod -f docker-compose.prod.yml up -d --force-recreate pgbouncer

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_ROOT}/.env.prod}"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJECT_ROOT}/docker-compose.prod.yml}"
OVERRIDE_PASSWORD=""

usage() {
  cat <<'EOF'
用法:
  bash scripts/test_prod_db_connection.sh [--password PASSWORD]

读取 .env.prod 中的 POSTGRES_* / DATABASE_URL，依次测试：
  1) 直连 postgres:5432
  2) 经 pgbouncer:6432
  3) app 容器内用 DATABASE_URL（Python/psycopg）
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --password)
      shift
      OVERRIDE_PASSWORD="${1:-}"
      if [ -z "${OVERRIDE_PASSWORD}" ]; then
        echo "错误：--password 需要参数"
        exit 1
      fi
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1"
      usage
      exit 1
      ;;
  esac
  shift
done

if [ ! -f "${ENV_FILE}" ]; then
  echo "错误：找不到 ${ENV_FILE}"
  exit 1
fi

if [ ! -f "${COMPOSE_FILE}" ]; then
  echo "错误：找不到 ${COMPOSE_FILE}"
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}")
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}")
else
  echo "错误：未找到 docker compose / docker-compose"
  exit 1
fi

if command -v sudo >/dev/null 2>&1 && ! docker ps >/dev/null 2>&1; then
  if [ "${EUID}" -ne 0 ]; then
    COMPOSE_CMD=(sudo "${COMPOSE_CMD[@]}")
  fi
fi

# shellcheck disable=SC1090
set -a
source "${ENV_FILE}"
set +a

POSTGRES_DB="${POSTGRES_DB:-tm_demo}"
POSTGRES_USER="${POSTGRES_USER:-tm_user}"
POSTGRES_PASSWORD="${OVERRIDE_PASSWORD:-${POSTGRES_PASSWORD:-}}"

mask_password() {
  local value="$1"
  local length="${#value}"
  if [ "${length}" -le 4 ]; then
    printf '%s' '****'
    return
  fi
  printf '%s****%s' "${value:0:2}" "${value: -2}"
}

extract_url_password() {
  python3 - <<'PY' "$1"
import sys
from urllib.parse import urlparse
parsed = urlparse(sys.argv[1])
print(parsed.password or "")
PY
}

extract_url_host_port() {
  python3 - <<'PY' "$1"
import sys
from urllib.parse import urlparse
parsed = urlparse(sys.argv[1])
host = parsed.hostname or ""
port = parsed.port or ""
print(f"{host}:{port}" if port else host)
PY
}

URL_PASSWORD=""
URL_TARGET=""
if [ -n "${DATABASE_URL:-}" ]; then
  URL_PASSWORD="$(extract_url_password "${DATABASE_URL}")"
  URL_TARGET="$(extract_url_host_port "${DATABASE_URL}")"
fi

echo "=== 配置摘要（${ENV_FILE}）==="
echo "POSTGRES_DB=${POSTGRES_DB}"
echo "POSTGRES_USER=${POSTGRES_USER}"
echo "POSTGRES_PASSWORD=$(mask_password "${POSTGRES_PASSWORD}")"
echo "DATABASE_URL target=${URL_TARGET:-<未设置>}"
echo "DATABASE_URL password=$(mask_password "${URL_PASSWORD}")"
echo "DATABASE_PGBOUNCER_TRANSACTION_MODE=${DATABASE_PGBOUNCER_TRANSACTION_MODE:-<未设置>}"
echo

if [ -z "${POSTGRES_PASSWORD}" ]; then
  echo "错误：POSTGRES_PASSWORD 为空。可用 --password 指定要测试的密码。"
  exit 1
fi

if [ -n "${URL_PASSWORD}" ] && [ "${URL_PASSWORD}" != "${POSTGRES_PASSWORD}" ]; then
  echo "警告：DATABASE_URL 中的密码与 POSTGRES_PASSWORD 不一致，app 会用 DATABASE_URL 里的密码连接。"
  echo
fi

run_psql() {
  local label="$1"
  local host="$2"
  local port="$3"
  shift 3
  echo "--- ${label} (${host}:${port}) ---"
  if "${COMPOSE_CMD[@]}" exec -T -e "PGPASSWORD=${POSTGRES_PASSWORD}" postgres \
    psql -h "${host}" -p "${port}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c 'SELECT 1 AS ok;' "$@"; then
    echo "结果: 成功"
    echo
    return 0
  fi
  echo "结果: 失败"
  echo
  return 1
}

DIRECT_OK=0
PGBOUNCER_OK=0
APP_OK=0

if run_psql "直连 Postgres" postgres 5432; then
  DIRECT_OK=1
fi

if run_psql "经 PgBouncer" pgbouncer 6432; then
  PGBOUNCER_OK=1
fi

echo "--- app 容器 DATABASE_URL（Python/psycopg）---"
if "${COMPOSE_CMD[@]}" ps --services --filter status=running 2>/dev/null | grep -qx app; then
  if "${COMPOSE_CMD[@]}" exec -T app python - <<'PY'
import os
import sys

url = os.environ.get("DATABASE_URL", "")
if not url:
    print("错误：app 容器内未设置 DATABASE_URL")
    sys.exit(1)

try:
    from sqlalchemy import create_engine, text

    connect_args = {"connect_timeout": 5}
    if os.environ.get("DATABASE_PGBOUNCER_TRANSACTION_MODE", "").lower() in {"1", "true", "yes"}:
        connect_args["prepare_threshold"] = None

    engine = create_engine(url, connect_args=connect_args)
    with engine.connect() as conn:
        value = conn.execute(text("SELECT 1")).scalar_one()
    print(f"SELECT 1 => {value}")
except Exception as exc:
    print(f"连接失败: {exc}")
    sys.exit(1)
PY
  then
    echo "结果: 成功"
    APP_OK=1
  else
    echo "结果: 失败"
  fi
else
  echo "跳过：app 容器未在运行"
fi
echo

echo "=== 诊断结论 ==="
if [ "${DIRECT_OK}" -eq 0 ]; then
  echo "• 直连 Postgres 失败：数据卷里的 tm_user 密码 ≠ 当前测试密码。"
  echo "  修复示例（把密码换成 .env.prod 里要用的值）："
  echo "  ${COMPOSE_CMD[*]} exec postgres psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} \\"
  echo "    -c \"ALTER USER ${POSTGRES_USER} WITH PASSWORD '${POSTGRES_PASSWORD}';\""
  echo "  然后：${COMPOSE_CMD[*]} up -d --force-recreate pgbouncer app worker pretranslation-worker"
elif [ "${PGBOUNCER_OK}" -eq 0 ]; then
  echo "• 直连成功但 PgBouncer 失败：常见是 PgBouncer 客户端认证配置问题（如 SASL/scram userlist 不匹配）。"
  echo "  1) 确认 docker-compose.prod.yml 中 pgbouncer 使用 AUTH_TYPE=plain（内网部署）"
  echo "  2) 重建：${COMPOSE_CMD[*]} up -d --force-recreate pgbouncer"
  echo "  3) 若仍失败，查看：${COMPOSE_CMD[*]} logs --tail=50 pgbouncer"
elif [ "${APP_OK}" -eq 0 ]; then
  echo "• DB 层成功但 app 失败：检查 DATABASE_URL 密码/主机是否与 POSTGRES_PASSWORD 一致。"
else
  echo "• 全部通过。若页面仍异常，查看 app 日志："
  echo "  ${COMPOSE_CMD[*]} logs --tail=80 app"
fi
