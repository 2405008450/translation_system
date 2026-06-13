# Docker 生产部署说明

本项目推荐用独立 Compose 项目部署，避免影响服务器上已有的 `offline-deploy-*` 容器。当前方案只对公网发布 `19013`，PostgreSQL、Redis、LanguageTool 均留在本项目 Docker 内网。

## 1. 服务器准备

```bash
sudo ss -tulpn | grep 19013 || true
docker --version
docker compose version || docker-compose version
```

在云服务器安全组和系统防火墙中放行 TCP `19013`。

## 2. 配置环境变量

```bash
cp .env.prod.example .env.prod
nano .env.prod
```

必须替换：

- `POSTGRES_PASSWORD`
- `DATABASE_URL` 中的数据库密码
- `JWT_SECRET_KEY`
- 至少一个 LLM Key：`DEEPSEEK_API_KEY` 或 `OPENROUTER_API_KEY`

如果数据库密码包含 `@`、`:`、`/`、`?`、`#` 等字符，`DATABASE_URL` 里的密码部分必须 URL 编码。

## 3. 构建并启动

如果服务器支持新版插件命令 `docker compose`，使用：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml build
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
```

如果服务器只有独立命令 `docker-compose`，先复制一份默认 `.env`，再使用：

```bash
cp .env.prod .env

sudo docker-compose -f docker-compose.prod.yml config
sudo docker-compose -f docker-compose.prod.yml build
sudo docker-compose -f docker-compose.prod.yml up -d
sudo docker-compose -f docker-compose.prod.yml ps
```

首次创建数据库 volume 时会自动执行：

- `docker/postgres/init/00-extensions.sql`
- `docker/postgres/init/05-users-prereq.sql`
- `scripts/init_db.sql`
- `scripts/create_reference_profiles.sql`

这些初始化脚本只在 PostgreSQL volume 第一次创建时运行。

## 4. 验证

```bash
curl http://127.0.0.1:19013/api/health
curl http://43.132.156.72:19013/
docker compose --env-file .env.prod -f docker-compose.prod.yml logs --tail=100 app
docker compose --env-file .env.prod -f docker-compose.prod.yml logs --tail=100 worker
```

如果使用的是 `docker-compose` 命令：

```bash
sudo docker-compose -f docker-compose.prod.yml logs --tail=100 app
sudo docker-compose -f docker-compose.prod.yml logs --tail=100 worker
```

打开：

```text
http://43.132.156.72:19013/login
```

首次进入后初始化管理员账号。

## 5. 数据恢复策略

本次先按空库上线。后续如果要恢复数据库和文件：

1. 先停应用和 worker。
2. 备份当前 `postgres_data`、`app_file_storage`、`app_export_tasks` volume。
3. 按你提供的 dump 或连接信息恢复 PostgreSQL。
4. 同步 `data/file_records` 和 `data/export_tasks`。
5. 再启动服务并检查 `/api/health`。

仓库中的 `migration_20260605_105050` 是旧迁移包，可作为后续恢复来源，但不要在首次空库上线时自动覆盖。

数据库迁移和远程连接请看 [DATABASE_MIGRATION.md](DATABASE_MIGRATION.md)。默认推荐 SSH 隧道连接数据库，不建议把 PostgreSQL 裸露到公网。
