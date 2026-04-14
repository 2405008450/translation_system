# Translation Memory Demo

一个基于 FastAPI + PostgreSQL 的翻译记忆库检索 Demo，支持：

- 上传 `.txt` / `.docx` 文本做 TM 匹配
- 首页上传 `.xlsx` 批量导入翻译记忆库
- 精确匹配 + 模糊匹配
- 局域网访问

## 目录结构

```text
app/
  main.py
  config.py
  database.py
  models.py
  schemas.py
  routers/
    web.py
  services/
    file_parser.py
    matcher.py
    normalizer.py
    sentence_splitter.py
    tm_importer.py
  templates/
    index.html
    result.html
scripts/
  init_db.sql
  create_document_tables.sql
  import_tm.py
  import_tm_xlsx.py
  rebuild_tm_fields.py
  deduplicate_tm_source_hash.py
requirements.txt
README.md
```

## 1. 安装依赖

建议使用 Python 3.11+ 虚拟环境。

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## 2. 初始化数据库

先创建数据库 `tm_demo`，然后执行初始化 SQL：

```powershell
psql -U postgres -d tm_demo -f scripts/init_db.sql
```

`scripts/init_db.sql` 会同时初始化：

- `translation_memory`
- `file_records`
- `segments`

如果是旧库，只初始化过 TM 相关表，也可以单独补跑：

```powershell
psql -U postgres -d tm_demo -f scripts/create_document_tables.sql
```

## 3. 数据库连接

项目默认会从根目录 `.env` 读取连接串。

`.env` 只建议本地保留，不要提交到仓库。

当前可用示例：

```env
DATABASE_URL=postgresql+psycopg://tm_user:tm123456@localhost:5432/tm_demo
```

如果你想临时手动指定，也可以：

```powershell
$env:DATABASE_URL="postgresql+psycopg://tm_user:tm123456@localhost:5432/tm_demo"
```

## 4. 导入翻译记忆库

### 4.1 导入 CSV

CSV 至少需要两列：

- `source_text`
- `target_text`

```powershell
python scripts/import_tm.py --database-url "postgresql+psycopg://tm_user:tm123456@localhost:5432/tm_demo" --csv-path sample_tm.csv
```

### 4.2 导入 XLSX

XLSX 默认读取：

- A 列：中文原文
- B 列：英文译文

支持首行表头，例如：

- `zh-CN / en-US`
- `source_text / target_text`

命令行导入：

```powershell
python scripts/import_tm_xlsx.py --database-url "postgresql+psycopg://tm_user:tm123456@localhost:5432/tm_demo" --xlsx-path "27. 技术中英.xlsx" --batch-size 5000
```

也可以直接在首页上传 `.xlsx` 文件导入数据库。

XLSX 导入会按 `source_hash` 去重；如果命中现有 TM，系统会更新原记录而不是重复插入。

## 5. 启动项目

### 5.1 本机启动

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 19003
```

本机访问地址：

```text
http://127.0.0.1:19003
```

### 5.2 局域网启动

如果需要同一局域网其他设备访问，请监听 `0.0.0.0`：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 19003
```

当前机器局域网 IP：

```text
192.168.31.135
```

局域网访问地址：

```text
http://192.168.31.135:19003
```

## 6. 页面说明

首页包含两个入口：

- 文本匹配：上传 `.txt` / `.docx`，输入模糊匹配阈值
- XLSX 导入 TM：上传 `.xlsx`，读取 A/B 列导入数据库

结果页展示字段：

- 原文句子
- 状态：`exact` / `fuzzy` / `none`
- 匹配度
- 匹配到的原文
- 对应译文

## 7. 匹配逻辑

### 7.1 文本处理

- 支持 `.txt` / `.docx`
- 先按换行分行
- 再按 `。？！!?.` 拆句
- 保留原句用于展示

### 7.2 标准化

- 去除首尾空白
- 压缩连续空白
- 清理不可见字符
- 匹配时会去掉尾部句末标点，提升命中率

### 7.3 匹配策略

先精确匹配：

- `source_hash`
- `source_normalized`
- `source_text`

再模糊匹配：

- 先走 trigram 索引候选召回
- 再用相似度重排

## 8. 维护脚本

重建 `source_hash` / `source_normalized`：

```powershell
python scripts/rebuild_tm_fields.py --database-url "postgresql+psycopg://tm_user:tm123456@localhost:5432/tm_demo" --batch-size 1000
```

按 `source_hash` 清理历史重复记录：

```powershell
python scripts/deduplicate_tm_source_hash.py --database-url "postgresql+psycopg://tm_user:tm123456@localhost:5432/tm_demo" --apply
```

如果要顺便补 `uq_translation_memory_source_hash` 唯一索引，执行该命令的数据库账号需要是 `translation_memory` 表属主。

## 9. 注意事项

- 模糊匹配依赖 PostgreSQL 扩展 `pg_trgm`
- 大文件导入建议使用命令行脚本
- 如果局域网设备无法访问，通常需要检查 Windows 防火墙是否放行 `19003` 端口
