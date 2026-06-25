# Nginx 反向代理（可选）

默认部署**不启动** nginx（方案 A，直连 `app:19013`）。启用方式：

```bash
USE_NGINX=1 scripts/deploy_prod.sh up
# 80 被占用时换端口：
USE_NGINX=1 NGINX_HTTP_PORT=19080 scripts/deploy_prod.sh up
```

Compose 文件为独立的 `docker-compose.nginx.yml`（叠加启用），默认 prod 栈不含 nginx，与改版前一致。

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
