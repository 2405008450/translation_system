   # Handoff：确认 / TM 检索 / 句段同步性能优化

> 交接给下一个 AI Agent（Codex）。**不要从零重做阶段一**；继续完成阶段二未接线代码，再视需要做阶段三。
>
> 关联计划：用户确认过的修订版「确认、TM 检索与句段同步性能优化」。
> **完整方案档案（原方案 + 偏差清单 + 修订执行版）**：[`确认_TM检索_句段同步_完整方案.md`](确认_TM检索_句段同步_完整方案.md) —— 执行时请对照，避免只看本 handoff 摘要而偏离。
> 生产机：`43.132.156.72`（SSH Host `tx-server`，用户 `ubuntu`）。
> 部署目录：`/home/ubuntu/opt/translation_system`，环境文件 `.env.prod`。

---

## 1. 当前任务目标

解决用户反馈的两类慢点：

1. **确认句段慢**：前端 1.5s 防抖 + 确认接口全文件统计 / `row_number()` / 有条件二次 commit；项目同步无去重，确认后同句段跨文件同步偏慢。
2. **预翻译 TM 检索慢**：记忆库列表扫 1300 万行 COUNT；模糊检索在小 `shared_buffers` 下读盘多（阶段一已调参）。

同时保证：**相同 `source_hash` 句段尽快同步到统一译文**（默认仅「确认」触发同步，避免未定稿译文扩散成两种译法）。

验收目标（计划）：

- 资源列表 `<200ms`
- 确认 API p95 `<300ms`
- 同步落库后约 1s 通知、2s 内界面可见
- 5 句模糊批次热缓存 p95 `<400ms`（阶段一后先复测；不达标再上 KNN）

---

## 2. 已完成内容

### 2.1 阶段一（生产已落地，勿重复）

在 `43.132.156.72` 已执行：

- **删除 3 个未使用大索引**（DDL 备份：`/home/ubuntu/index_backup_20260720.sql`）
  - `ix_memory_entries_source_text_trgm`
  - `ix_memory_entries_collection_source_normalized`
  - `ix_memory_entries_source_normalized`
  - `memory_entries` 约 39GB → 33GB；磁盘空闲约 72GB → 78GB
- **`ALTER SYSTEM` 持久化**（在数据卷 `postgresql.auto.conf`，与 compose 无关）：
  - `shared_buffers=8GB`、`effective_cache_size=40GB`、`work_mem=16MB`、`maintenance_work_mem=1GB`
  - `random_page_cost=1.1`、`effective_io_concurrency=200`、`max_wal_size=8GB`、`track_io_timing=on`
  - `shared_preload_libraries=pg_stat_statements`，扩展已 `CREATE EXTENSION`
- **compose 增加 `shm_size: 2gb`**（Docker 默认 64MB 会导致并行查询失败）
- postgres 容器已 recreate；app/worker 未强制重建，健康检查正常
- 模糊单句实测约 **54ms**（UTF-8 正确查询）；记忆库列表 COUNT 仍约 **2.8–5s**（必须靠 `entry_count` 持久化解决）

**未删索引（有意保留）**：`ix_memory_entries_external_tuid`、`ix_memory_entries_import_batch_id`（会被 `schema_setup.REQUIRED_INDEXES` 重建）、向量相关索引（重建成本高，`TM_VECTOR_ENABLED=false`）。

### 2.2 阶段二（代码半成品：迁移 + 部分后端；未接线完）

**迁移脚本已写好（未在生产执行）**：

| 文件 | 作用 |
|------|------|
| `scripts/migrations/0015_add_memory_bases_entry_count.sql` | `memory_bases.entry_count` + 语句级触发器 + 同事务回填 |
| `scripts/migrations/0016_projection_sync_conditional.sql` | 投影触发器仅 source/hash/语言/collection 变化时重建 |
| `scripts/migrations/0017_add_segment_display_confirm_stats.sql` | `segments.display_index` / `confirmed_at`；`file_segment_stats` + 触发器 + 回填 |
| `scripts/migrations/0018_create_project_segment_sync_outbox.sql` | `project_segment_sync_outbox` 表 |

**模型 / 配置 / 服务层已部分完成**：

- `app/models.py`：`Segment.display_index`、`Segment.confirmed_at`、`TMCollection.entry_count`、`FileSegmentStats`、`ProjectSegmentSyncOutbox`
- `app/config.py`：`project_sync_confirmed_only=True`、`segment_events_enabled=True`
- `app/services/file_record_service.py`：
  - `get_file_segment_status_counts` / `sync_file_record_status` 读统计表
  - `apply_segment_status`（维护 `confirmed_at`）
  - `refresh_segment_display_indexes`
  - `update_segment_*` / `batch_update_segments` 支持 `defer_commit`
- `app/services/project_segment_sync.py`：`sync_project_segments_for_hash`；冲突决胜用 `confirmed_at` + segment id
- **新文件** `app/services/project_sync_outbox.py`：outbox 入队 / 消费 / 发布事件
- **新文件** `app/services/segment_events.py`：Redis pub/sub 发布与异步订阅客户端工厂

**`app/routers/api.py` 已改一部分**：

- `_get_segment_display_index_map` 改读持久化 `display_index`
- `_apply_segment_display_range_filter` / workflow 范围过滤改用持久化列（**1-based 入参 → 0-based 列**）
- `_get_segment_status_stats` 优先读 `FileSegmentStats`

**前端：尚未改**（确认仍 1.5s 防抖；预翻译仍拉全量 collections；无 SSE）。

**阶段二尚未接线 / 未做**：

- api 保存链路改单事务 + outbox 入队 + 仅确认触发
- worker 改消费 outbox（`run_project_sync_outbox_once`）
- SSE 端点 + Redis 订阅
- collections 读 `entry_count` + 语言对过滤
- `tm_scope_mode`
- 拆分/合并后调用 `refresh_segment_display_indexes`
- 前端确认立即保存、SSE、预翻译 scope

---

## 3. 修改过哪些文件

### 本任务相关（应继续）

| 路径 | 状态 |
|------|------|
| `docker-compose.prod.yml` | 已改：`shm_size: 2gb`（本地 + 服务器均已改；服务器有备份 `docker-compose.prod.yml.bak20260720`） |
| `app/config.py` | 已改 |
| `app/models.py` | 已改 |
| `app/services/file_record_service.py` | 已改 |
| `app/services/project_segment_sync.py` | 已改 |
| `app/services/project_sync_outbox.py` | **新增** |
| `app/services/segment_events.py` | **新增** |
| `app/routers/api.py` | **部分改完，未完成接线；且当前有已知 import 断裂** |
| `scripts/migrations/0015_*.sql` ~ `0018_*.sql` | **新增，未上生产** |
| `frontend/src/stores/segment.ts` | **未改** |
| `frontend/src/components/PreTranslateDialog.vue` | **未改** |

### 工作区里存在、但与本任务无关（不要误当成进度）

- `frontend/src/locales/en-US.ts`、`zh-CN.ts`、`ProjectDetailView.vue`：git 显示已修改，**不是本性能任务写的**
- `.cursor/`、各类 `*.plan.md`、`DOCX完整解析...`、`migration_20260605_105050/`、`pytest.ini`、`docs/` 等：无关或历史产物

---

## 4. 为什么这么修改

| 决策 | 原因 |
|------|------|
| 先调 PG 内存 / 删废索引，再改业务代码 | 模糊检索慢主因是 128MB `shared_buffers` 装不下投影 GIN；调参即见效，且不需 build |
| `entry_count` + 语句级触发器 | 列表接口实测 5.77s COUNT；触发器同事务回填避免漏计 |
| 投影触发器条件化 | 仅改译文也会删重建投影 → GIN 写放大 |
| 持久化 `display_index` + `file_segment_stats` | 去掉确认/轮询里的全文件 `row_number()` 与实时聚合 |
| `confirmed_at` | 人工确认冲突需「最新确认」决胜；以前只有 `updated_at` |
| 项目同步 outbox 按 `(project, 语言对, source_hash)` 唯一键 | 连续确认产生大量重复 ARQ job；合并后同 hash 只收敛一次 |
| 默认仅确认触发同步 | 用户明确担心同句两种译法；配置 `PROJECT_SYNC_CONFIRMED_ONLY` 可回退 |
| Redis pub/sub + SSE，不用 DB LISTEN | 生产走 **pgbouncer transaction 模式**，不能持有会话做 NOTIFY |
| KNN/GiST 放到阶段三条件触发 | 阶段一后 GIN 可能已够用；且缺 `btree_gist`，复合 GiST 有前提 |

生产关键事实：

- `memory_entries` ~1304 万行；`memory_bases` 1052；zh-CN→en-US 633 个库
- `TM_SEARCH_PROJECTION_ENABLED=true`，`TM_FUZZY_MATCH_BATCH_SIZE=5`
- Auto-TM 已有 outbox；**项目同步原先没有**（直接 ARQ `arq:segment-sync`）
- 代码里**没有**现成的 `TM_FUZZY_SEARCH_STRATEGY`（阶段三才新建）

---

## 5. 还有哪些 TODO

按优先级：

1. **修断裂**：`api.py` 使用了 `FileSegmentStats`、`refresh_segment_display_indexes`，但 **未 import**（见第 6 节）。不修则相关路径 NameError。
2. **接线保存链路**（单句 PUT / 批量 PUT / confirmation）：
   - `defer_commit=True` 写句段
   - 同事务：`enqueue_confirmed_segments_for_auto_tm` + `enqueue_project_segment_sync`
   - **一次** `db.commit()`，再 `_schedule_*` worker
   - 仅确认（或配置允许）的句段入项目同步 outbox
   - commit 后可对当前文件 `publish_segment_changes`
3. **改 segment-sync worker**：`project_segment_sync_job` / `_dispatch_project_segment_sync` 改为调度 `run_project_sync_outbox_once`（或等价），不再按 segment_ids 直接同步为主路径。
4. **`list_tm_collections`**：读 `MemoryBase.entry_count`；加可选 `source_language`/`target_language` 过滤；`get_tm_collection` 同理。
5. **预翻译**：`PretranslationRunRequest` 增加 `tm_scope_mode=selected|language_pair_all`；全选时按语言对查库，不传 633 UUID。
6. **结构变更**（拆分/合并/导入）：成功后调用 `refresh_segment_display_indexes`。
7. **SSE**：`GET /file-records/{id}/segments/events`（`text/event-stream`）；订阅 `segment-events:file:{id}`；nginx 已有类似 SSE 先例需 `proxy_buffering off`。
8. **前端**：
   - 确认：`updateTarget(..., {confirm:true})` 立即 `syncToBackend()`，跳过 1.5s
   - 订阅 SSE，断线回退现有 10s 轮询 + burst
   - 预翻译：按语言对拉 collections；全选发 `tm_scope_mode=language_pair_all`
9. **生产执行迁移**：用现有部署流程跑 `db-migrate`（`scripts/run_migrations.sh` 顺序执行 `0015`–`0018`）。0015/0017 会短暂锁写入做回填——低峰执行；0015 期间可短停 `auto-tm-worker`/`import-worker`。
10. **build 部署**（用户给定命令）：

```bash
sudo docker-compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.proxy.yml -f docker-compose.nginx.yml build --no-cache app
sudo docker-compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.proxy.yml -f docker-compose.nginx.yml up --force-recreate db-migrate
sudo docker-compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.proxy.yml -f docker-compose.nginx.yml up -d --force-recreate --no-deps app import-worker worker auto-tm-worker segment-sync-worker pretranslation-worker nginx
```

11. **阶段三（条件）**：若热缓存 5 句模糊 p95 仍 >400ms → `CREATE EXTENSION btree_gist` + 投影分区 GiST（**不要**把 collection_id 放进 GiST 前导列）+ `TM_FUZZY_SEARCH_STRATEGY=knn`。

---

## 6. 已知 Bug / 半成品风险

1. **`api.py` import 断裂（高）**  
   使用了 `FileSegmentStats`、`refresh_segment_display_indexes`，但：
   - `from app.models import (...)` **没有** `FileSegmentStats`
   - `from app.services.file_record_service import (...)` **没有** `refresh_segment_display_indexes`  
   → 一进 `_get_segment_status_stats` / display_index 路径就 NameError。**下一步第一件事修这个。**

2. **迁移未上生产**  
   模型已有 `display_index`/`confirmed_at`/`entry_count`/`file_segment_stats`，但库表可能还没有 → 未迁移前部署新代码会炸。必须先 `db-migrate` 再起 app，或保证同一次 up 顺序正确。

3. **`list_tm_collections` 仍 COUNT 全表**  
   模型有 `entry_count`，接口未改。

4. **项目同步主路径仍是旧 ARQ job**  
   `project_sync_outbox.py` 存在但 api/worker **未调用**；旧 `_schedule_project_segment_sync_for_segments` 仍按 segment_ids 投递。

5. **`defer_commit` 未在 API 使用**  
   服务层有参数，路由仍走旧双 commit（有 Auto-TM 时）。

6. **批量确认未写 `confirmed_at`**  
   `batch_update_segment_confirmation` 仍直接 `segment.status = ...`，未走 `apply_segment_status`。

7. **`display_index` 与 UI 范围**  
   API/UI 范围是 **1-based**；持久化列是 **0-based**。范围过滤已减 1。其它仍用 `row_number()` 的路径（如部分 next-unconfirmed / 合并视图）需逐个核对，避免 off-by-one。

8. **统计表与可见性过滤**  
   `_get_segment_status_stats` 读全文件统计；`/segments/changes` 若对「可见子集」过滤，全文件 stats 可能与旧行为不一致（任务分配可见性）。需确认产品是否接受，或仅在无过滤时用统计表。

9. **PowerShell 测 SQL 曾把中文变成 `???`**  
   导致误判全表扫描 11s；真实 UTF-8 查询约 54ms。测模糊检索务必保证 UTF-8。

10. **StrReplace 工具曾多次中断**  
    大文件 `api.py` 编辑不稳定；建议小块改或脚本补丁，改完立刻 `python -c "from app.routers import api"` 验 import。

---

## 7. 哪些地方千万不要改

1. **不要再调一次 postgres 内存参数 / 不要再删那三个索引**（阶段一已完成）。
2. **不要重建被删的废索引**；不要把它们加回 `schema_setup.REQUIRED_INDEXES`。
3. **不要覆盖用户工作区无关改动**：`ProjectDetailView.vue`、locales、以及用户其它未提交实验。`api.py` 可继续改，但只动本任务相关段落。
4. **不要默认启用 KNN/GiST**；先复测阶段一基线。
5. **不要给 GiST 加 collection_id 前导列**（633 库 `IN (...)` 用不上）。
6. **不要用 DB LISTEN/NOTIFY 做协同推送**（pgbouncer 事务池）。
7. **不要 force push / 改 git config / 无请求就 commit**。
8. **不要在业务高峰跑 0015/0017 回填**（会锁 `memory_entries`/`segments` 写入数秒到更久）。
9. **精确匹配继续走 `source_hash` B-tree**；不要改成「哈希索引类型」或拆掉唯一约束。
10. **生产重启命令必须带三个 compose 文件 + `.env.prod`**（见第 5 节）；不要只 `docker compose up app`。

---

## 8. 已经尝试失败 / 无效的方法

1. **`ssh root@43.132.156.72`**：失败。正确：`ssh tx-server`（ubuntu + `id_ed25519`）。
2. **PowerShell 里嵌套 `sudo docker exec ... $(sudo ...)`**：本机把 `sudo` 当 cmdlet；应整段远程 bash 执行。
3. **PowerShell heredoc `python - <<'EOF'`**：不支持；改用文件或 `python -c`。
4. **非 UTF-8 管道测中文 trigram**：查询变成 `???`，计划变成 Parallel Seq Scan 11s——**测量假象**。
5. **指望调参修好记忆库列表**：调参后 COUNT 仍数秒；必须 `entry_count`。
6. **原计划「DOCX 整文件重解析」是瓶颈**：实际只是 Word 自动编号前缀剥离，优先级应低。
7. **原计划「每次确认调用两次 `sync_file_record_status`」不准确**：函数内两个 COUNT 子查询；另有 `_get_segment_status_stats`。二次 commit 仅 Auto-TM 入队时发生。
8. **原计划 `TM_FUZZY_SEARCH_STRATEGY`**：代码里本不存在，是新配置。
9. **对 `file_records` 加 `(project_id, 语言对)` 索引**：表仅 ~2100 行，无必要。
10. **大段 StrReplace `api.py`**：多次被中断；应小步提交编辑。

---

## 9. 推荐下一步执行顺序

```text
A. 本地修断裂
   1) api.py import FileSegmentStats + refresh_segment_display_indexes
   2) python -c "from app.routers import api" 确认可导入
   3) 批量确认改为 apply_segment_status

B. 接完后端主路径（仍本地）
   4) 保存/确认：defer_commit + outbox 同事务 + 单次 commit + 调度
   5) SegmentSyncWorker → run_project_sync_outbox_once
   6) list_tm_collections 读 entry_count + 语言对过滤
   7) tm_scope_mode
   8) SSE endpoint + publish_segment_changes（commit 后）
   9) 拆分/合并后 refresh_segment_display_indexes

C. 前端
   10) 确认立即 sync
   11) SSE + 轮询回退
   12) 预翻译 scope_mode / 语言对请求

D. 上线
   13) 低峰：db-migrate（0015–0018）；必要时短停 auto-tm/import worker
   14) 用户给定的 build + force-recreate 命令
   15) 验收：列表延迟、确认延迟、同步可见性、模糊批次

E. 可选阶段三
   16) 仅当模糊 p95 不达标再上 GiST/KNN
```

### 关键代码锚点

- 前端防抖：`frontend/src/stores/segment.ts` → `AUTO_SYNC_DELAY_MS = 1500`，`scheduleSync`
- 批量保存：`app/routers/api.py` → `batch_update` ~13726
- 批量确认：`batch_update_segment_confirmation` ~13816
- 项目同步调度：`_schedule_project_segment_sync_for_segments` ~9682；worker `project_segment_sync_job` ~1354
- 轮询：`GET .../segments/changes` ~10892；前端 `CHANGE_POLL_INTERVAL_MS = 10000`
- TM 列表：`list_tm_collections` ~15309（仍 COUNT）
- Auto-TM 范例（照抄 outbox 模式）：`app/services/auto_tm_sync.py`
- 新 outbox：`app/services/project_sync_outbox.py`
- 新事件：`app/services/segment_events.py`

### 服务器速查

```bash
ssh tx-server
cd /home/ubuntu/opt/translation_system
# PG
sudo docker exec -i ai-translation-postgres psql -U tm_user -d tm_demo
# 参数是否仍生效
# show shared_buffers;  -- 应为 8GB
# 索引备份
# cat /home/ubuntu/index_backup_20260720.sql
```

---

## 10. 交接时状态一句话

**阶段一生产已完成。阶段二迁移与核心服务已写，但 api/worker/前端未接完，且 `api.py` 当前存在未 import 符号的硬错误——下一个 Agent 应从修 import 开始，然后按第 9 节顺序接线，最后低峰跑迁移并按用户命令 build 部署。**
