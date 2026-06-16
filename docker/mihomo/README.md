# Mihomo / Clash 代理配置

这里放生产服务器上由 Clash 订阅生成的 `config.yaml`。

`config.yaml` 可能包含机场节点、订阅信息或访问凭据，已经被本目录的 `.gitignore` 忽略，也被项目 `.dockerignore` 排除，不要提交到 Git。

推荐在服务器项目根目录执行：

```bash
MIHOMO_SUBSCRIPTION_URL='你的 Clash 订阅链接' bash scripts/prepare_mihomo_config.sh
```

推荐给 OpenRouter 启用日本/美国节点故障转移：

```bash
MIHOMO_OPENROUTER_FALLBACK=true \
MIHOMO_SUBSCRIPTION_URL='你的 Clash 订阅链接' \
bash scripts/prepare_mihomo_config.sh
```

脚本会自动扫描订阅中的日本/美国节点，生成 `OpenRouter-Fallback` 策略组，并把 `openrouter.ai` 规则放到 `rules` 第一条。默认健康检查间隔为 `30` 秒，超时为 `2000` 毫秒。

如需调得更激进：

```bash
MIHOMO_OPENROUTER_FALLBACK=true \
MIHOMO_OPENROUTER_FALLBACK_INTERVAL=15 \
MIHOMO_OPENROUTER_FALLBACK_TIMEOUT=1500 \
MIHOMO_SUBSCRIPTION_URL='你的 Clash 订阅链接' \
bash scripts/prepare_mihomo_config.sh
```

如果订阅里的“自动选择”没有选到适合 OpenRouter 的节点，可以先在生成后的配置里找日本或美国节点名：

```bash
grep -nE 'name:.*(日本|东京|大阪|JP|Japan|美国|美國|US|USA|United States|洛杉矶|洛杉磯|硅谷|圣何塞)' docker/mihomo/config.yaml | head -50
```

然后用具体节点名或策略组名重新生成，并把 `openrouter.ai` 固定到该策略：

```bash
MIHOMO_OPENROUTER_POLICY='这里填日本或美国节点名' \
MIHOMO_SUBSCRIPTION_URL='你的 Clash 订阅链接' \
bash scripts/prepare_mihomo_config.sh
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
