# Docker 生产部署说明

本项目使用独立 Compose 项目部署。现在默认启用 nginx，对外发布 **HTTP 80**；`app` 只在 Docker 内网监听 `19013`，不再把宿主机公网端口绑定到 `19013`。

如需临时绕过 nginx，可设置 `USE_NGINX=0`，脚本会叠加 `docker-compose.app-port.yml`，默认把宿主机 `80` 直连到 `app:19013`。

```text
公网 :80 (nginx) ──► app:19013
                    ├── import-worker / worker (arq maintenance) / pretranslation-worker
                    ├── postgres / pgbouncer / redis / languagetool
                    └── 共享卷：file_records / export_tasks / import_tasks

可选 USE_NGINX=0：
公网 :80 (app 直连) ──► app:19013
```

## 1. 服务器准备

```bash
sudo ss -tulpn | grep -E ':80' || true
docker --version
docker compose version || docker-compose version
chmod +x scripts/deploy_prod.sh
```

安全组 / 防火墙放行：

| 端口 | 用途 | 默认方案 |
|------|------|------------|
| 80 | nginx HTTP 入口 | **公网放行** |
| 19013 | app 容器内端口 | 不对公网放行 |

## 2. 配置环境变量

```bash
cp .env.prod.example .env.prod
nano .env.prod
```

**必须替换：**

- `POSTGRES_PASSWORD`
- `DATABASE_URL` 中的数据库密码
- `JWT_SECRET_KEY`
- 至少一个 LLM Key：`DEEPSEEK_API_KEY` 或 `OPENROUTER_API_KEY`
- `CORS_ALLOW_ORIGINS` 中的公网 IP / 域名

**上传相关（已有默认值，一般无需改）：**

| 变量 | 默认 | 说明 |
|------|------|------|
| `UPLOAD_MAX_TOTAL_SIZE_MB` | 500 | 单次上传总大小 |
| `UPLOAD_MAX_FILES_PER_BATCH` | 50 | 单次文件数 |
| `UPLOAD_MAX_EXPANDED_FILES` | 100 | zip/rar 解压后文件数 |
| `IMPORT_TASK_DIR` | `/app/data/import_tasks` | 导入暂存（app/import-worker/worker/pretranslation-worker 共享卷） |
| `GUNICORN_TIMEOUT` | 600 | 大文件上传超时（秒） |

nginx 的 `client_max_body_size` 在 `docker/nginx/default.conf`，默认 **500m**，需与 `UPLOAD_MAX_TOTAL_SIZE_MB` 保持一致。

**队列并发（资源充足时可调）：**

| 变量 | 推荐起点 | 说明 |
|------|----------|------|
| `ARQ_IMPORT_MAX_JOBS` | 2 | 上传文件、记忆库、术语库、词汇表等导入队列并发，独立于 QA、自动 TM 等维护任务 |
| `ARQ_MAINTENANCE_MAX_JOBS` | 4 | QA、自动 TM 重匹配、项目重复句段同步等维护队列并发 |
| `ARQ_PRETRANSLATION_MAX_JOBS` | 2 | 项目预翻译 run 队列并发 |
| `PRETRANSLATION_RUN_FILE_CONCURRENCY` | 2 | 单个预翻译 run 内同时处理的文件数 |
| `LLM_MAX_CONCURRENCY` | 3 | 单个文件 LLM 阶段的模型请求并发 |
| `ARQ_MAX_JOBS` | 5 | 导入/维护队列未单独设置时的全局兜底值 |

LLM 预翻译的理论请求上限约为 `ARQ_PRETRANSLATION_MAX_JOBS × PRETRANSLATION_RUN_FILE_CONCURRENCY × LLM_MAX_CONCURRENCY`。若模型服务出现 429、超时或错误率升高，优先下调 `LLM_MAX_CONCURRENCY` 或 `PRETRANSLATION_RUN_FILE_CONCURRENCY`。

拼写/语法 QA 默认手动生成：`SPELLING_GRAMMAR_QA_AUTO_SCHEDULE=false`。不要轻易开启自动调度，否则保存/确认句段会持续产生 QA 任务并增加维护队列与数据库压力。

密码含 `@`、`:`、`/`、`?`、`#` 等字符时，`DATABASE_URL` 须 URL 编码。

### `.env.prod` 和 `.env`

| 文件 | 作用 |
|------|------|
| `.env.prod` | 生产权威配置，Compose 通过 `--env-file` 读取 |
| `.env` | 可选副本：`cp .env.prod .env`，兼容不带 `--env-file` 的旧命令 |

## 3. 构建并启动（推荐：nginx 80）

```bash
# 默认：启用 nginx，公网访问 http://<IP>/
scripts/deploy_prod.sh up

# 若需 Mihomo 代理访问 OpenRouter
USE_PROXY=1 scripts/deploy_prod.sh up
```

**80 被其他服务占用时，临时改 nginx 对外端口：**

```bash
NGINX_HTTP_PORT=19080 scripts/deploy_prod.sh up
```

**临时不用 nginx，直连 app：**

```bash
USE_NGINX=0 scripts/deploy_prod.sh up
# 如确实需要旧的 19013：
USE_NGINX=0 APP_PUBLISH_PORT=19013 scripts/deploy_prod.sh up
```

等价手动命令：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml build
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.nginx.yml up -d
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.nginx.yml ps
```

独立版 `docker-compose`：

```bash
cp .env.prod .env
sudo docker-compose -f docker-compose.prod.yml -f docker-compose.nginx.yml build
sudo docker-compose -f docker-compose.prod.yml -f docker-compose.nginx.yml up -d
```

### 代码更新后重建

```bash
git pull
scripts/deploy_prod.sh restart
# 或完整重建：
scripts/deploy_prod.sh build
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.nginx.yml up -d --force-recreate app import-worker worker pretranslation-worker nginx
```

## 4. 验证

```bash
scripts/deploy_prod.sh health

curl http://127.0.0.1/api/health
curl http://<公网IP>/

docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.nginx.yml logs --tail=100 app import-worker worker pretranslation-worker nginx
```

浏览器访问（将 IP 换成你的服务器）：

```text
http://43.132.156.72/
http://43.132.156.72/login
```

首次进入后初始化管理员账号。

## 5. 数据卷说明

| Volume | 路径 | 内容 |
|--------|------|------|
| `app_file_storage` | `/app/data/file_records` | 源文件持久化 |
| `app_export_tasks` | `/app/data/export_tasks` | 导出临时文件 |
| `app_import_tasks` | `/app/data/import_tasks` | 上传与资源导入暂存（app/import-worker/worker/pretranslation-worker **必须共享**） |
| `app_logs` | `/app/logs` | 应用日志 |
| `postgres_data` | — | PostgreSQL 数据 |
| `redis_data` | — | Redis AOF |

启动时 app 会自动创建目录并清理过期 `import_tasks`（TTL 默认 86400s）。

## 6. nginx 说明

配置文件：`docker/nginx/default.conf`

- 反向代理到 `app:19013`
- `client_max_body_size 500m` — 与上传总大小限制对齐
- `proxy_request_buffering off` — 大文件流式转发
- 超时 600s — 与 `GUNICORN_TIMEOUT` 对齐

HTTPS：当前仅 HTTP。接入 TLS 时可挂载证书到 nginx，或使用云厂商 LB 终结 SSL 后转发到 80。

## 7. 数据恢复

1. 停 app / import-worker / worker / pretranslation-worker / nginx
2. 备份 `postgres_data`、`app_file_storage`、`app_export_tasks`、`app_import_tasks`
3. 恢复 PostgreSQL dump 与文件卷
4. 启动并检查 `/api/health`

详见 [DATABASE_MIGRATION.md](DATABASE_MIGRATION.md)。

## 9. 从旧版 Docker 栈升级（保留数据）

本次上传改造**只新增** `app_import_tasks` 卷与环境变量，不改动 postgres / file_records / export_tasks 卷名。

```bash
git pull
# .env.prod 可不改：compose 已为 IMPORT_TASK_DIR / 上传限制提供默认值
# 若希望显式配置，从 .env.prod.example 合并新增项即可

docker compose --env-file .env.prod -f docker-compose.prod.yml build app
docker compose --env-file .env.prod -f docker-compose.prod.yml up --force-recreate db-migrate
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.nginx.yml up -d --force-recreate app import-worker worker pretranslation-worker nginx
```

**必须重建 app + import-worker + worker + pretranslation-worker**，否则新卷 `app_import_tasks` 不会挂载，ARQ 导入会报「暂存文件不存在」。

如同时叠加 Mihomo 代理，可使用完整 Compose 文件组合：

```bash
sudo docker-compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.proxy.yml -f docker-compose.nginx.yml build app
sudo docker-compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.proxy.yml -f docker-compose.nginx.yml up --force-recreate db-migrate
sudo docker-compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.proxy.yml -f docker-compose.nginx.yml up -d --force-recreate app import-worker worker pretranslation-worker nginx
```

## 10. 常见问题

**上传 413 / 502**

- 检查 nginx `client_max_body_size` 与 `UPLOAD_MAX_TOTAL_SIZE_MB`
- 检查 `GUNICORN_TIMEOUT` 是否足够

**ARQ 导入失败「暂存文件不存在」**

- 确认 import-worker、worker 和 pretranslation-worker 已挂载 `app_import_tasks` 卷（`docker-compose.prod.yml` 已配置）
- 重建 import-worker、worker 和 pretranslation-worker：`scripts/deploy_prod.sh restart`

**临时绕过 nginx，直连 app**

- `USE_NGINX=0 scripts/deploy_prod.sh up` 会叠加 `docker-compose.app-port.yml`，默认发布到宿主机 80。
- 如确实需要旧端口，再显式执行 `USE_NGINX=0 APP_PUBLISH_PORT=19013 scripts/deploy_prod.sh up`。

**nginx 改端口**

- 设置 `NGINX_HTTP_PORT`，例如 `NGINX_HTTP_PORT=19080 scripts/deploy_prod.sh up`。
