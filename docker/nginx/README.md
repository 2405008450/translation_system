# Nginx 反向代理

默认生产部署会启动 nginx，并对外发布 HTTP 80；`app:19013` 只作为 Docker 内网 upstream。

```bash
scripts/deploy_prod.sh up
# 80 被占用时换端口：
NGINX_HTTP_PORT=19080 scripts/deploy_prod.sh up
```

Compose 文件为独立的 `docker-compose.nginx.yml`，由 `scripts/deploy_prod.sh` 默认叠加。若临时不启 nginx，使用 `USE_NGINX=0 scripts/deploy_prod.sh up`，脚本会改为叠加 `docker-compose.app-port.yml` 直连 app。

## 与上传限制对齐

修改 `UPLOAD_MAX_TOTAL_SIZE_MB` 时，请同步修改本目录 `default.conf` 中的：

```nginx
client_max_body_size 1024m;
```

建议：`client_max_body_size` ≥ `UPLOAD_MAX_TOTAL_SIZE_MB`。

## HTTPS（可选）

1. 将证书挂载到 nginx 容器
2. 在 `default.conf` 增加 `listen 443 ssl` 与 `ssl_certificate` 配置
3. 或使用云 LB / CDN 在 nginx 前终结 TLS

当前仓库默认仅提供 HTTP 80，便于开箱部署。
