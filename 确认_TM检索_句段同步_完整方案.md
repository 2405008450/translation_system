# 确认、TM 检索与句段同步性能优化 — 完整方案档案

> 用途：给后续执行 Agent 对照，避免只看半成品代码或 handoff 摘要而偏离目标。
>
> 结构：
> 1. **你最初提交的完整方案（原文）**
> 2. **对照当前系统后的偏差 / 风险清单**
> 3. **已确认的修订执行版（应以本版为准实施）**
> 4. **执行进度快照（截至 handoff 时）**
>
> 配套文件：
> - 执行交接：[`handoff.md`](handoff.md)
> - Cursor 计划源：`C:\Users\Administrator\.cursor\plans\确认与tm检索性能优化_f0fa1851.plan.md`

---

## 1. 最初提交的完整方案（原文）

以下为对话开始时用户给出的待执行计划原文，保留完整意图，便于对照“为什么修订”。

### 1.1 现状结论

- “确认句段”慢并非同步写入 1304 万条 TM：TM 入库和项目同步已走后台队列。主要延迟来自前端固定 `1.5 秒` 保存等待，以及确认接口内的全文件状态统计、`row_number()` 排序、DOCX 编号解析和两次事务提交。
- 记忆库列表接口每次扫描全部 `memory_entries` 并分组计数，生产日志耗时约 `6.2 秒`。
- 当前投影表 GIN 索引虽被命中，但单句模糊检索仍约 `236ms`，5 句批次约 `1.0–1.6 秒`，单次读取约 1.57 万个缓冲页。
- 协同轮询会重复对大文件执行全量 `row_number()`，生产日志中耗时 `1.1–3.0 秒`。
- PostgreSQL 只有 `128MB shared_buffers`，没有 `pg_stat_statements`，与 61GB 内存和当前数据规模明显不匹配。

### 1.2 实现改动

#### 确认链路

- 确认操作继续采用前端乐观更新，但立即触发保存，不再等待普通编辑的 `1.5 秒` 防抖；连续确认使用短时间合并和串行批次，上一批完成后立即发送下一批。
- 重构句段保存事务：句段、修订记录、统计增量、Auto-TM outbox 和项目同步 outbox 在同一事务提交，移除当前二次提交。
- 保存响应只返回发生变化的句段字段，不再重新解析 DOCX、不再计算全文件 `row_number()`。
- 新增持久化 `display_index`，并建立：
  - `(file_record_id, workflow_step_id, display_index)`
  - `(file_record_id, updated_at, id)`
- 新增文件句段统计表，持久化总数、精确、模糊、无匹配、已确认和空译文数量；由数据库触发器维护，批量任务按语句聚合增量，接口不再实时扫描整个文件。
- `sync_file_record_status` 改为读取持久化统计，不再每次确认执行两次全文件计数。

#### TM 资源与检索

- 给 `memory_bases` 增加持久化 `entry_count`；使用语句级触发器按批次维护，替换所有 `COUNT(memory_entries)` 资源列表查询。
- `GET /translation-memory/collections` 增加可选语言对过滤参数；预翻译弹窗只请求当前语言对。
- 预翻译请求增加兼容字段 `tm_scope_mode=selected|language_pair_all`。全选时不再发送 633 个 UUID；旧客户端的 `tm_collection_ids` 继续支持。
- 精确匹配保留现有哈希索引链路。
- 为投影表各长度分区并发创建多列 GiST/KNN 索引，范围包含语言对、collection 和 `source_normalized gist_trgm_ops`；查询改用距离排序获取少量候选，再沿用现有 `SequenceMatcher` 和分数上限做最终判定。
- 按最终匹配阈值推导更紧的长度边界；阈值 0.8 时约为原文长度的 `0.67–1.5`，同时保留安全边距。
- 投影同步触发器只在原文、哈希、语言或 collection 改变时更新；仅修改译文不再删除并重建投影记录。
- KNN 由 `TM_FUZZY_SEARCH_STRATEGY=knn` 控制，原 GIN 查询保留为快速回滚路径，至少观察 7 天后再评估旧索引。

#### 实时句段同步

- 新增持久化项目同步 outbox，以“项目、语言对、source_hash”为唯一键合并重复任务，防止连续确认产生大量重复队列作业。
- worker 每批锁定并处理多个 source_hash；先取得项目文件 ID，再使用现有 `(file_record_id, source_hash)` 索引查找目标。
- 增加 `file_records(project_id, source_language, target_language, id)` 索引。
- 冲突优先级固定为：最新人工确认 > 人工未确认 > LLM > TM > 项目同步；两个不同的人工确认译文冲突时，以最新确认时间和句段 ID 确定唯一结果。
- 同步提交后通过 Redis 发布文件变更事件，前端使用 SSE 接收并拉取增量句段；断线时自动回退到低频游标轮询。
- `/segments/changes` 默认不返回全文件统计，不解析无变化文件，不使用运行时 `row_number()`；目标为同步落库后约 1 秒通知、2 秒内界面可见。

### 1.3 数据库与上线

- PostgreSQL 调整为保守生产参数：`shared_buffers=8GB`、`effective_cache_size=40GB`、`work_mem=16MB`、`maintenance_work_mem=1GB`、`random_page_cost=1.1`、`effective_io_concurrency=200`、`max_wal_size=8GB`、`track_io_timing=on`。
- 启用 `pg_stat_statements`，需要低峰期短暂重启一次 PostgreSQL。
- 先部署兼容代码和统计字段，再短暂阻止 TM 写入完成 `entry_count` 初始统计，预计只持续数秒且不影响读取。
- GiST 索引按分区逐个 `CREATE INDEX CONCURRENTLY`；当前磁盘剩余约 72GB，低于 40GB 时自动停止后续构建。
- 更新部署文档，加入确认延迟、同步 outbox 年龄、TM 阶段耗时、索引命中和慢 SQL 检查命令。

### 1.4 测试与验收

- 验证 TM 条目插入、删除、移动 collection、批量导入后 `entry_count` 始终准确。
- 使用生产抽样句段对比旧 GIN 与 KNN：精确匹配必须完全一致；模糊匹配首选结果一致率至少 `99.5%`，不达标不启用 KNN。
- 基准目标：资源列表低于 `200ms`；1–10 个库的 5 句模糊批次热缓存 p95 低于 `400ms`；确认 API p95 低于 `300ms`。
- 模拟 40 个并发用户连续确认，验证请求无明显排队、outbox 可合并、同步无丢失、人工确认不会被低优先级结果覆盖。
- 验证 SSE 断线重连、Redis 暂时不可用、worker 重启、重复任务和并发冲突均可通过数据库游标/outbox 恢复。
- 保留 GIN、旧轮询和配置回滚开关，确认稳定后再清理无用索引。

### 1.5 默认假设

- 采用实时 SSE 同步方案。
- 常见场景选择 1–10 个记忆库，同时完整支持语言对内全选。
- 允许低峰期短暂重启 PostgreSQL，并在线并发构建索引。
- 实施时保留当前工作区已有未提交修改，尤其不覆盖 `app/routers/api.py` 中的用户改动。

---

## 2. 对照当前系统后的偏差 / 风险清单

以下是代码调研 + SSH 实测后，相对「第 1 节原文」必须修正的点。**执行时以第 3 节修订版为准，不要机械照搬第 1 节原文中已否决的条目。**

| # | 原文说法 | 实际 / 修订 |
|---|---|---|
| 1 | `TM_FUZZY_SEARCH_STRATEGY` 已存在 | **不存在**，需新建；现有开关是 `TM_SEARCH_PROJECTION_ENABLED`（生产已 true） |
| 2 | GiST 多列含 collection + 语言对 | 服务器只有 `btree_gin`，**没有 `btree_gist`**；且 `gist_trgm_ops` 需指定 `siglen`；**不要把 collection_id 放 GiST 前导列**（633 库 `IN` 用不上） |
| 3 | 全选发 633 UUID 是主问题 | 前端全选已按语言对过滤选中集；但仍应加 `tm_scope_mode=language_pair_all` 避免传超长 UUID 列表 |
| 4 | 以“最新确认时间”决胜 | `segments` **没有 `confirmed_at`**，需新增；当前用 `updated_at` |
| 5 | 仅确认触发同步 | 现网：**有译文的普通保存也会调度项目同步**；修订版默认收敛为仅确认，配置 `PROJECT_SYNC_CONFIRMED_ONLY` |
| 6 | “两次事务提交”“DOCX 编号解析”是主因 | 二次 commit **仅 Auto-TM 入队时**；DOCX 实际是自动编号前缀剥离，**非整文件重解析**，优先级应低 |
| 7 | 项目同步已有类似 outbox | **没有**；只有 Auto-TM 有 outbox；项目同步直接投 ARQ |
| 8 | 只改 PG 参数即可 | compose **未设 `shm_size`**（默认 64MB），上 8GB shared_buffers + 并行查询会炸；必须加 `shm_size: 2gb` |
| 9 | SSE 可用 DB NOTIFY | 生产 **pgbouncer transaction 池**，不能持有会话；必须用 **Redis pub/sub + SSE** |
| 10 | “短暂阻止 TM 写入”即可回填 | 还需短停 **auto-tm-worker / import-worker**，否则 outbox 消费会继续写库 |
| 11 | 先建 GiST 再清索引 | 磁盘 88%；`memory_entries` 上约 **7GB idx_scan=0** 废索引应**先删**再考虑新索引 |
| 12 | 建 `file_records(project_id, 语言对, id)` | 表仅 ~2100 行，**无意义，删掉这条** |
| 13 | “精确匹配走哈希索引” | 语义上走 `source_hash` 字段，索引类型是 **B-tree**，不是 `USING HASH` |
| 14 | 投影表默认开启 | 代码默认 `False`，**生产 env 已设 true** |
| 15 | KNN 与调参并行上 | **先调参复测**；热缓存 5 句 p95 仍 >400ms 再上 KNN，避免一次引入过多变量 |

实测补充（阶段一前）：

- 列表 COUNT：约 **5.77s**
- 投影 GIN 单句：约 **55ms**（UTF-8 正确时）；缓冲页读取量接近旧 shared_buffers 总量
- PowerShell 非 UTF-8 测中文会变成 `???`，误判成 11s 全表扫描——**测量假象，勿信**

---

## 3. 已确认的修订执行版（实施真源）

用户已选择先做阶段一，并同意按修订版推进。**代码实现与上线以本节为准。**

### 3.1 阶段一：数据库配置与瘦身（不改业务代码）

- postgres：`shm_size: 2gb`
- `ALTER SYSTEM`（或等价）：`shared_buffers=8GB`、`effective_cache_size=40GB`、`work_mem=16MB`、`maintenance_work_mem=1GB`、`random_page_cost=1.1`、`effective_io_concurrency=200`、`max_wal_size=8GB`、`track_io_timing=on`、`shared_preload_libraries=pg_stat_statements`
- 低峰重启 postgres 一次
- 删除确认无用的大索引（先备份 DDL）：至少
  - `ix_memory_entries_source_text_trgm`
  - `ix_memory_entries_collection_source_normalized`
  - `ix_memory_entries_source_normalized`
  - （可选评估）`external_tuid` / embedding 相关——若会被 `schema_setup.REQUIRED_INDEXES` 重建则勿删
- 重启后复测模糊批次，建立是否做阶段三的基线

### 3.2 阶段二：应用层改造

#### 确认链路

- 前端：确认跳过 1.5s 防抖，立即 `syncToBackend`；普通编辑保持防抖；连续确认串行合并
- 后端：句段 + 修订 + Auto-TM outbox + 项目同步 outbox **同事务一次 commit**；commit 后再调度 worker
- 持久化 `display_index` + `confirmed_at`；索引 `(file_record_id, display_index)`、`(file_record_id, updated_at, id)`（workflow_step_id 可空，建复合索引时注意）
- 导入 / 拆分 / 合并后刷新 `display_index`
- `file_segment_stats` 表 + 语句级触发器；`sync_file_record_status` / `_get_segment_status_stats` 读统计表
- 保存响应只回变化字段；Word 编号剥离保留

#### TM 资源与检索

- `memory_bases.entry_count` + 语句级触发器；回填时短停 auto-tm / import worker
- collections 接口读 `entry_count` + 可选语言对过滤
- `tm_scope_mode=selected|language_pair_all`；兼容旧 `tm_collection_ids`
- 投影触发器仅 source/hash/语言/collection 变化时重建

#### 实时句段同步

- `project_segment_sync_outbox`，唯一键 `(project_id, source_language, target_language, source_hash)`
- worker：`FOR UPDATE SKIP LOCKED` 批量消费；按 hash 收敛同步
- 默认 **仅确认触发**（`project_sync_confirmed_only=True`）
- 冲突：confirmed > manual > llm > tm > project_sync；确认间用 `confirmed_at` + segment id
- Redis pub/sub → SSE；断线回退游标轮询
- `/segments/changes` 用持久化 `display_index`，去掉全量 `row_number()`
- **不建** `file_records(project_id, 语言对)` 索引

### 3.3 阶段三（条件触发）：GiST / KNN

**仅当阶段一后 5 句热缓存 p95 仍 >400ms：**

- `CREATE EXTENSION btree_gist`
- 各投影分区 `CREATE INDEX CONCURRENTLY`：`(source_language, target_language, source_normalized gist_trgm_ops(siglen=128))`，**不含 collection_id**
- 配置 `TM_FUZZY_SEARCH_STRATEGY=gin|knn`；`<->` 取候选 + `SequenceMatcher` 终判
- 长度边界按请求阈值推导（注意短句阈值降到 0.3）
- 磁盘 <40GB 停止后续构建；GIN 留 7 天；首选一致率 ≥99.5% 才切 KNN

### 3.4 验收与上线顺序

- entry_count 准确；列表 <200ms
- 确认 API p95 <300ms；确认后其它文件同句 <2s 可见
- 40 并发确认：outbox 合并、无丢失、人工确认不被低优先级覆盖
- SSE / Redis / worker 故障可恢复
- 顺序：**阶段一 → 阶段二 → 观察 → 阶段三按需**
- 回滚：保留 GIN、旧轮询、配置开关

### 3.5 默认假设（修订后）

- 允许低峰重启 PG 一次
- 项目同步默认仅确认触发
- 删索引前二次确认 `idx_scan` 并备份 DDL
- 不覆盖工作区无关未提交改动

### 3.6 生产部署命令（用户给定）

```bash
sudo docker-compose --env-file .env.prod \
  -f docker-compose.prod.yml \
  -f docker-compose.proxy.yml \
  -f docker-compose.nginx.yml \
  build --no-cache app

sudo docker-compose --env-file .env.prod \
  -f docker-compose.prod.yml \
  -f docker-compose.proxy.yml \
  -f docker-compose.nginx.yml \
  up --force-recreate db-migrate

sudo docker-compose --env-file .env.prod \
  -f docker-compose.prod.yml \
  -f docker-compose.proxy.yml \
  -f docker-compose.nginx.yml \
  up -d --force-recreate --no-deps \
  app import-worker worker auto-tm-worker segment-sync-worker pretranslation-worker nginx
```

环境：`.env.prod`；目录：`/home/ubuntu/opt/translation_system`；SSH：`tx-server`（ubuntu）。

---

## 4. 执行进度快照（截至 handoff 时）

### 已完成

- **阶段一已在生产落地**：PG 参数、`shm_size`、删 3 个废索引、`pg_stat_statements`；模糊单句约 54ms；列表 COUNT 仍慢（待 entry_count）
- 迁移脚本已写：`0015`–`0018`（**未在生产执行**）
- 模型 / config / `file_record_service` / `project_segment_sync` / 新文件 `project_sync_outbox.py`、`segment_events.py` 已部分完成
- `api.py` 已改 display_index 读取、范围过滤、统计表优先读取

### 未完成（详见 handoff.md）

- api/worker/前端接线未完
- `api.py` 存在 **FileSegmentStats / refresh_segment_display_indexes 未 import** 硬错误
- collections 仍 COUNT；无 SSE；确认仍 1.5s 防抖；同步仍走旧 ARQ 路径

### 执行时请同时打开

1. 本文档（完整方案，防偏差）
2. [`handoff.md`](handoff.md)（当前代码状态与下一步顺序）

**一句话：第 1 节是原始意图；第 2 节是必须避开的坑；第 3 节才是该落地的方案；第 4 节是目前做到哪。**
