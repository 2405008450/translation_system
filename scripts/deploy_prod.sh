#!/usr/bin/env bash
# 生产环境一键构建/启动脚本（在云服务器项目根目录执行）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env.prod}"
USE_NGINX="${USE_NGINX:-1}"
COMPOSE_FILES=(-f docker-compose.prod.yml)

if [[ ! -f "$ENV_FILE" ]]; then
  echo "缺少 $ENV_FILE，请先执行: cp .env.prod.example .env.prod && nano .env.prod" >&2
  exit 1
fi

# 未显式指定时沿用现有应用容器的代理配置，避免重建容器后丢失网络出口。
USE_PROXY="${USE_PROXY:-auto}"
if [[ "$USE_PROXY" == "auto" ]]; then
  previous_compose_files="$(docker inspect ai-translation-app \
    --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' 2>/dev/null || true)"
  if [[ "$previous_compose_files" == *docker-compose.proxy.yml* ]]; then
    USE_PROXY=1
  else
    USE_PROXY=0
  fi
fi

if [[ "$USE_PROXY" != "0" && "$USE_PROXY" != "1" ]]; then
  echo "USE_PROXY 只支持 auto、0 或 1。" >&2
  exit 1
fi

if [[ "$USE_PROXY" == "1" ]]; then
  COMPOSE_FILES+=(-f docker-compose.proxy.yml)
  echo "网络代理：启用 Mihomo（保留或显式指定的代理配置）。"
fi

if [[ "$USE_NGINX" == "1" ]]; then
  COMPOSE_FILES+=(-f docker-compose.nginx.yml)
else
  COMPOSE_FILES+=(-f docker-compose.app-port.yml)
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
  up        构建并后台启动（默认启用 nginx，对外发布 80）
  restart   强制重建 app/import-worker/worker/auto-tm-worker/segment-sync-worker（代码热更新常用）
  ps        查看服务状态
  logs      跟踪 app/import-worker/worker/auto-tm-worker/segment-sync-worker 日志
  health    本机健康检查（默认检查 nginx:80；USE_NGINX=0 时检查直连端口）

环境变量:
  ENV_FILE=.env.prod   Compose 环境文件
  USE_PROXY=auto       默认沿用现有应用容器的代理配置；首次部署不启用
  USE_PROXY=1          显式启用 docker-compose.proxy.yml（Mihomo 代理）
  USE_PROXY=0          显式关闭代理
  USE_NGINX=0          不启 nginx，改用 docker-compose.app-port.yml 直连 app
  NGINX_HTTP_PORT=80   nginx 对外 HTTP 端口
  APP_PUBLISH_PORT=80  USE_NGINX=0 时 app 直连发布端口

示例:
  cp .env.prod.example .env.prod && nano .env.prod
  scripts/deploy_prod.sh up
  USE_PROXY=1 scripts/deploy_prod.sh up
  NGINX_HTTP_PORT=19080 scripts/deploy_prod.sh up
  USE_NGINX=0 APP_PUBLISH_PORT=19013 scripts/deploy_prod.sh up
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
    if [[ "$USE_PROXY" == "1" ]]; then
      compose up -d --no-deps mihomo
    fi
    compose up --force-recreate db-migrate
    if [[ "$USE_NGINX" == "1" ]]; then
      compose up -d --force-recreate --no-deps app import-worker worker auto-tm-worker segment-sync-worker pretranslation-worker nginx
    else
      compose up -d --force-recreate --no-deps app import-worker worker auto-tm-worker segment-sync-worker pretranslation-worker
    fi
    compose ps
    ;;
  ps)
    compose ps "$@"
    ;;
  logs)
    if [[ "$USE_NGINX" == "1" ]]; then
      compose logs -f --tail=200 app import-worker worker auto-tm-worker segment-sync-worker pretranslation-worker nginx "$@"
    else
      compose logs -f --tail=200 app import-worker worker auto-tm-worker segment-sync-worker pretranslation-worker "$@"
    fi
    ;;
  health)
    if [[ "$USE_NGINX" == "1" ]]; then
      echo "== nginx :${NGINX_HTTP_PORT:-80} =="
      curl -fsS --max-time 10 "http://127.0.0.1:${NGINX_HTTP_PORT:-80}/api/health" || true
      echo
    else
      echo "== app :${APP_PUBLISH_PORT:-80} =="
      curl -fsS --max-time 10 "http://127.0.0.1:${APP_PUBLISH_PORT:-80}/api/health" || true
      echo
    fi
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    compose "$cmd" "$@"
    ;;
esac
