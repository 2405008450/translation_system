#!/usr/bin/env bash
# 生产环境一键构建/启动脚本（在云服务器项目根目录执行）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE_FILES=(-f docker-compose.prod.yml)

if [[ ! -f "$ENV_FILE" ]]; then
  echo "缺少 $ENV_FILE，请先执行: cp .env.prod.example .env.prod && nano .env.prod" >&2
  exit 1
fi

if [[ "${USE_PROXY:-0}" == "1" ]]; then
  COMPOSE_FILES+=(-f docker-compose.proxy.yml)
fi

if [[ "${USE_NGINX:-0}" == "1" ]]; then
  COMPOSE_FILES+=(-f docker-compose.nginx.yml)
fi

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" "$@"
  else
    docker-compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" "$@"
  fi
}

usage() {
  cat <<'EOF'
用法: scripts/deploy_prod.sh <命令>

命令:
  build     构建 app 镜像（含前端）
  up        构建并后台启动（默认直连 app:19013，不启 nginx）
  restart   强制重建 app/worker（代码热更新常用）
  ps        查看服务状态
  logs      跟踪 app/worker 日志
  health    本机健康检查（app:19013；USE_NGINX=1 时额外检查 nginx）

环境变量:
  ENV_FILE=.env.prod   Compose 环境文件
  USE_PROXY=1          叠加 docker-compose.proxy.yml（Mihomo 代理）
  USE_NGINX=1          启用 nginx 反代（需空闲端口，见 NGINX_HTTP_PORT）

示例:
  cp .env.prod.example .env.prod && nano .env.prod
  scripts/deploy_prod.sh up
  USE_PROXY=1 scripts/deploy_prod.sh up
  USE_NGINX=1 NGINX_HTTP_PORT=19080 scripts/deploy_prod.sh up
EOF
}

cmd="${1:-up}"
shift || true

case "$cmd" in
  build)
    compose build "$@"
    ;;
  up)
    compose build "$@"
    compose up -d "$@"
    compose ps
    ;;
  restart)
    compose build app
    compose up --force-recreate db-migrate
    if [[ "${USE_NGINX:-0}" == "1" ]]; then
      compose up -d --force-recreate --no-deps app worker pretranslation-worker nginx
    else
      compose up -d --force-recreate --no-deps app worker pretranslation-worker
    fi
    compose ps
    ;;
  ps)
    compose ps "$@"
    ;;
  logs)
    if [[ "${USE_NGINX:-0}" == "1" ]]; then
      compose logs -f --tail=200 app worker pretranslation-worker nginx "$@"
    else
      compose logs -f --tail=200 app worker pretranslation-worker "$@"
    fi
    ;;
  health)
    if [[ "${USE_NGINX:-0}" == "1" ]]; then
      echo "== nginx :${NGINX_HTTP_PORT:-80} =="
      curl -fsS --max-time 10 "http://127.0.0.1:${NGINX_HTTP_PORT:-80}/api/health" || true
      echo
    fi
    echo "== app :${APP_PUBLISH_PORT:-19013} =="
    curl -fsS --max-time 10 "http://127.0.0.1:${APP_PUBLISH_PORT:-19013}/api/health" || true
    echo
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    compose "$cmd" "$@"
    ;;
esac
