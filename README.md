# AI Translation System

基于 **FastAPI + Vue 3 + PostgreSQL** 的内部翻译项目工作台。系统围绕项目创建、语言资产导入、源文档解析、TM/术语辅助、AI 修正、人工审校和译后文件导出组织流程，当前前端已经从早期的单一 DOCX Demo 演进为项目管理 + 任务工作台 + 语言资产管理的一体化应用。

## 功能概览

- **项目与任务管理**：创建翻译项目，维护源语言、目标语言、截止时间、访问级别、创建人、处理进度；项目详情页上传源文档，任务页进入工作台。
- **多格式任务解析**：支持 DOCX 以及文本、本地化、网页、字幕、技术写作、双语交换和部分工程/设计文件的句段抽取与原格式导出。
- **翻译记忆库（TM）**：按记忆库分组管理双语句对，支持 TMX/SDLTM/XLS/XLSX/CSV 导入、条目维护、XLSX/TMX 导出、精确匹配、trigram 模糊匹配和 pgvector 语义候选。
- **术语库**：独立术语库与术语条目管理，支持 TMX/TBX/XLS/XLSX/CSV 导入与 XLSX/TMX/TBX 导出，并在工作台按当前语言对和句段内容推荐术语。
- **翻译工作台**：句段分页/懒加载、原文/译文/分屏预览、自动保存、TM 候选、术语面板、修改快照、批注与嵌套回复。
- **AI 修正**：对 exact / fuzzy / none 不同范围触发 LLM 修正，支持 DeepSeek、OpenRouter 和自动选择，SSE 流式返回进度并写回句段。
- **用户与权限**：首次初始化管理员、JWT 登录、管理员创建用户、用户昵称、基础角色区分。

## 技术栈


| 层级          | 技术                                                                    |
| ----------- | --------------------------------------------------------------------- |
| 后端          | FastAPI、SQLAlchemy 2、Pydantic Settings、python-docx、openpyxl、httpx     |
| 前端          | Vue 3、Vite、TypeScript、Pinia、Vue Router、vue-i18n、Axios、lucide-vue-next |
| 数据库         | PostgreSQL、pg_trgm、pgvector                                           |
| 缓存          | Redis 可选；未配置时使用进程内存缓存                                                 |
| AI Provider | DeepSeek、OpenRouter                                                   |


## 当前支持的任务文件

前端任务入口和后端任务白名单当前支持：

```text
.docx
.txt, .csv
.html, .htm, .md, .markdown
.json, .yaml, .yml, .php, .properties
.po, .pot, .strings, .srt
.dita, .ditamap, .xml, .svg
.sdlxliff, .txml
.dxf, .idml, .mif
.zip
```

说明：

- DOCX 使用专用解析与导出逻辑，尽量保留段落、表格、页眉页脚、脚注、尾注、编号和部分内联样式。
- 其他格式走 `app/services/adapters/` 下的格式适配器，译后导出优先使用原格式；部分格式也支持 TMX / XLIFF / 双语文本等导出能力。
- 代码中存在 PDF、PPTX、XLSX、RAR 等适配器文件，但当前任务入口白名单与前端上传选择未开放这些格式。

## 目录结构

```text
app/                                  # FastAPI 后端
  main.py                             # 应用入口，挂载 /api 与生产 SPA
  config.py                           # 环境变量配置与启动校验
  database.py                         # SQLAlchemy Engine / Session
  models.py                           # ORM 模型
  schemas.py                          # 请求/响应 Schema
  auth.py                             # JWT、密码、用户工具
  routers/
    auth.py                           # /api/auth/*
    api.py                            # 项目、任务、TM、批注、AI 等接口
    term_base.py                      # /api/term-bases*
  services/
    task_file_service.py              # 多格式任务解析/预览/导出统一入口
    document_workspace.py             # DOCX 解析与 HTML 预览
    document_exporter.py              # DOCX 译后导出
    file_record_service.py            # 项目/任务/句段持久化
    matcher.py                        # TM 精确、模糊、语义匹配
    tm_vector.py                      # pgvector embedding 同步
    tm_importer.py                    # TM 多格式导入
    term_importer.py                  # 术语多格式导入
    revision_service.py               # 句段修改历史
    comment_service.py                # 批注与回复
    llm_service.py                    # LLM 调用、fallback、并发与重试
    adapters/                         # 多格式解析与导出适配器

frontend/                             # Vue 3 + Vite 前端
  src/
    views/                            # 项目、任务、工作台、TM、术语、用户页面
    components/                       # 表格、预览、句段编辑、批注、资源导入等组件
    stores/                           # Pinia：auth/task/segment/comment/shell/preferences
    constants/                        # 语言、状态、LLM、任务文件配置
    locales/                          # 中文文案
    router/                           # 前端路由

scripts/                              # 数据库初始化、迁移、导入与维护脚本
tests/                                # pytest 测试
data/file_records/                    # 默认源文件存储目录
requirements.txt
.env.example
```

## 环境准备


| 组件         | 建议版本          | 说明                        |
| ---------- | ------------- | ------------------------- |
| Python     | 3.11+         | 后端运行环境                    |
| Node.js    | 18+，推荐 20 LTS | 前端开发与构建                   |
| PostgreSQL | 14+           | 需要 `pg_trgm` 和 `pgvector` |
| Redis      | 5+，可选         | 配置 `REDIS_URL` 后用于缓存      |


### PostgreSQL 扩展

`pg_trgm` 通常随 PostgreSQL 自带；`pgvector` 需要额外安装到当前 PostgreSQL 实例。

- 官方文档：[https://github.com/pgvector/pgvector#installation](https://github.com/pgvector/pgvector#installation)
- Windows 可下载 pgvector release zip，将 `vector.dll`、`vector--*.sql`、`vector.control` 放到 PostgreSQL 对应的 `lib/` 和 `share/extension/` 目录。

初始化 SQL 会执行 `CREATE EXTENSION IF NOT EXISTS pg_trgm;` 与 `CREATE EXTENSION IF NOT EXISTS vector;`，因此执行初始化脚本时建议使用 PostgreSQL 超级用户。

## 快速开始

### 1. 创建数据库

以 `postgres` 超级用户登录后执行一次：

```sql
CREATE USER tm_user WITH PASSWORD 'tm123456';
CREATE DATABASE tm_demo OWNER tm_user;
GRANT ALL PRIVILEGES ON DATABASE tm_demo TO tm_user;
```

### 2. 初始化当前 schema

当前仓库里的 `init_db.sql` 覆盖基础表、扩展和多数索引；项目管理、资源绑定、创建者字段等最新增量由后续脚本补齐。新库建议按下面顺序全部执行一遍，脚本均按幂等方式编写：

```powershell
psql -U postgres -d tm_demo -f scripts/init_db.sql
psql -U postgres -d tm_demo -f scripts/add_user_nickname.sql
psql -U postgres -d tm_demo -f scripts/create_segment_revisions.sql
psql -U postgres -d tm_demo -f scripts/add_project_fields.sql
psql -U postgres -d tm_demo -f scripts/add_file_record_resource_binding.sql
psql -U postgres -d tm_demo -f scripts/add_creator_to_entries.sql
```

涉及的核心表：

- `users`：用户、昵称、角色、启用状态
- `memory_bases` / `memory_entries`：TM 记忆库与双语句对
- `term_bases` / `term_entries`：术语库与术语条目
- `file_records`：项目/任务记录、语言对、资源绑定、截止时间
- `segments`：句段、匹配状态、译文、结构定位、匹配来源信息
- `segment_comments`：句段批注、选区锚点、嵌套回复
- `segment_revisions`：人工修改与 AI 写回的修改快照

旧库升级时，如果仍存在 `tm_collections` / `translation_memory` 等旧表名，先执行：

```powershell
psql -U postgres -d tm_demo -f scripts/rename_translation_memory_tables.sql
```

如果旧库仍是非 UUID 主键，再根据现场情况执行：

```powershell
psql -U postgres -d tm_demo -f scripts/migrate_primary_keys_to_uuid.sql
```

### 3. 配置环境变量

复制模板：

```powershell
Copy-Item .env.example .env
```

重点配置项：


| 变量                                            | 说明                                                                                          |
| --------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `DATABASE_URL`                                | SQLAlchemy PostgreSQL 连接串，例如 `postgresql+psycopg://tm_user:tm123456@localhost:5432/tm_demo` |
| `JWT_SECRET_KEY`                              | 必须替换为长随机字符串；保持默认值会拒绝启动                                                                      |
| `FILE_STORAGE_DIR`                            | 源文件存储目录，默认 `data/file_records`                                                              |
| `UPLOAD_MAX_SIZE_MB`                          | 上传文件大小限制                                                                                    |
| `DEFAULT_SIMILARITY_THRESHOLD`                | 默认 TM 模糊匹配阈值                                                                                |
| `REDIS_URL`                                   | 可选；不配置时使用内存缓存                                                                               |
| `TM_VECTOR_*`                                 | pgvector 语义检索开关、维度、候选数和权重                                                                   |
| `DEEPSEEK_*` / `OPENROUTER_*`                 | LLM Provider 配置，AI 修正至少需要一个 API Key                                                         |
| `LLM_TIMEOUT_SECONDS` / `LLM_STALL_TIMEOUT_SECONDS` / `LLM_MAX_CONCURRENCY` | LLM 单次请求超时、无进展中止阈值与并发控制                                                                 |
| `LANGUAGETOOL_BASE_URL` / `LANGUAGETOOL_TIMEOUT_SECONDS` / `LANGUAGETOOL_MAX_TEXT_LENGTH` | 拼写/语法 QA 使用的自托管 LanguageTool HTTP Server 配置；未配置时自动跳过 QA，不影响译文保存。 |


`.env` 已在 `.gitignore` 中，密钥不要提交到仓库。


### LanguageTool 拼写/语法 QA

建议将 LanguageTool 作为内网服务部署，不暴露公网。应用侧通过 `LANGUAGETOOL_BASE_URL=http://languagetool:8010/v2` 访问：

```yaml
services:
  app:
    environment:
      LANGUAGETOOL_BASE_URL: http://languagetool:8010/v2
      LANGUAGETOOL_TIMEOUT_SECONDS: 10
      LANGUAGETOOL_MAX_TEXT_LENGTH: 20000
    depends_on:
      - languagetool

  languagetool:
    image: silviof/docker-languagetool:latest
    expose:
      - "8010"
```

项目设置中启用“质量保证 -> 拼写/语法检查”后，译文保存会后台触发检查。LanguageTool 未配置、不可用或目标语言暂不支持时，系统只跳过 QA，不会阻塞保存、确认、预翻译等核心流程。

### 4. 启动后端

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 19013 --reload
```

- 开发后端：`http://127.0.0.1:19013`
- API 文档：`http://127.0.0.1:19013/docs`

- `--host 0.0.0.0` 用于允许局域网访问；Windows 防火墙需要放行端口。

### 5. 启动前端

```powershell
cd frontend
npm install
npm run dev
```

开发前端默认地址：`http://127.0.0.1:5173`

`frontend/vite.config.ts` 默认把 `/api` 代理到 `http://127.0.0.1:19013`。如果后端端口不同，在 `frontend/.env.local` 中覆盖：

```env
VITE_API_PROXY_TARGET=http://127.0.0.1:19003
```

### 6. 首次初始化管理员

打开 `http://127.0.0.1:5173/login`。如果数据库中还没有任何用户，登录页会进入初始化管理员流程。

也可以直接调用接口：

```bash
curl -X POST http://127.0.0.1:19013/api/auth/init \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","nickname":"管理员","password":"your-strong-password"}'
```

## 生产构建

前端构建后由 FastAPI 直接托管：

```powershell
cd frontend
npm install
npm run build
```

构建产物输出到 `frontend/dist`。后端启动后会挂载：

- `/assets/*`：静态资源
- `/` 与 `/*`：回退到 `frontend/dist/index.html`，支持 Vue Router history 模式

Docker 生产默认由 nginx 对外发布 HTTP 80，例如 `http://127.0.0.1/`；容器内 app 仍监听 `19013`。

## 常用开发命令

```powershell
# 后端开发
.\.venv\Scripts\activate
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 19013 --reload

# 前端开发
cd frontend
npm run dev

# 前端类型检查与构建
cd frontend
npm run build

# 后端测试（需要已安装 pytest）
python -m pytest tests
```

## 语言资产维护

### TM CSV 导入

```powershell
python scripts/import_tm.py `
  --database-url "$env:DATABASE_URL" `
  --csv-path sample_tm.csv `
  --source-language zh-CN `
  --target-language en-US `
  --collection-id <memory-base-uuid>
```

### TM 表格导入

Excel 约定：第一列为源文，第二列为译文。

```powershell
python scripts/import_tm_xlsx.py `
  --database-url "$env:DATABASE_URL" `
  --xlsx-path your_tm.xlsx `
  --source-language zh-CN `
  --target-language en-US `
  --collection-id <memory-base-uuid> `
  --batch-size 5000
```

### 术语表格导入

Excel 约定：第一列为源术语，第二列为目标术语。

```powershell
python scripts/import_term_xlsx.py `
  --database-url "$env:DATABASE_URL" `
  --xlsx-path your_terms.xlsx `
  --term-base-id <term-base-uuid> `
  --source-language zh-CN `
  --target-language en-US `
  --batch-size 5000
```

### 重建 TM 归一化字段

```powershell
python scripts/rebuild_tm_fields.py --database-url "$env:DATABASE_URL" --batch-size 1000
```

### 回填 / 重建 pgvector embedding

```powershell
python scripts/rebuild_tm_embeddings.py --database-url "$env:DATABASE_URL" --batch-size 500
```

首次开启向量检索、重新安装 pgvector、修改 `TM_VECTOR_DIMENSIONS` 或需要全量刷新时使用：

```powershell
python scripts/rebuild_tm_embeddings.py --database-url "$env:DATABASE_URL" --batch-size 500 --rebuild-all
```

### 按 source_hash 去重

先 dry-run 查看，再确认执行：

```powershell
python scripts/deduplicate_tm_source_hash.py --database-url "$env:DATABASE_URL"
python scripts/deduplicate_tm_source_hash.py --database-url "$env:DATABASE_URL" --apply
```

## API 速查

所有业务接口均带 `/api` 前缀。除 `/api/auth/init` 与 `/api/auth/login` 外，默认需要：

```http
Authorization: Bearer <token>
```


| 模块  | 方法             | 路径                                                           | 说明                |
| --- | -------------- | ------------------------------------------------------------ | ----------------- |
| 认证  | GET            | `/api/auth/init`                                             | 查询是否需要首次初始化       |
| 认证  | POST           | `/api/auth/init`                                             | 首次创建管理员           |
| 认证  | POST           | `/api/auth/login`                                            | 登录并获取 JWT         |
| 认证  | GET            | `/api/auth/me`                                               | 当前用户              |
| 用户  | GET            | `/api/auth/users`                                            | 用户列表              |
| 用户  | POST           | `/api/auth/register`                                         | 管理员创建用户           |
| 用户  | PATCH          | `/api/auth/users/{id}`                                       | 更新用户名、昵称或密码       |
| 项目  | GET/POST       | `/api/projects`                                              | 项目列表 / 新建项目       |
| 项目  | GET            | `/api/projects/{id}`                                         | 项目详情与进度           |
| 项目  | POST           | `/api/projects/{id}/source-document`                         | 为项目上传源文档并生成句段     |
| 任务  | POST           | `/api/file-records`                                          | 直接上传任务文件          |
| 任务  | GET            | `/api/file-records`                                          | 任务列表              |
| 任务  | GET            | `/api/file-records/{id}`                                     | 任务详情与分页句段         |
| 任务  | GET            | `/api/file-records/{id}/preview`                             | 原文预览 HTML         |
| 任务  | GET            | `/api/file-records/{id}/export`                              | 导出译后文件            |
| 句段  | PUT            | `/api/file-records/{id}/segments/{sentence_id}`              | 更新单个译文            |
| 句段  | PUT            | `/api/file-records/{id}/segments`                            | 批量更新译文            |
| 句段  | GET            | `/api/file-records/{id}/segments/{segment_id}/tm-candidates` | 查看 TM 候选          |
| 历史  | GET            | `/api/file-records/{id}/revisions`                           | 修改快照列表            |
| 历史  | PATCH          | `/api/revisions/{revision_id}`                               | 接受或拒绝修改快照         |
| 历史  | POST           | `/api/file-records/{id}/revisions/batch-accept`              | 批量接受              |
| 历史  | POST           | `/api/file-records/{id}/revisions/batch-reject`              | 批量拒绝              |
| 批注  | GET/POST       | `/api/file-records/{id}/comments`                            | 批注列表 / 新建批注       |
| 批注  | PATCH/DELETE   | `/api/comments/{id}`                                         | 更新 / 删除批注         |
| 批注  | POST           | `/api/comments/{id}/replies`                                 | 创建嵌套回复            |
| AI  | POST           | `/api/file-records/{id}/llm-translate`                       | SSE 流式 AI 修正      |
| TM  | GET/POST       | `/api/translation-memory/collections`                        | 记忆库列表 / 新建        |
| TM  | GET/PUT/DELETE | `/api/translation-memory/collections/{id}`                   | 记忆库详情 / 更新 / 删除   |
| TM  | GET/POST       | `/api/translation-memory/collections/{id}/entries`           | 条目列表 / 新增         |
| TM  | PUT/DELETE     | `/api/translation-memory/entries/{id}`                       | 更新 / 删除条目         |
| TM  | POST           | `/api/translation-memory/import`                             | TMX/SDLTM/表格导入 TM |
| TM  | GET            | `/api/translation-memory/collections/{id}/export-xlsx`       | XLSX 导出 TM        |
| 术语  | GET/POST       | `/api/term-bases`                                            | 术语库列表 / 新建        |
| 术语  | GET/PUT/DELETE | `/api/term-bases/{id}`                                       | 术语库详情 / 更新 / 删除   |
| 术语  | GET/POST       | `/api/term-bases/{id}/entries`                               | 术语条目列表 / 新增       |
| 术语  | PUT/DELETE     | `/api/term-entries/{id}`                                     | 更新 / 删除术语条目       |
| 术语  | POST           | `/api/term-bases/import`                                     | TMX/TBX/表格导入术语   |
| 术语  | GET            | `/api/term-bases/{id}/export-xlsx`                           | XLSX 导出术语         |
| 解析  | POST           | `/api/parser/workspace`                                      | 仅解析文件并返回工作台结构，不落库 |


`/api/documents/*` 与 `/api/tm/*` 仍保留为兼容旧前端的隐藏别名，新开发建议使用上表中的新路径。

## 常见问题

- **启动报 `JWT_SECRET_KEY` 仍为默认值**：复制 `.env.example` 后必须修改 `JWT_SECRET_KEY`，并确认启动命令在项目根目录执行。
- **前端 `/api` 请求失败**：开发模式默认代理到 `http://127.0.0.1:19013`，确认后端端口一致；否则配置 `frontend/.env.local` 的 `VITE_API_PROXY_TARGET`。
- `**CREATE EXTENSION vector` 失败**：pgvector 未安装到当前 PostgreSQL 实例，安装后再执行初始化脚本。
- **上传任务提示必须选择 TM 记忆库**：当前解析会限制在已选择的记忆库中做匹配，需要先创建并选择至少一个语言对一致的记忆库。
- **语言对不一致**：项目、TM 记忆库和术语库都有语言对校验，上传或导入时需要保持一致。
- **AI 修正不可用**：至少配置 `DEEPSEEK_API_KEY` 或 `OPENROUTER_API_KEY`；也要确认 `provider` 选择与实际配置一致。
- **导出失败**：源文件缺失或当前格式未开放原格式导出时会失败；DOCX 需要保留原始源文件。
- **TM 向量命中慢或为空**：确认已安装 pgvector、执行 `scripts/add_tm_pgvector_support.sql`，并运行 `scripts/rebuild_tm_embeddings.py` 回填 embedding。
- **局域网无法访问**：后端启动使用 `--host 0.0.0.0`，并放行 Windows 防火墙端口。

## 许可证

内部项目，默认保留所有权利。如需开源，请补充正式 LICENSE。
