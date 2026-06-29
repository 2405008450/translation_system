# 数据库迁移与远程连接说明

## 结论

源数据库 `192.168.31.144` 是局域网地址，云服务器通常不能直接访问它。推荐流程是：

1. 在能访问 `192.168.31.144` 的电脑上导出数据库。
2. 把 dump 文件上传到云服务器。
3. 在云服务器 Docker Postgres 容器中恢复。
4. 远程维护数据库时使用 SSH 隧道，不把 Postgres 裸露到公网。

## 1. 推荐远程连接方式：SSH 隧道

先在服务器项目目录启用本机端口映射：

```bash
cd ~/opt/translation_system

sudo docker-compose \
  -f docker-compose.prod.yml \
  -f docker-compose.db-tunnel.yml \
  up -d postgres
```

这只会让服务器本机的 `127.0.0.1:15432` 访问到容器内 PostgreSQL，不会开放公网端口。

在你的电脑上建立 SSH 隧道：

```bash
ssh -L 15432:127.0.0.1:15432 ubuntu@43.132.156.72
```

然后本地数据库客户端连接：

```text
Host: 127.0.0.1
Port: 15432
Database: tm_demo
User: tm_user
Password: 使用服务器 .env.prod 中的 POSTGRES_PASSWORD
```

## 2. 修改服务器数据库密码

在服务器 `.env.prod` 中把 `POSTGRES_PASSWORD` 和 `DATABASE_URL` 的密码改成同一个高强度密码。

如果密码包含 `@`、`:`、`/`、`?`、`#`、`%` 等 URL 特殊字符，`DATABASE_URL` 中的密码部分必须 URL 编码。

容器已经初始化过以后，单改 `.env.prod` 不会自动修改数据库内用户密码，还需要执行：

```bash
cd ~/opt/translation_system

sudo docker-compose -f docker-compose.prod.yml exec postgres \
  psql -U postgres -d tm_demo \
  -c "ALTER USER tm_user WITH PASSWORD '替换成你的新强密码';"

sudo docker-compose -f docker-compose.prod.yml up -d app worker pretranslation-worker
```

## 3. 从局域网数据库导出

在能访问 `192.168.31.144` 的电脑上执行。下面默认源库名是 `tm_demo`，源库超级用户是 `postgres`；如果实际不同，请替换。

```bash
pg_dump \
  -h 192.168.31.144 \
  -U postgres \
  -d tm_demo \
  -Fc \
  -f tm_demo.dump
```

如果源库还有文件存储目录，也要一起打包，例如：

```bash
tar -czf file_storage.tar.gz data/file_records data/export_tasks
```

## 4. 上传到云服务器

```bash
scp tm_demo.dump ubuntu@43.132.156.72:~/opt/translation_system/
scp file_storage.tar.gz ubuntu@43.132.156.72:~/opt/translation_system/
```

## 5. 恢复到云服务器容器数据库

恢复会覆盖当前空库中的数据。先停应用和 worker：

```bash
cd ~/opt/translation_system

sudo docker-compose -f docker-compose.prod.yml stop app worker pretranslation-worker
```

重建空数据库并恢复 dump：

```bash
sudo docker-compose -f docker-compose.prod.yml exec postgres \
  psql -U postgres -d postgres \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'tm_demo';"

sudo docker-compose -f docker-compose.prod.yml exec postgres \
  dropdb -U postgres --if-exists tm_demo

sudo docker-compose -f docker-compose.prod.yml exec postgres \
  createdb -U postgres -O tm_user tm_demo

cat tm_demo.dump | sudo docker-compose -f docker-compose.prod.yml exec -T postgres \
  pg_restore --exit-on-error --verbose --no-owner --no-acl -U postgres -d tm_demo
```

恢复后补齐当前项目需要的扩展和新增表：

```bash
cat scripts/init_db.sql | sudo docker-compose -f docker-compose.prod.yml exec -T postgres \
  psql -U postgres -d tm_demo

cat scripts/create_reference_profiles.sql | sudo docker-compose -f docker-compose.prod.yml exec -T postgres \
  psql -U postgres -d tm_demo
```

如果上传了文件存储包：

```bash
tar -xzf file_storage.tar.gz
sudo docker cp data/file_records/. ai-translation-app:/app/data/file_records/
sudo docker cp data/export_tasks/. ai-translation-app:/app/data/export_tasks/ || true
```

最后启动并验证：

```bash
sudo docker-compose -f docker-compose.prod.yml up -d app worker pretranslation-worker
curl http://127.0.0.1:19013/api/health
```

## 6. 不建议裸露公网数据库

如果一定要公网直连 PostgreSQL，至少要满足：

- 云安全组只允许你的固定公网 IP 访问数据库端口。
- 不使用默认 `5432`，改用高位端口。
- 使用高强度密码，并定期轮换。
- 不使用超级用户远程日常操作。
- 优先配置 TLS；否则数据库业务流量不应穿越公网裸传。

默认部署不提供公网数据库端口，避免被全网扫描和暴力破解。
