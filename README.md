# AI Translation System

基于 **FastAPI + Vue 3 + PostgreSQL** 的全栈翻译记忆（TM）工作台，支持：

- DOCX 上传解析、句段切分、TM 命中（精确 + trigram 模糊 + pgvector 语义）
- 翻译工作台：段落 / 表格感知编辑、译文预览、DOCX 导出
- LLM 二次润色修正（DeepSeek / OpenRouter，支持多 provider fallback 与 SSE 流式进度）
- 多记忆库（TM Collections）与 XLSX 批量导入
- 句段级批注（带锚点 + 嵌套回复 + 解决状态）
- 用户 / 管理员体系（JWT + 首次安装初始化管理员）

---

## 目录结构

```text
app/                          # FastAPI 后端
  main.py                     # 入口：加载 SPA + 挂载 /api
  config.py                   # pydantic-settings 配置
  database.py                 # SQLAlchemy Engine
  models.py                   # ORM 模型
  schemas.py                  # 请求/响应 schema
  auth.py                     # JWT + 口令校验
  routers/
    auth.py                   # /api/auth/*
    api.py                    # /api/* 业务端点
  services/
    file_parser.py            # 通用文本解析
    slate_parser.py           # DOCX 结构化解析
    sentence_splitter.py      # 句段切分
    normalizer.py             # 文本归一化 / 哈希
    matcher.py                # TM 精确 + 模糊匹配
    tm_vector.py              # pgvector 语义检索
    tm_importer.py            # XLSX 导入
    file_record_service.py    # 文档/句段 CRUD
    document_workspace.py     # 工作台装配
    document_storage.py       # 源 DOCX 存储
    document_exporter.py      # 导出译文 DOCX
    comment_service.py        # 批注逻辑
    llm_service.py            # LLM 调用 + 多 provider
    cache.py                  # Redis + 内存 fallback
frontend/                     # Vue 3 + Vite 前端
  src/
    views/                    # 登录 / 工作台 / TM / 用户管理
    components/               # 预览、编辑器、批注等组件
    stores/                   # Pinia stores
    router/                   # 前端路由
scripts/
  init_db.sql                 # 一次性建表脚本（推荐使用）
  insert_test_data.sql        # 可选：塞一批示例 TM
  import_tm.py                # 命令行：CSV 导入 TM
  import_tm_xlsx.py           # 命令行：XLSX 导入 TM
  rebuild_tm_fields.py        # 重建 source_hash / normalized
  rebuild_tm_embeddings.py    # 重建 / 回填 pgvector embedding
  deduplicate_tm_source_hash.py  # 按 source_hash 去重
  add_tm_pgvector_support.sql    # 旧库补 pgvector 列（迁移用）
  create_document_tables.sql     # 旧库补建文档相关表（迁移用）
  rename_documents_to_file_records.sql  # 旧库 documents→file_records 改名
  migrate_primary_keys_to_uuid.sql      # 旧库主键迁移到 UUID
requirements.txt
.env.example
```

> 仅新部署：`scripts/init_db.sql` 已经是全量脚本，其他 `scripts/*.sql` 只在存量库迁移时才需要。

---

## 1. 环境准备

| 组件 | 版本 | 备注 |
| --- | --- | --- |
| Python | 3.11+ | 后端 |
| Node.js | 18+（推荐 20 LTS） | 前端构建 |
| PostgreSQL | 14+ | 需安装 `pg_trgm` 与 `pgvector` 扩展 |
| Redis | 5+（可选） | 缓存；未配置时自动回退到内存 |

### 1.1 安装 PostgreSQL 扩展

Windows 可直接使用 PostgreSQL 官方安装包，`pg_trgm` 自带；`pgvector` 需要额外安装：

- 官方安装指引：https://github.com/pgvector/pgvector#installation
- Windows 推荐下载 [pgvector 的 release zip](https://github.com/pgvector/pgvector/releases)，把 `vector.dll` / `vector--*.sql` / `vector.control` 按扩展名放到 PostgreSQL 对应目录（`lib/` 与 `share/extension/`）。

扩展本身的 `CREATE EXTENSION` 会在初始化 SQL 里执行，不用提前手动创建。

---

## 2. 克隆仓库

```bash
git clone <your-repo-url>.git
cd AI_translation_system12312
```

---

## 3. 数据库初始化

### 3.1 创建数据库与账号（超级用户下执行，只需一次）

```sql
-- 以 postgres 超级用户登录 psql 后执行
CREATE USER tm_user WITH PASSWORD 'tm123456';
CREATE DATABASE tm_demo OWNER tm_user;
GRANT ALL PRIVILEGES ON DATABASE tm_demo TO tm_user;
```

### 3.2 建表 + 扩展 + 索引（用超级用户连接 tm_demo 执行）

```powershell
psql -U postgres -d tm_demo -f scripts/init_db.sql
```

> `CREATE EXTENSION pg_trgm / vector` 需要超级用户；建表后可以把日常读写权限交给 `tm_user`。

`scripts/init_db.sql` 幂等，可以多次运行。涉及的表：

- `tm_collections`：TM 记忆库分组
- `translation_memory`：翻译记忆条目（含 trigram + pgvector 双路索引）
- `file_records`：上传的文件 / 翻译任务记录
- `segments`：句段及匹配状态
- `users`：用户 / 角色（`admin` / `user`）
- `segment_comments`：句段批注与嵌套回复

### 3.3（可选）导入示例 TM

```powershell
psql -U postgres -d tm_demo -f scripts/insert_test_data.sql
```

---

## 4. 环境变量

复制模板后根据实际情况填写：

```powershell
Copy-Item .env.example .env
```

关键项（完整字段见 `.env.example`）：

- `DATABASE_URL`：SQLAlchemy 格式的 PostgreSQL 连接串
- `JWT_SECRET_KEY`：**必须替换为一段长随机字符串**，否则后端会拒绝启动
- `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY`：LLM 修正功能的 provider，至少配一个
- `REDIS_URL`：可选；不配置时自动使用进程内存缓存
- `TM_VECTOR_*`：语义向量检索的开关与权重

> `.env` 已在 `.gitignore` 中，仓库只保留 `.env.example`。敏感密钥请通过私聊/密管平台分发。

---

## 5. 后端安装与启动

### 5.1 创建虚拟环境并安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 5.2 启动开发服务

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 19003 --reload
```

- `--host 0.0.0.0`：允许局域网访问
- 本机访问：`http://127.0.0.1:19003`
- 注意 Windows 防火墙要放行该端口

### 5.3 首次初始化管理员

打开前端 `http://<host>:19003/`，如果数据库里还没有任何用户，会进入首次初始化流程；也可以直接调用接口：

```bash
curl -X POST http://127.0.0.1:19003/api/auth/init \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your-strong-password"}'
```

之后登录使用：`POST /api/auth/login`，拿到 JWT 后即可访问业务接口。

---

## 6. 前端安装与启动

### 6.1 开发模式（带热更新）

```powershell
cd frontend
npm install
npm run dev
```

默认监听 `http://127.0.0.1:5173`，并把 `/api` 代理到 `http://127.0.0.1:19013`。
如果后端监听在其他端口（例如 `19003`），可以在 `frontend/` 下建 `.env.local`：

```env
VITE_API_PROXY_TARGET=http://127.0.0.1:19003
```

### 6.2 生产模式（由 FastAPI 直接托管 SPA）

```powershell
cd frontend
npm install
npm run build
```

构建产物会输出到 `frontend/dist`。后端启动时会自动挂载：

- `/assets/*`：静态资源
- `/`、`/*`：回退到 `frontend/dist/index.html` 以支持 Vue Router history 模式

此时只需访问后端端口（例如 `http://<host>:19003/`）即可使用整站。

---

## 7. 常用维护脚本

> 下面命令在激活虚拟环境 (`.\.venv\Scripts\activate`) 且已设置 `DATABASE_URL` 后执行。

### 7.1 CSV 导入 TM

```powershell
python scripts/import_tm.py --database-url "$env:DATABASE_URL" --csv-path sample_tm.csv
```

### 7.2 XLSX 导入 TM

```powershell
python scripts/import_tm_xlsx.py --database-url "$env:DATABASE_URL" --xlsx-path your_tm.xlsx --batch-size 5000
```

### 7.3 重建 `source_hash` / `source_normalized`

```powershell
python scripts/rebuild_tm_fields.py --database-url "$env:DATABASE_URL" --batch-size 1000
```

### 7.4 回填 / 重建 pgvector embedding

```powershell
python scripts/rebuild_tm_embeddings.py --database-url "$env:DATABASE_URL" --batch-size 500
```

首次开启向量检索或修改 `TM_VECTOR_DIMENSIONS` 后都需要跑一次（可加 `--rebuild-all`）。

### 7.5 按 `source_hash` 去重

```powershell
python scripts/deduplicate_tm_source_hash.py --database-url "$env:DATABASE_URL" --apply
```

---

## 8. API 速查

所有业务接口都在 `/api` 前缀下，除 `/api/auth/*` 外默认需要 `Authorization: Bearer <token>`。

| 模块 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| 认证 | GET | `/api/auth/init` | 查询是否需要首次初始化 |
| 认证 | POST | `/api/auth/init` | 首次创建管理员 |
| 认证 | POST | `/api/auth/login` | 登录换取 JWT |
| 认证 | GET | `/api/auth/me` | 当前用户信息 |
| 认证 | POST | `/api/auth/register` | 管理员创建新用户 |
| 文档 | POST | `/api/file-records` | 上传 DOCX 并创建翻译任务 |
| 文档 | GET | `/api/file-records` | 文档列表 |
| 文档 | GET | `/api/file-records/{id}` | 文档 + 分页句段 |
| 文档 | GET | `/api/file-records/{id}/preview` | HTML 预览 |
| 文档 | GET | `/api/file-records/{id}/export-docx` | 导出译文 DOCX |
| 文档 | PUT | `/api/file-records/{id}/segments/{sentence_id}` | 更新单个译文 |
| 文档 | PUT | `/api/file-records/{id}/segments` | 批量更新译文 |
| 文档 | POST | `/api/file-records/{id}/llm-translate` | SSE 流式 LLM 修正 |
| 文档 | DELETE | `/api/file-records/{id}` | 删除（管理员） |
| 批注 | GET/POST | `/api/file-records/{id}/comments` | 列表 / 新建 |
| 批注 | PATCH/DELETE | `/api/comments/{id}` | 更新 / 删除 |
| 批注 | POST | `/api/comments/{id}/replies` | 嵌套回复 |
| TM | GET/POST/PUT/DELETE | `/api/tm/collections[/{id}]` | 记忆库 CRUD |
| TM | POST | `/api/tm/import-xlsx` | XLSX 导入（管理员） |
| TM | POST | `/api/tm/add` | 单条新增（去重） |
| TM | POST | `/api/tm/batch-add` | 批量新增（去重） |
| 解析 | POST | `/api/parser/workspace` | 仅解析不落库，返回工作台结构 |

更多细节直接看 `app/routers/api.py` 与 FastAPI 自带的 `/docs` 页。

---

## 9. 常见问题

- **启动报 "JWT_SECRET_KEY 仍为默认值"**：检查 `.env` 是否真的生效（工作目录要在项目根），并替换为长随机字符串。
- **`CREATE EXTENSION vector` 失败**：说明 pgvector 没装到当前 PostgreSQL 实例，参考第 1.1 节。
- **`ix_translation_memory_source_embedding_ivfflat` 初次命中慢**：ivfflat 索引训练后建议执行 `VACUUM ANALYZE translation_memory;` 并调整 `lists` / `ivfflat.probes`。
- **LLM 修正不可用**：至少配置 `DEEPSEEK_API_KEY` 或 `OPENROUTER_API_KEY`，否则调用 `llm-translate` 会返回 400。
- **局域网无法访问**：后端启动参数用 `--host 0.0.0.0`，并放行 Windows 防火墙端口。
- **前端开发时 `/api` 404**：检查 `frontend/.env.local` 中的 `VITE_API_PROXY_TARGET` 是否指向真正的后端端口。

---

## 10. 许可证

内部项目，默认保留所有权利。需要开源请自行在此处补充 LICENSE。
