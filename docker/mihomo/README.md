# Mihomo / Clash 代理配置

这里放生产服务器上由 Clash 订阅生成的 `config.yaml`。

`config.yaml` 可能包含机场节点、订阅信息或访问凭据，已经被本目录的 `.gitignore` 忽略，也被项目 `.dockerignore` 排除，不要提交到 Git。

推荐在服务器项目根目录执行：

```bash
MIHOMO_SUBSCRIPTION_URL='你的 Clash 订阅链接' bash scripts/prepare_mihomo_config.sh
```

脚本会下载订阅并补齐本项目需要的入站配置：

```yaml
mixed-port: 7890
allow-lan: true
bind-address: "*"
mode: rule
log-level: info
external-controller: 127.0.0.1:9090
```

本项目通过 Docker 内网访问 `http://mihomo:7890`，`mihomo` 不映射宿主机端口，默认不会影响同一台服务器上的其它项目。
