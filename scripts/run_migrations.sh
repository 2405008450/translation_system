#!/usr/bin/env bash
# 幂等数据库迁移：按文件名顺序执行 scripts/migrations/ 下的所有 .sql。
# 所有脚本须使用 CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS 等幂等语句，
# 以便每次容器启动重复执行也安全（已存在则秒过）。
set -euo pipefail

: "${PGHOST:=postgres}"
: "${PGPORT:=5432}"
: "${PGUSER:?需要 PGUSER}"
: "${PGPASSWORD:?需要 PGPASSWORD}"
: "${PGDATABASE:?需要 PGDATABASE}"

MIGRATIONS_DIR="$(dirname "$0")/migrations"

echo "[migrate] 等待 PostgreSQL ${PGHOST}:${PGPORT} 就绪..."
until pg_isready -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" >/dev/null 2>&1; do
  sleep 2
done

echo "[migrate] 开始执行迁移脚本..."
shopt -s nullglob
for sql in $(ls -1 "$MIGRATIONS_DIR"/*.sql | sort); do
  echo "[migrate] -> $(basename "$sql")"
  psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -v ON_ERROR_STOP=1 -f "$sql"
done
echo "[migrate] 全部迁移完成。"
