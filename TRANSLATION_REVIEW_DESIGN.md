# 翻译内容校对模块（Translation Review）设计与实施方案

> 依据文档：`广东地方志翻译规则_230620.docx`（共 10 个大类，编号 0–9）
> 目标：把这份人工校对规则拆成多个独立 AI Agent 分类别检查，按句段汇总（同一句段可命中多个类别），支持逐条/批量应用修改，并导出 Word 校对报告。

---

## 0. 已确认的决策

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 类别 3（数字） | 新模块只做**格式合规**（拼写形式、日期、金额、单位、罗马数字等）；**数值一致性**继续由现有「数字专检」负责，两者并存，UI 上互相提示 |
| 2 | 类别 4（专有名词） | 接入当前文件绑定的**术语库 + 记忆库**作为上下文；**联网查证做成可选开关**，默认关闭，插件化设计（首个实现基于 OpenRouter `openrouter:web_search` server tool，并预留自研联网插件接口） |
| 3 | 检查范围 | 做成**可选设置**（句段状态 / 类别多选），**默认全选**（全部句段、全部类别，句法优化类默认不参与批量应用） |
| 4 | 合并视图 | **v1 支持**（与数字专检一致）。本模块以**独立的「翻译内容校对」窗口**展示；单文件与合并视图共用同一套面板，合并视图下报告按文件分组呈现 |
| 5 | `agent_runs` 排障表 | **需要**，单独建表 |
| 6 | 生成入口 | **arq 任务 + 前端轮询**（不用长连 SSE） |
| 7 | 并发默认值 | `translation_review_max_concurrency = 3`，`translation_review_requests_per_minute = 60` |

> 术语澄清：项目里的「合并视图（merge view）」指的是把多个文件放进同一个工作台一起编辑（`mergeViewId`）。本模块在合并视图下**不额外开窗**，仍是同一个「翻译内容校对」抽屉窗口，只是报告内容覆盖视图内所有文件并按文件分组。合并视图相关的额外约束见第 19 节。

### 代码现状核实结论（本方案的前提）

| 能力 | 现状 | 处理 |
|---|---|---|
| `config.llm_max_concurrency = 5` | Semaphore 只在 `iter_batch_translate` 内部生效，`request_chat_completion` **无任何并发控制** | 新增独立闸门模块 |
| `llm_retry_attempts_per_provider = 2` | 退避 `min(0.5*(n+1), 2.0)`，线性、2 秒封顶 | 需要 429 感知的指数退避 |
| 429 处理 | `_request_translation` 把 `HTTPStatusError` 统一包成 `LLMRequestError(文本)`，**状态码与 `Retry-After` 丢失** | 需改造 `llm_service` |
| 现有数字专检并发 | `for block in blocks: await _call_ai(...)`，**完全串行** | 并发压力是本模块新引入的风险 |
| arq + Redis | 依赖已装、生产 compose 已有 redis，`api.py` 有 `_enqueue_arq_job()` + 「未启用 arq 回退本地执行」惯例 | 直接复用 |
| 长任务范式 | 导出走 `file_export_queue` + `POST → task_id` + `GET export-tasks/{id}` 轮询 | 本模块照此实现 |
| `request_chat_completion` | 支持 `response_format`，**不支持 `tools`** | 联网查证需扩展该函数 |
| 建表方式 | 无 Alembic，靠 `app/services/schema_setup.py` 的 `CREATE TABLE IF NOT EXISTS` 运行时建表 | 新表必须 ORM + schema_setup 双写 |
| `Segment` 模型 | 无 heading/style 字段，仅 `block_type`、`segment_metadata`(JSON) | 阶段 1 需先确认标题信息是否在 `segment_metadata` 里 |

---

## 1. 规则清单与检测分层

10 个类别，每类一个 Agent。`mode` 决定是否需要程序预筛，`apply_mode` 决定能否自动应用。

| key | idx | 类别 | mode | 程序可判定部分 | AI 判断部分 | severity | apply_mode |
|---|---|---|---|---|---|---|---|
| `tense` | 0 | 时态 | ai_only | — | 客观事实描述用现在时 vs 过去事件用过去时/过去完成时；图片说明同正文 | error | anchor |
| `symbols` | 1 | 英文符号与中文符号 | program_then_ai | 译文残留中文标点；`&` 未两侧空格；`/` 多空格；破折号非 em dash；年份区间非 en dash（前后不空格）；电话括号未空格；括号嵌套未用方括号 | 中文间隔号 `·` 转逗号/分号/`&`/冒号/属格的选择 | error | anchor |
| `casing` | 2 | 大小写 | program_then_ai | 标题实词首字母；hyphen 后大写（前缀例外、港澳/民国人名例外）；标题中介词一律小写；冒号后首字母大写；`Party` 大写；`the X dynasty`；拉丁词（per capita）不大写 | 双引号内三分支判定：专名→大写、概括性简写→直译保留引号 或 解释性转译去引号、普通引用→不大写；原文误用引号则删除 | error | anchor |
| `number_format` | 3 | 数字格式 | program_then_ai | 0–9 用单词 / 10+ 用阿拉伯数字（科学单位例外）；句首不得用阿拉伯数字；`percentage point(s)` 单复数；日期三种格式；金额（`USD`/`RMB` + 空格 + 千分位 + 小数≤2 位，超两位降级 million）；度量衡用全称（kWh 等例外）；复合单位用缩写；`X-odd` 加 hyphen；分数用单词、作形容词加 hyphen；Phase/X 期用大写罗马数字；一二三→I/II/III | `"10+1"`/`"9+2"` 等带含义数字的意译；经纬度特例（末尾两个单引号）；`PM2.5` 下标 | error | anchor |
| `proper_noun` | 4 | 专有名词 | program_then_ai (+可选联网) | 出现缩写；`XXXXX("XX")` 形式；`km`/`m3` 等缩写；`southern Guangdong`；`Lyu`（应为 `Lv`） | 人名姓+名；地址（Unit/Tower//F/方向词前置/路名拼音/`Avenue`/`中山六路→Sixth Zhongshan Road`/门牌不加 No.）；文件号拼音首字母；书名号文件名与已有译文一致；机构名/职位官方译法；`南粤=Guangdong`、`大陆=Chinese mainland`；同拼音地名加中文括号 | error | anchor |
| `fixed_syntax` | 5 | 固定句法 | ai_only | — | 无主语标题必须转被动（不得动词/ing 开头）；正文补主语（省/市名）并与被动交替；`截至…末=By the end of…`；`居前五=ranks among the top five in…`；"会议"不宜直接作主语；"XX省/市"不宜直接作主语 | warning | full |
| `noun_merge` | 6 | 相同格式名词合并 | ai_only | — | 并列同类专名合并为单一中心词复数，且中心词小写（`… expressways`、`… rivers`） | warning | anchor |
| `omission` | 7 | 避免漏译 | program_then_ai | 原文含历史纪年+公元年但译文只出现其一（启发式） | 并列动词/名词漏译；数字前限定词（超…、不少于…）；活动主题；长句中状语成分；逐项列出不可概括合并；不省译半句 | error | full |
| `comprehension` | 8 | 原文理解 | ai_only | — | 地理范围扩大化；程度副词误作比较级；并列成分错误合并；修饰语对象混淆；同字不同义；领域专业词；逻辑关系；原文歧义需查证 | error | full |
| `syntax_polish` | 9 | 句法优化 | ai_only | — | 长句断句；不以 `In 2019,` 等日期开头（调整至句中/句末）；意译不够确切 | suggestion | full |

### 关键约束

- **类别 2、5 强依赖"这句是不是标题"**。阶段 1 必须先确认解析器有没有把段落样式写进 `segment_metadata`；若没有，采用启发式（无句末标点 + 长度较短 + 独立成块 + `block_type`）并在提示词里让模型二次判断，同时在报告里标注"标题判定为推测"。
- **类别 3 与现有「数字专检」不重叠**：本模块只看"英文书写格式是否合规"，数值是否忠实原文由数字专检负责。UI 上在类别 3 的空态提示"数值一致性请使用数字专检"。
- **类别 4 必须喂术语/记忆上下文**，否则模型会凭空编造机构名译法。
- **类别 9 是建议性质**（`severity=suggestion`），默认不参与任何批量应用，必须逐条人工确认。

---

## 2. 总体架构

```
前端「翻译内容校对」窗口
   │  POST /api/file-records/{id}/translation-review-tasks     → { task_id, report_id }
   │  GET  /api/translation-review-tasks/{task_id}  (1.5s 轮询)  → 进度快照
   │  GET  /api/translation-review-reports/{id}                → 最终报告
   ▼
_enqueue_arq_job("translation_review_job", ...)   ── 未启用 arq → 回退本地后台任务
   ▼
┌──────────────────────── arq worker 进程 ────────────────────────┐
│ Orchestrator                                                    │
│   1. PayloadBuilder    句段装配（上下文 / 术语 / 记忆 / 标题判定） │
│   2. for agent in AgentRegistry (10 个)：                        │
│        a. program_rules  确定性预筛  → program findings          │
│        b. ai_input_filter  短路判断（无候选则跳过 LLM）           │
│        c. AgentRunner   分批 → llm_gate(Semaphore+令牌桶)        │
│                          → request_chat_completion(+可选联网)    │
│                          → seq/sid 校验 → 缺失补发重试           │
│        d. 写 agent_runs 一条                                     │
│        e. 更新 report.progress（供轮询读取）                     │
│   3. Aggregator  去重 / 锚点复验 / 偏移量计算 / 按句段聚合        │
│   4. 落库 translation_review_report_items                        │
└─────────────────────────────────────────────────────────────────┘
   ▼
translation_review_reports / _report_items / _agent_runs
   ▼
┌────────────┬──────────────────┬───────────────┬──────────────┐
│ 校对窗口   │ 应用/拒绝/忽略   │ 批量应用/撤销 │ Word 报告导出 │
└────────────┴──────────────────┴───────────────┴──────────────┘
```

设计要点：

- **每个 Agent 完全独立**：独立提示词、独立批次大小、独立失败状态。任一类别失败不影响其余 9 个，报告里单独标注该类别"检查失败"，可单独重跑。
- **进度落库而非驻留内存**：关页面、换设备、进程重启后都能续看进度。
- **并发闸门在单一 worker 进程内**，是真正的单点，不与 Web 请求争抢 LLM 配额。

---

## 3. Agent 设计

### 3.1 注册表

`app/services/translation_review/registry.py`，声明式配置：

```python
@dataclass(frozen=True)
class CategoryAgent:
    key: str                     # "casing"，稳定标识，落库用
    index: int                   # 2，对应规则文档章节号
    label: str                   # "大小写"
    rule_refs: tuple[str, ...]   # ("2.1", "2.4", "2.8", ...)
    severity: str                # "error" | "warning" | "suggestion"
    mode: str                    # "program_only" | "ai_only" | "program_then_ai"
    apply_mode: str              # "anchor" | "full" | "manual"
    batch_size: int              # 逐类调优，默认 12
    needs_context: bool          # 是否附带前后句
    needs_terms: bool            # 是否附带术语库/记忆库命中
    allow_web_verify: bool       # 是否允许联网查证（仅 proper_noun 为 True）
    program_rule: Callable | None       # 确定性预筛
    ai_input_filter: Callable | None    # 短路过滤：返回需送 LLM 的子集
    prompt_builder: Callable            # 构造 messages
```

### 3.2 提示词组织

`app/services/translation_review/prompts/{00_tense,01_symbols,...,09_syntax}.py`，每个文件包含四部分：

1. **规则原文摘录**（带条款编号，从 docx 抽出）——将来文档更新可对照修改，不把规则散写进代码逻辑。
2. **正反例 few-shot**——直接用文档里自带的例句，这是准确率最重要的来源。
3. **输出 JSON schema**。
4. **边界声明**——"只检查本类别，其他类别问题不要报"、"`quote`/`replace_anchor` 必须是译文原样片段，不得增删空格标点"。

### 3.3 输入格式（seq + sid 双写）

一个批次内的句段**必须同属一个文件**（见第 19.1 节），文件级信息放在批次头部而非逐条重复：

```
【本批文件】广东省情概貌.docx  语言对：zh-CN → en-US
【绑定术语】内涝=waterlogging | 全省=the province      (needs_terms=True 时提供)

[0] <sid=S00123> <heading=false>
原文: 全省城市内涝点有453个
译文: The province had 453 spots prone to drainage flooding.
上文: ...(needs_context=True 时提供)
下文: ...
```

### 3.4 统一输出 schema（所有 Agent 一致）

```json
[
  {
    "seq": 0,
    "sid": "S00123",
    "has_issue": true,
    "findings": [
      {
        "rule_ref": "5.6",
        "quote": "The province had",
        "replace_anchor": "The province had 453 spots",
        "suggested_value": "There were 453 spots",
        "suggested_target_text": "",
        "reason": "「XX省/市」不宜直接作主语，应改为 There be 句式",
        "confidence": "high"
      }
    ]
  }
]
```

### 3.5 结果校验（防错位的核心）

1. 数组长度必须等于输入条数；缺失条目补发重试（复用现有 `_run_ai_for_inputs` 逻辑，抽为公共 `agent_runner`）。
2. **`seq → sid` 映射不一致的条目直接丢弃**，记入 `agent_runs.error_message`，绝不按顺序"猜"。这是防止整批错位污染数据的关键闸门。
3. 仍缺失的条目标 `_missing` + 错误状态，写进 `agent_runs`，UI 提示"该类别 N 条未完成检查，可重跑"。

---

## 4. 联网查证插件（可选，默认关闭）

规则文档里大量条款要求"一定要上网查"（4.2.1 地名、4.5 机构名、4.6 企业职位、4.8.2 高速公路全称、8.6/8.9 词义查证）。没有联网时这些只能降级为"提示人工查证"；开启联网后由 Agent 自行发起搜索并返回带引用的结论。

### 4.1 插件接口（预留自研实现）

`app/services/translation_review/web_verify/base.py`

```python
class WebVerifyResult(TypedDict):
    content: str                        # 供模型使用的查证结论
    citations: list[dict[str, str]]     # [{title, url, snippet}]
    request_count: int                  # 计费/统计用

class WebVerifier(Protocol):
    key: str
    def is_available(self) -> bool: ...
    async def augment_messages(
        self, messages: list[dict], *, queries: list[str] | None = None
    ) -> tuple[list[dict], dict]: ...
    # 返回 (可能被改写的 messages, 需要合并到请求体的额外字段)
```

内置实现：

| key | 说明 |
|---|---|
| `none` | 默认。不联网，`proper_noun` Agent 把需查证项输出为 `apply_mode=manual` 的"建议人工查证"提示 |
| `openrouter` | 基于 OpenRouter `openrouter:web_search` server tool（见 4.2） |
| `custom` | 预留自研插件：读配置 `translation_review_web_search_custom_url` / `..._api_key`，POST `{query, max_results}` → `{results:[{title,url,snippet}]}`，由本模块把结果拼进 system 消息作为参考资料 |

选择哪个由配置 `translation_review_web_search_provider` 决定，前端设置弹窗可切换（下拉框：关闭 / OpenRouter 联网 / 自研插件）。

### 4.2 OpenRouter web_search 实现要点

参考 [OpenRouter Web Search Server Tool 文档](https://openrouter.ai/docs/guides/features/server-tools/web-search)（Beta）。要点（内容经改写以符合授权要求）：

- 启用方式是在请求体的 `tools` 数组里加入 `{ "type": "openrouter:web_search" }`；是否搜索、搜索几次由模型自行决定（0 到 N 次），这与已废弃的 `plugins: [{id:"web"}]` 插件不同——后者每次请求固定搜一次。
- 引擎 `engine` 可选 `auto`（默认，有原生搜索的模型走原生，否则回退 Exa）、`native`、`exa`、`firecrawl`（自带 key）、`parallel`、`perplexity`。
- 可控参数：`max_results`（默认 5，范围 1–25）、`max_uses`（单次请求最多搜几次）、`max_total_results`（跨多次搜索的结果总上限，控成本用）、`search_context_size`（low/medium/high）、`max_characters`（每条结果字符上限，与 `search_context_size` 同时给时以它为准）、`allowed_domains` / `excluded_domains`（域名白/黑名单，各引擎支持度不同）。
- 请求体顶层的 `max_tool_calls` 限制整体 server tool 调用预算，缺省 30（也是上限）。
- 用量在响应 `usage.web_search_requests` 里回报；搜索结果通过 `url_citation` 形式的 annotations 返回给调用方。
- 计费在 LLM token 费用之外另计，按引擎不同（Exa/Perplexity 约 $0.005/次请求，Parallel 约 $0.001/次请求，Firecrawl 走自己的 credits，native 由上游 provider 透传）。

> 内容已按授权要求改写，具体参数与价格以官方文档为准。

本模块的具体用法：

```python
# 仅当 provider=openrouter 且开关开启时生效
extra_body = {
    "tools": [{
        "type": "openrouter:web_search",
        "engine": settings.translation_review_web_search_engine,      # 默认 "auto"
        "max_results": 5,
        "max_uses": 2,                                                 # 每批次最多 2 次搜索
        "max_total_results": 10,
        "search_context_size": "low",                                  # 控 token
        "allowed_domains": settings.translation_review_web_allow_domains,  # 可配官方域名白名单
    }],
    "max_tool_calls": 4,
}
```

### 4.3 需要的 `llm_service` 扩展

`request_chat_completion` 目前只支持 `response_format`，需要补两个可选参数：

```python
async def request_chat_completion(
    ...,
    tools: list[dict] | None = None,
    extra_body: dict | None = None,
) -> LLMChatCompletionResult
```

`_request_translation` 里把它们合并进 payload；`LLMChatCompletionResult` 增加 `annotations` 与 `web_search_requests` 字段，供报告记录引用来源与用量。

### 4.4 强约束与降级

- **联网只在 `provider == "openrouter"` 时可用**（该 server tool 是 OpenRouter 的能力）。开启联网但当前选的是别的 provider 时：要么在设置里强制切到 openrouter，要么自动降级为 `none` 并在报告里提示，**不静默失败**。
- 联网只作用于 `allow_web_verify=True` 的 Agent（v1 仅 `proper_noun`），且只对 `ai_input_filter` 判定"疑似需查证"的句段生效，避免全量句段都触发搜索。
- 查证得到的引用写进 item 的 `citations` JSON 字段，Word 报告里作为脚注/备注列出，方便人工复核 AI 的依据。
- 联网开启时单独统计 `report.web_search_requests`，在窗口顶部显示，让用户对成本有感知。

---

## 5. 锚点定位与可应用性

只靠模型回传 ID 只能定位"哪一句"，句内定位必须服务端复验。五道关：

| 关卡 | 规则 | 失败处理 |
|---|---|---|
| 1. sid 校验 | `seq → sid` 必须与输入一致 | 丢弃该条，记 `agent_runs` |
| 2. quote 精确匹配 | `quote` 必须是 `target_text` 的子串 | 转归一化匹配（统一空白、引号/破折号变体、大小写不敏感） |
| 3. 归一化匹配 | 归一化后仍匹配则记 `locate_status='normalized'` | 仍失败 → `locate_status='unlocatable'` |
| 4. 锚点唯一性 | `replace_anchor` 在译文中出现次数必须 **恰好为 1** | 0 次 → `unlocatable`；≥2 次 → `ambiguous` |
| 5. 偏移量落库 | 定位成功时写 `quote_start` / `quote_end` 字符偏移 | 前端直接按偏移切 span 高亮，不重复做字符串搜索 |

后果约定：

- `locate_status != 'ok' | 'normalized'` 的 finding：**只作为文字提示展示，不高亮、不提供"应用"按钮**。宁可少高亮，不能高亮错位。
- 锚点唯一性这一关最有效——它把"改错位置"从概率问题变成确定性拒绝。
- 提示词里明确禁止改写片段，否则该条视为无效。

---

## 6. 数据模型

三张新表，字段风格照 `number_check_*`（JSON 用 `Text` 存字符串、全部带 `server_default`、独立索引）。
**必须 ORM (`app/models.py`) 与 `app/services/schema_setup.py` 双写**，否则生产环境不会建表。

### 6.1 `translation_review_reports`

```
id                    UUID PK
project_id            UUID FK projects ON DELETE CASCADE (nullable)
file_record_id        UUID FK file_records ON DELETE CASCADE (nullable)
merge_view_id         UUID FK project_merge_views ON DELETE CASCADE (nullable)
created_by_id         UUID FK users ON DELETE SET NULL (nullable)
scope                 VARCHAR(20)  DEFAULT 'file'       -- file | merge_view
segment_scope         VARCHAR(30)  DEFAULT 'all'        -- all | translated_only | unconfirmed_only | confirmed_only
enabled_categories    TEXT JSON    DEFAULT '[]'
file_ids              TEXT JSON    DEFAULT '[]'         -- 合并视图检索键，顺序同 _get_merge_view_context
total_files           INTEGER      DEFAULT 0
total_segments        INTEGER      DEFAULT 0
checked_segments      INTEGER      DEFAULT 0
category_counts       TEXT JSON    DEFAULT '{}'         -- {"casing": 12, "omission": 5, ...}
file_counts           TEXT JSON    DEFAULT '{}'         -- {file_id: 问题数}，供文件筛选下拉直接用
issue_count           INTEGER      DEFAULT 0
active_issue_count    INTEGER      DEFAULT 0
applied_count         INTEGER      DEFAULT 0
ignored_count         INTEGER      DEFAULT 0
multi_category_segment_count INTEGER DEFAULT 0          -- 命中 >=2 个类别的句段数
provider              VARCHAR(40)  DEFAULT ''
model                 VARCHAR(200) DEFAULT ''
web_verify_provider   VARCHAR(20)  DEFAULT 'none'
web_search_requests   INTEGER      DEFAULT 0
task_id               VARCHAR(64)  DEFAULT ''           -- arq job / 本地任务标识
status                VARCHAR(20)  DEFAULT 'running'    -- running | completed | partial_failed | failed
progress              TEXT JSON    DEFAULT '{}'         -- worker 写进度快照，供轮询读取
failed_categories     TEXT JSON    DEFAULT '[]'
error_message         TEXT         DEFAULT ''
created_at            TIMESTAMP    DEFAULT NOW()
finished_at           TIMESTAMP    NULL
```

索引：`project_id`、`file_record_id`、`merge_view_id`、`created_by_id`、`task_id`、`created_at`、`(scope, created_at)`、`(merge_view_id, created_at)`。

> - `file_record_id` 在合并视图下为 `NULL`（文件归属靠 items）；单文件下等于该文件 id。
> - `merge_view_id` 在单文件下为 `NULL`，合并视图下必填。**合并视图报告一律按 `merge_view_id` 检索，不按 `file_ids` 精确匹配**——这是本方案与数字专检的有意差异：视图内文件增减后历史报告依然可查。`file_ids` 仍然写入（记录当次检查覆盖了哪些文件，用于报告里如实呈现"本次检查包含 N 个文件"），但不作为检索键。
> - 单文件下 `file_ids` 也写单元素数组，保持序列化逻辑统一。

### 6.2 `translation_review_report_items`

一条 = 一个 finding。同一句段可有多条不同 `category_key`（这是本模块相对现有专检的核心差异）。

```
id                    UUID PK
report_id             UUID FK translation_review_reports ON DELETE CASCADE
project_id            UUID FK projects ON DELETE CASCADE (nullable)
file_record_id        UUID FK file_records ON DELETE CASCADE
segment_id            UUID FK segments ON DELETE SET NULL (nullable)
sentence_id           VARCHAR(100) DEFAULT ''
file_name             VARCHAR(255) DEFAULT ''
file_order            INTEGER      DEFAULT 0            -- 该文件在 view.file_ids 中的下标；单文件恒 0
display_index         INTEGER      DEFAULT -1
sequence_index        INTEGER      DEFAULT -1           -- 复制自 segment，参与排序
category_key          VARCHAR(40)  DEFAULT ''           -- tense | symbols | casing | ...
category_index        INTEGER      DEFAULT 0
rule_ref              VARCHAR(20)  DEFAULT ''           -- "2.8"
severity              VARCHAR(20)  DEFAULT 'error'      -- error | warning | suggestion
origin                VARCHAR(10)  DEFAULT 'ai'         -- program | ai
source_text           TEXT         DEFAULT ''
target_text           TEXT         DEFAULT ''
quote                 TEXT         DEFAULT ''
quote_start           INTEGER      DEFAULT -1
quote_end             INTEGER      DEFAULT -1
locate_status         VARCHAR(20)  DEFAULT 'ok'         -- ok | normalized | unlocatable | ambiguous
replace_anchor        TEXT         DEFAULT ''
suggested_value       TEXT         DEFAULT ''
suggested_target_text TEXT         DEFAULT ''
reason                TEXT         DEFAULT ''
confidence            VARCHAR(10)  DEFAULT 'medium'     -- high | medium | low
citations             TEXT JSON    DEFAULT '[]'         -- 联网查证引用
apply_mode            VARCHAR(10)  DEFAULT 'manual'     -- anchor | full | manual
original_target_text  TEXT         DEFAULT ''           -- 应用前快照，用于恢复
applied               BOOLEAN      DEFAULT FALSE
applied_at            TIMESTAMP    NULL
apply_batch_id        UUID         NULL                 -- 批量应用分组，用于"撤销上一次批量"
status                VARCHAR(20)  DEFAULT 'open'       -- open | applied | rejected | ignored | stale
ignored_by_id         UUID FK users ON DELETE SET NULL
ignored_at            TIMESTAMP    NULL
block_index           INTEGER      DEFAULT 0
row_index             INTEGER      NULL
cell_index            INTEGER      NULL
created_at            TIMESTAMP    DEFAULT NOW()
```

索引：`report_id`、`project_id`、`file_record_id`、`segment_id`、`category_key`、`status`、`(report_id, sentence_id)`、`apply_batch_id`，以及排序主索引
`(report_id, file_order, block_index, row_index, cell_index, sequence_index, sentence_id, category_index)`。

### 6.3 `translation_review_agent_runs`

每个类别一条，排障用。没有它就无法区分"该类别检查通过"和"该类别调用失败"。

```
id                    UUID PK
report_id             UUID FK translation_review_reports ON DELETE CASCADE
category_key          VARCHAR(40)
category_index        INTEGER      DEFAULT 0
mode                  VARCHAR(20)  DEFAULT ''
input_segment_count   INTEGER      DEFAULT 0            -- 送进本类别的句段数
ai_input_count        INTEGER      DEFAULT 0            -- 短路过滤后真正送 LLM 的条数
batch_count           INTEGER      DEFAULT 0
llm_request_count     INTEGER      DEFAULT 0
retry_count           INTEGER      DEFAULT 0
web_search_requests   INTEGER      DEFAULT 0
program_finding_count INTEGER      DEFAULT 0
ai_finding_count      INTEGER      DEFAULT 0
dropped_count         INTEGER      DEFAULT 0            -- sid 校验失败 / 无法定位被丢弃
status                VARCHAR(30)  DEFAULT 'ok'
                      -- ok | skipped_no_candidate | parse_failed | api_error | partial | failed
error_message         TEXT         DEFAULT ''
provider              VARCHAR(40)  DEFAULT ''
model                 VARCHAR(200) DEFAULT ''
started_at            TIMESTAMP    NULL
finished_at           TIMESTAMP    NULL
```

索引：`report_id`、`(report_id, category_key)`、`status`。

---

## 7. 任务调度与进度协议

### 7.1 为什么不用 SSE

10 个 Agent 扫全文要几分钟到十几分钟。挂在 HTTP 长连接上有四个隐患：网关读超时、部署重启即丢、关页面即中断、刷新无法恢复。现有数字专检能用 SSE 是因为它只对"程序判错的少数句段"做 AI 复核，量级完全不同。

同时说明：同进程内 10 个 Agent 做进度 fan-in，`asyncio.Queue` 就够，**不需要 Redis Pub/Sub**——Pub/Sub 解决的是跨进程问题，本设计里进度已经落库，读库即可。

### 7.2 调度流程

```
POST /api/file-records/{id}/translation-review-tasks
  1. 校验读权限 + 文件未被编辑锁定
  2. 先同步落一条 report(status='running', progress={})，拿到 report_id
  3. _enqueue_arq_job("translation_review_job", str(report_id),
                      queue_name=ARQ_TRANSLATION_REVIEW_QUEUE_NAME)
     └─ 返回 False（未启用 arq / 无 redis）→ 回退 asyncio 后台任务本地执行
  4. 立即返回 { task_id, report_id }
```

新增队列常量 `ARQ_TRANSLATION_REVIEW_QUEUE_NAME = "arq:translation-review"`，worker 函数 `translation_review_job(ctx, report_id: str)`，注册进现有 worker 配置。

### 7.3 进度快照结构（`report.progress`）

worker 每完成一个批次就更新一次（`UPDATE ... SET progress = ...`，不影响 items）：

```json
{
  "phase": "program | ai | aggregate | done",
  "overall_percent": 42,
  "current_category": "casing",
  "current_file_name": "广东省情概貌.docx",
  "categories": [
    {"key": "tense", "label": "时态", "status": "done",
     "current": 84, "total": 84, "finding_count": 7},
    {"key": "symbols", "label": "英文符号", "status": "running",
     "current": 30, "total": 84, "finding_count": 12},
    {"key": "casing", "label": "大小写", "status": "pending",
     "current": 0, "total": 0, "finding_count": 0}
  ],
  "files": [
    {"file_id": "…", "file_name": "广东省情概貌.docx",
     "done_categories": 3, "total_categories": 10, "finding_count": 19}
  ],
  "updated_at": "2026-07-29T10:11:12"
}
```

`categories[].total` 是该类别**跨所有文件**的句段总数；`files` 数组仅在合并视图下有多个元素，用于让用户看出"卡在哪个文件"。

前端进度条：主进度 = 已完成类别数 / 总类别数（或 `overall_percent`），副进度 = 当前类别批次进度。

### 7.4 轮询端点

`GET /api/translation-review-tasks/{task_id}` 返回 `{ status, report_id, progress, error_message }`。
前端 1.5 秒轮询，`status in (completed, partial_failed, failed)` 时停止并拉取完整报告。首屏延迟约 1.5 秒，对几分钟的任务可忽略。

---

## 8. 并发、限流与退避

三层防护，缺一层就会 429。

### 第一层：全局并发闸门

`app/services/translation_review/llm_gate.py`，模块级 `asyncio.Semaphore(translation_review_max_concurrency)`（默认 **3**）。所有 Agent 的每次 LLM 调用必须穿过。

注意：这是**进程级**闸门。因为生成已挪进单一 arq worker（决策 6），它就是事实上的全局单点；若回退到 Web 进程本地执行且有多 uvicorn worker，实际并发是 `worker 数 × 3`，需在日志里提示。

### 第二层：令牌桶限流

同模块内实现异步令牌桶（`translation_review_requests_per_minute`，默认 **60**），在闸门之后、发请求之前 `await bucket.acquire()`。
Semaphore 限"同时在飞"，令牌桶限"每分钟总量"，平台限的通常是后者，两者不可互相替代。

### 第三层：429 感知的指数退避（需改 `llm_service.py`）

改动很小但必要：

1. `LLMRequestError` 增加 `status_code: int | None`、`retry_after: float | None`。
2. `_request_translation` 捕获 `httpx.HTTPStatusError` 时填入 `exc.response.status_code` 与 `Retry-After` 头；urllib 分支同理处理 `HTTPError.code`。
3. `request_chat_completion` 重试循环：
   - `status_code == 429` 或 `5xx` → 退避 `min(base * 2**attempt, 30) + random_jitter`，`Retry-After` 存在时优先采用；
   - 其他错误保持现有线性退避不变（不改动现有行为）。
4. **429 时不要立刻切 provider**，先在本 provider 退避重试；重试耗尽再 fallback。否则只是把突发流量搬到第二家。

这三层是通用能力，改完后 number-check、样式标记专检一并受益。抽 `agent_runner` 时让 number-check 复用同一套重试逻辑，避免两份实现。

**实施顺序上，这一层必须先于任何提示词编写完成**，否则第一次跑 10 个 Agent 就会被限流打断，无法分清是提示词问题还是限流问题。

---

## 9. 预检短路（Short-Circuit）

区分"完全短路"和"仅缩小输入"，取决于程序规则召回是否完整。

### 可完全短路（跳过 LLM）

程序规则能穷尽、AI 无增量的机械项：

- 类别 1：中文标点残留、`&`/`/` 空格、em/en dash、括号嵌套、电话括号
- 类别 3：0–9 拼写、句首阿拉伯数字、`percentage point(s)`、千分位、小数位数、度量衡缩写、`X-odd`
- 类别 4：缩写、`XXXXX("XX")`、`km`/`m3`、`southern Guangdong`、`Lyu`

这三类程序规则跑完后，只把"存在 AI 专属子规则"的句段送 LLM：
类别 1 只送含 `·` 的句段；类别 3 只送含 `"数字+数字"` 模式或经纬度的句段；类别 4 只送含疑似专名/机构名/地址的句段。
粗估这三类的 LLM 调用量可降到全文的 5%–15%。

### 仅缩小输入（程序规则是启发式，漏报多）

- 类别 2：程序处理标题相关规则，AI 只处理含双引号的句段
- 类别 7：仅"历史纪年+公元年双译"可程序化，其余必须 AI 看全句

### 无法短路

类别 0、5、6、8、9 纯语义判断，只能靠**范围过滤**降本（句段状态筛选）。

### 实现

`registry.py` 每个 Agent 的 `ai_input_filter(payloads) -> list[payload]`。返回空列表时整体跳过 LLM，`agent_runs.status = 'skipped_no_candidate'`，UI 显示"程序检查已完成，无需 AI 复核"，避免用户误以为漏跑。

---

## 10. 应用、撤销与批量

### 10.1 单条应用

```
apply_mode == 'anchor'：
  重新读取最新 target_text → 校验 replace_anchor 唯一命中
  → 替换 → update_segment_by_sentence_id(...)   # 自动产生 revision、version 自增
apply_mode == 'full'：
  整句替换为 suggested_target_text
apply_mode == 'manual'：
  无"应用"按钮，仅提示人工处理
```

应用前写 `original_target_text` 快照；"恢复"即写回快照并把 `status` 复位为 `open`。

### 10.2 多类别同句冲突

同一句段可能被多个类别同时提修改建议。批量执行时**按句段串行**，每条应用前重新读取最新 `target_text` 并重新校验锚点唯一性；失效的标 `status='stale'`，结果里单独列出"N 条因译文已变化需重新检查"。

### 10.3 批量入口（按"从安全到危险"排列，每个按钮显示影响条数）

1. **应用所有程序检查项**（`origin == 'program'`）—— 确定性规则，最安全，默认最显眼
2. **应用所有高置信度修改**（`confidence == 'high'` 且 `apply_mode == 'anchor'` 且 `locate_status in ('ok','normalized')` 且 `severity != 'suggestion'`）
3. **按类别批量应用** —— 类别 chip 上的"应用本类"，闸门同第 2 条
4. **应用选中项** —— 勾选后批量

### 10.4 统一硬闸门（任何批量入口都绕不过）

- `apply_mode == 'manual'` 或 `locate_status in ('unlocatable','ambiguous')` → 永不进入批量
- `severity == 'suggestion'`（类别 9 全部）→ 永不进入批量，必须逐条确认
- `confidence == 'low'` → 永不进入批量
- 已 `applied` / `ignored` / `rejected` / `stale` → 跳过

### 10.5 撤销上一次批量

每次批量应用生成一个 `apply_batch_id` 写入涉及的 items。窗口提供「撤销上一次批量应用」，按 `apply_batch_id` 逆序写回 `original_target_text`。
**没有这个撤销口，校对员不敢点批量按钮**，批量功能等于白做。

---

## 11. API 端点

```
# 生成（arq 任务 + 轮询）
POST   /api/file-records/{file_record_id}/translation-review-tasks
       body: { categories?: string[], segment_scope?: string,
               provider?: string, model?: string,
               web_verify?: 'none'|'openrouter'|'custom' }
       → { task_id, report_id }

GET    /api/translation-review-tasks/{task_id}
       → { status, report_id, progress, error_message }

# 报告
GET    /api/file-records/{file_record_id}/translation-review-reports?limit=1
GET    /api/translation-review-reports/{report_id}
       → 报告 + items + agent_runs 摘要
DELETE /api/translation-review-reports/{report_id}

# 重跑
POST   /api/translation-review-reports/{report_id}/rerun
       body: { category_keys?: string[], item_ids?: string[] }
       → 同样入队 arq，返回 { task_id }

# 处置
POST   /api/translation-review-report-items/{item_id}/apply
POST   /api/translation-review-report-items/{item_id}/restore
POST   /api/translation-review-report-items/{item_id}/reject
PATCH  /api/translation-review-report-items/ignore
       body: { item_ids: string[], ignored: bool }
POST   /api/translation-review-reports/{report_id}/apply-batch
       body: { mode: 'program'|'high_confidence'|'category'|'selected',
               category_key?: string, item_ids?: string[] }
       → { applied_count, stale_count, skipped_count, apply_batch_id }
POST   /api/translation-review-reports/{report_id}/undo-batch
       body: { apply_batch_id?: string }   # 缺省撤销最近一次
       → { restored_count }

# 导出
GET    /api/translation-review-reports/{report_id}/export-docx
GET    /api/translation-review-reports/{report_id}/export-xlsx      # 复用 xlsx_exporter
```

### 合并视图变体（决策 4）

与数字专检完全对称，复用 `_get_merge_view_context(db, view_id, current_user)`（返回 `(view, project, files)`）：

```
POST   /api/merge-views/{view_id}/translation-review-tasks
       body 同单文件版
       → { task_id, report_id }

GET    /api/merge-views/{view_id}/translation-review-reports?limit=1
       # 按 merge_view_id 检索（不用 file_ids 精确匹配）：
       #   scope == 'merge_view' AND merge_view_id == view_id
       #   ORDER BY created_at DESC, id DESC
```

其余端点（`translation-review-tasks/{task_id}`、`translation-review-reports/{report_id}`、
`rerun`、单条处置、`apply-batch`、`undo-batch`、`export-docx`、`export-xlsx`）**都是按 report_id / item_id 操作，单文件与合并视图共用，无需变体**。

权限：
- 单文件读取沿用 `_require_file_record_read_access` + `_resolve_file_record_project`；
- 合并视图沿用 `_get_merge_view_context`（内部已含权限校验）；
- `apply` / `apply-batch` / `undo-batch` 额外校验**目标句段所属文件**的写权限（合并视图内可能存在 `can_write == false` 的文件，见第 19 节）。

---

## 12. Word 报告

新增 `app/services/docx_report_exporter.py`，对标 `app/services/xlsx_exporter.py` 的结构：

```python
def build_translation_review_docx(report: dict, items: list[dict], runs: list[dict]) -> bytes
def build_docx_download_response(filename: str, docx_bytes: bytes) -> StreamingResponse
    # media_type: application/vnd.openxmlformats-officedocument.wordprocessingml.document
    # Content-Disposition: attachment; filename="ascii"; filename*=UTF-8''<quoted>
```

仓库已在多处使用 python-docx（`universal_exporter.py`、`quote_converter.py`、`adapters/docx_adapter.py` 等），无需新增依赖。

### 报告结构

1. **封面 / 概览**：范围（单文件名 / 合并视图名 + 文件数）、语言对（合并视图混合语言对时逐一列出）、句段总数、检查范围、检查时间、模型、联网状态与搜索次数、启用类别、问题总数
2. **文件汇总表**（仅合并视图）：文件名 / 语言对 / 句段数 / 问题数 / 已应用 / 已忽略
3. **类别汇总表**：类别 / 问题数 / 已应用 / 已忽略 / 检查状态（失败或跳过的类别高亮标注）；合并视图下再附一张"类别 × 文件"交叉表
4. **明细**：
   - 单文件：按类别分节
   - 合并视图：**按文件分节 → 节内按类别分组**（避免不同文件的问题混排导致无法逐文件校对）
   每条含句段号、原文、译文（问题片段高亮）、违反规则条款号、建议、置信度、处理状态；联网查证项附引用链接
5. **按句段汇总**（重点，回应"一个句子可能有多个类别问题"）：只列命中 ≥2 个类别的句段，标注所属文件，原文/译文各一行，下面并列该句所有类别的问题
6. **附录 A：规则条款索引**（编号 → 规则原文摘要），让审校人能回查依据
7. **附录 B：检查执行情况**（`agent_runs` 摘要），标出哪些类别未完成、丢弃了多少条

### 高亮实现

按 item 的 `quote_start` / `quote_end` 偏移把 `target_text` 切成三段 run，中间段设 `font.highlight_color`（或加粗+下划线）。`locate_status` 非 ok 的项不高亮，只在"问题片段"列以文本形式给出。

---

## 13. 前端改动

### 13.1 新增文件

- `frontend/src/api/translationReview.ts` —— 照 `numberCheck.ts` / `styleTagCheck.ts` 写客户端
- `frontend/src/types/api.ts` 增加类型：
  `TranslationReviewReport`、`TranslationReviewReportItem`、`TranslationReviewCategoryStat`、`TranslationReviewProgress`、`TranslationReviewAgentRun`
- `frontend/src/components/TranslationReviewPanel.vue` —— 校对窗口面板，单文件与合并视图共用（合并视图下多出"文件"列与文件筛选）

### 13.2 `WorkbenchView.vue` 改动

1. `BottomToolKey` 增加 `'translation-review'`
2. 「AI能力」下拉菜单加第三项 **「翻译内容校对」**（与数字专检、样式标记专检并列）
3. 新底部抽屉渲染 `TranslationReviewPanel`
4. **合并视图下正常可用**：生成时走 `/merge-views/{viewId}/translation-review-tasks`，按 `isMergeWorkbench` 分支选择端点（照 `openNumberCheck` 的写法）

### 13.3 面板布局

**顶部工具条**
- 生成/重新检查按钮 + 类别级进度条（生成中显示"正在检查：大小写 30/84"）
- 概览：待处理 / 已应用 / 已忽略 / 多类别句段数 / 联网搜索次数
- 批量按钮组（10.3 的四个，各带条数）+「撤销上一次批量」
- 「导出 Word」/「导出 Excel」
- 「设置」

**筛选行**
- 类别 chips（多选，每个显示该类别问题数；跳过/失败的类别标灰并加图标）
- **文件筛选下拉（仅合并视图，含"全部文件"）**
- 严重程度筛选（error / warning / suggestion）
- 处理状态筛选（待处理 / 已应用 / 已忽略 / 已拒绝 / 需重查）
- 置信度筛选（high / medium / low）
- **视图切换：按问题列表 ↔ 按句段分组**（后者把同句段的多类别问题折叠成一组，是本模块的核心差异视图）

**表格列**
句段号 / **文件（仅合并视图，照 QA 结果面板的 `isMergeWorkbench` 条件列）** / 类别 / 规则号 / 原文 / 译文（问题片段高亮）/ 问题与建议 / 置信度 / 状态 / 操作（应用·拒绝·忽略·跳转到句段）

**设置弹窗**
- 启用类别多选（默认全选）
- 检查范围：全部句段（默认）/ 仅有译文 / 仅未确认 / 仅已确认
- 模型选择（复用 `llmModelOptions`）
- 联网查证：关闭（默认）/ OpenRouter 联网 / 自研插件；选联网时提示"需使用 OpenRouter 模型，会产生额外搜索费用"
- 并发度与每分钟请求数（高级项，默认 3 / 60）

### 13.4 交互复用

- 跳转句段复用现有 `focusEditorSegmentAtIndex`：单文件走 `/file-records/{id}/segments/{sid}/position`，合并视图走 `fetchMergeViewSegmentPosition(viewId, fileRecordId, sentenceId)`（照 `focusNumberCheckReportItem` 的分支写法）；合并模式下句段 key 是复合键 `fileId:sentenceId`，用 `segmentKeyOf` 生成，不能直接用 `sentence_id`
- 下载复用 `downloadBlob` / `resolveDownloadFilename`
- 轮询用 `window.setTimeout` 递归（照 `waitForFileExportTask` 的写法），组件卸载时清理
- **前端不做排序**：items 由服务端按第 19.5 节的排序键排好后返回，前端只做筛选（不同于 QA 结果面板在前端排序的做法）

---

## 14. 新增配置项（`app/config.py`）

```python
translation_review_max_concurrency: int = 3
translation_review_requests_per_minute: int = 60
translation_review_batch_size_default: int = 12
translation_review_default_segment_scope: str = "all"
translation_review_web_search_provider: str = "none"        # none | openrouter | custom
translation_review_web_search_engine: str = "auto"          # auto|native|exa|firecrawl|parallel|perplexity
translation_review_web_search_max_results: int = 5
translation_review_web_search_max_uses: int = 2
translation_review_web_allow_domains: list[str] = []
translation_review_web_search_custom_url: str | None = None
translation_review_web_search_custom_api_key: str | None = None
```

---

## 15. 文件清单

```
app/services/translation_review/
  __init__.py
  registry.py                    # 10 个 Agent 声明
  payload.py                     # SegmentPayload 装配（按文件分组/上下文/术语/记忆/标题判定）
  llm_gate.py                    # 进程级 Semaphore + 令牌桶
  agent_runner.py                # 批次切分、seq+sid 校验、缺失补发、JSON 解析
  aggregator.py                  # 去重、锚点复验、偏移量、排序键计算、按句段聚合
  orchestrator.py                # 串起 10 个 Agent + 进度写库
  service.py                     # create_task / run_job / rerun
                                 # apply / restore / reject / ignore
                                 # apply_batch / undo_batch
                                 # serialize_report / serialize_item
  program_rules/
    __init__.py
    symbols.py                   # 类别 1 机械项
    casing.py                    # 类别 2 标题相关
    number_format.py             # 类别 3 机械项
    proper_noun.py               # 类别 4 机械项
    omission.py                  # 类别 7 纪年双译启发式
  web_verify/
    base.py                      # WebVerifier Protocol + WebVerifyResult
    openrouter.py                # openrouter:web_search server tool
    custom.py                    # 自研插件预留
    noop.py
  prompts/
    __init__.py
    00_tense.py … 09_syntax.py   # 规则摘录 + few-shot + schema + 边界声明

app/services/docx_report_exporter.py        # 新增

修改：
  app/models.py                  # 3 个新模型
  app/services/schema_setup.py   # 3 张表 + 索引 DDL
  app/schemas.py                 # Pydantic 模型
  app/routers/api.py             # 端点 + ARQ_TRANSLATION_REVIEW_QUEUE_NAME
  app/services/llm_service.py    # 429 退避 + status_code/retry_after + tools/extra_body
  app/config.py                  # 新配置
  <arq worker 配置>              # 注册 translation_review_job

frontend:
  src/api/translationReview.ts                     # 新增
  src/components/TranslationReviewPanel.vue        # 新增
  src/types/api.ts                                 # 新类型
  src/views/WorkbenchView.vue                      # AI能力下拉 + 抽屉接线
```

---

## 16. 分阶段实施步骤（已完成标注）

### 阶段 1 — 地基 ✅

1. ✅ **确认标题信息来源**：`segment_metadata` 无段落样式字段，`block_type` 不区分标题。代码里用启发式（无句末标点 + 词数 ≤25 + 非 table_cell）；提示词里让模型二次判断。
2. ✅ **建三张表**：`translation_review_reports`（30 列/8 索引）、`translation_review_report_items`（39 列/9 索引）、`translation_review_agent_runs`（20 列/3 索引）。ORM + `schema_setup.py` 双写，本地验证成功。
3. ✅ **5 个程序规则模块**：`symbols.py`、`casing.py`、`number_format.py`、`proper_noun.py`、`omission.py`，规则文档例句作测试全部通过。
4. ✅ **Registry**：10 个 Agent 声明 + `ai_input_filter` 短路逻辑。
5. ✅ **Service 核心**：建报告、程序规则运行、应用/恢复/拒绝/忽略、批量应用（4 模式）/撤销批量、序列化。
6. ✅ **API 端点（阶段 1）**：单文件 + 合并视图任务创建、报告查询、任务进度、逐条处置、批量操作，共 14 个端点。

### 阶段 2 — LLM 基础设施 ✅

7. ✅ **`llm_service.py` 改造**：`LLMRequestError` 加 `status_code`/`retry_after`；`_request_translation` 透出状态码 + `Retry-After` 头；`request_chat_completion` 加 `tools`/`extra_body`；429/5xx 指数退避 + jitter（Retry-After 优先）；`LLMChatCompletionResult` 加 `annotations`/`web_search_requests`；`_extract_translation_from_payload` 在有 tool 注释时返回 dict。
8. ✅ **`llm_gate.py`**：进程级 Semaphore + 令牌桶，配置化（`translation_review_max_concurrency=3`，`translation_review_requests_per_minute=60`）。
9. ✅ **`agent_runner.py`**：通用批次调用 + seq/sid 双重校验 + 缺失补发重试。
10. ✅ **`config.py`**：11 个新配置项。

### 阶段 3 — Agent 逐个上线 ✅

11. ✅ **10 个提示词模块**（`prompts/00_tense.py` … `09_syntax.py`）：规则原文摘录 + 正反例 few-shot + 统一输出 schema + 边界声明。
12. ✅ **`orchestrator.py`**：串联 10 个 Agent，程序规则 → AI → 短路过滤 → 锚点定位 → 进度写库 → 聚合落库。
13. ✅ **`translation_review_job`**（arq worker 入口）+ `TranslationReviewWorkerSettings`；local fallback 改为调用全量 orchestrator。

### 阶段 4 — 联网查证 ✅（接口层）

14. ✅ `web_verify/base.py` + `noop.py` + `openrouter.py`（含 system hint 注入）+ `custom.py`（预留协议）。实际联网调用由 `orchestrator._build_web_tools()` 构造 OpenRouter `openrouter:web_search` tool。

### 阶段 5 — 报告与前端 ✅

15. ✅ **`docx_report_exporter.py`**：五节报告结构（概览 + 类别汇总 + 按类别明细 + 多类别汇总 + 附录）；`build_docx_download_response()` 处理中文文件名。
16. ✅ **`export-docx`、`export-xlsx`、`rerun` 端点**（共 3 个）。
17. ✅ **前端 API 客户端**：`translationReview.ts`（任务、轮询、报告、单条/批量、导出、重跑）。
18. ✅ **类型**：`TranslationReviewReport`、`Item`、`AgentRun`、`CategoryStat`、`Progress`（`frontend/src/types/api.ts`）。
19. ✅ **`TranslationReviewPanel.vue`**：类别 chips、文件筛选（合并视图）、列表/分组双视图、译文高亮、程序批量/高置信度批量/撤销批量、进度条、设置弹窗、Word/Excel 导出；`onActiveCountChange` prop 同步 badge 计数到 `WorkbenchView`。
20. ✅ **`WorkbenchView.vue`**：`BottomToolKey` + `BottomDrawerToolKey` + `is-wide`；AI能力下拉第三项「翻译内容校对」；`selectAiCapability` 处理新 key；`translationReviewActiveCount` ref；底部抽屉内嵌面板；合并视图下复合 key 跳转。

### 阶段 5.5 — 合并视图接线 ✅（已随阶段 5 一并完成）

后端 2 个变体端点（`/merge-views/{view_id}/translation-review-tasks`、`...-reports`）、`merge_view_id` 字段、按文件分组批次、只读文件降级 apply_mode、文件级排序键均已实现。

### 阶段 6 — 调优（待执行）

22. 成本与耗时实测，逐类别调 `batch_size`、调并发与 RPM。
23. 项目级配置：默认启用哪些类别（存进现有 project quality settings）。
24. 端到端测试：单文件 + 合并视图各一组含典型错误的样例文件。

---

## 17. 风险与成本

### 调用量估算

纯 AI 类别 7 个（0、5、6、7、8、9 + 4 的 AI 部分）。1000 句段、`batch_size=12` 时约 `7 × 84 ≈ 590` 次请求；短路后 1、3、4 三类合计再增加约 30–80 次。按 RPM 60 与并发 3 计算，单次全量检查约 10–15 分钟。

降本手段优先级：
1. 检查范围筛选（只查未确认句段）——效果最大
2. 类别按需启用（关掉 `syntax_polish` 能省约 1/7）
3. 预检短路
4. 增大 `batch_size`（但会降低单条判断质量，需权衡）

### 主要风险

| 风险 | 对策 |
|---|---|
| 429 限流打断 | 三层防护（第 8 节）；实施顺序上先做基础设施 |
| AI 误报（尤其类别 8、9） | `confidence` 字段 + 默认只展示 high/medium + suggestion 类不进批量 + 每条带 `rule_ref` 可回查 |
| 高亮/替换位置错误 | 五道定位关卡（第 5 节），锚点必须唯一命中，否则禁用应用 |
| 批量改坏译文 | 快照 + `update_segment_by_sentence_id`（自带 revision）+ `apply_batch_id` 撤销 + 硬闸门 |
| 多类别同句冲突 | 串行应用 + 每次重读最新译文 + 锚点复验 + `stale` 标记 |
| 联网成本失控 | 默认关闭 + 仅 `proper_noun` + `max_uses`/`max_total_results`/`max_tool_calls` 限额 + 用量回显 |
| 合并视图跨文件语境串味 | 批次按文件分组，绝不混批；术语上下文按文件解析（第 19.1/19.2 节） |
| 合并视图耗时成倍增长 | 文件数 × 类别数决定总量，设置里默认建议缩小检查范围；进度按文件回显便于中途判断 |
| 规则文档更新后漂移 | 提示词里保留条款编号与原文摘录，集中在 `prompts/`，不散写进代码 |
| 生产环境缺表 | ORM 与 `schema_setup.py` 双写，部署检查清单里加一条验证 |

---

## 18. 未决 / 后续可做

- **项目级提示词覆盖**：v1 提示词是代码文件；若需要非开发人员调优，后续可加 DB 覆盖层（project 级），沿用 guideline template 的思路。
- **术语库回写**：类别 4 查证出的官方译法，可提供"一键加入术语库"入口（复用现有 `POST /term-bases/{id}/entries`）。
- **跨文件术语一致性检查**：合并视图下可进一步检查"同一专有名词在不同文件里译法不一致"。这属于规则文档之外的新类别（现有 4.11 只管同句内同拼音地名），建议作为第 11 个可选 Agent 后续追加，v1 不做。
- **`custom` 联网插件的具体协议**：待定，当前只预留接口与配置项。

---

## 19. 合并视图支持的额外约束

合并视图不是"单文件跑 N 遍"，有五处必须特殊处理，否则会出隐性错误。

### 19.1 批次不得跨文件混排

合并视图里各文件的**语言对可能不同**（`mergeViewDetail.is_mixed_language_pair`），且各文件绑定**不同的术语库/记忆库**。因此：

- `PayloadBuilder` 先按 `file_record_id` 分组，**每个文件独立成批**，一个 LLM 批次内只包含同一个文件的句段。
- 每批的 system 提示词里注入该文件的语言对与术语上下文。
- 混批会让模型把 A 文件的语境套到 B 文件上，是最容易出现的隐性错误来源。

代价是批次利用率下降（小文件可能凑不满 `batch_size`）。可接受：正确性优先。若某文件候选句段极少，允许该文件单独成一个不满批的请求，不与其他文件拼接。

### 19.2 术语/记忆上下文按文件解析

类别 4 需要的绑定资源要按每个 `FileRecord` 各自解析（`collection_ids` / `term_base_ids` / `qa_term_base_ids`），不能取视图级并集——并集会把 A 文件不该用的术语喂给 A 文件。

### 19.3 只读文件的处理

合并视图内可能存在 `can_write == false` 的文件（流程阶段未流转到当前用户）。约定：

- **检查照做**（只读文件也要出报告，供审校查看）；
- 但其 items 的 `apply_mode` 一律降级为 `manual`，"应用"按钮禁用，tooltip 提示"当前流程阶段无编辑权限"；
- 批量应用时这些项直接跳过，并计入返回的 `skipped_count`，UI 明确告知"N 条因无写权限被跳过"。

### 19.4 报告归属与检索

- `scope = 'merge_view'`，`merge_view_id = view.id`，`file_ids = serialize_file_ids([f.id for f in files])`（顺序与 `_get_merge_view_context` 返回一致），`total_files = len(files)`。
- **列表检索按 `merge_view_id`**，不用 `file_ids` 精确匹配。这是与数字专检的有意差异：数字专检用 `file_ids == file_ids_text` 精确匹配，视图内文件增减后历史报告就查不到；本模块改为 `merge_view_id`，历史报告始终可查。
- 视图文件发生变动时，旧报告里可能包含已被移出视图（或已删除）的文件。序列化时对每个 item 标记 `file_in_view: bool`，面板上这类项归入"已移出视图"分组并禁用"应用"（`apply_mode` 降级为 `manual`），避免往已不在当前视图的文件里写译文。
- `report.file_record_id` 在合并视图下留空（`NULL`），文件归属靠 item 的 `file_record_id` / `file_name`。

### 19.5 排序规则：按视图文件顺序，再按句段位置

合并视图内的文件顺序由 `view.file_ids` 决定（`load_view_file_records` 按该顺序去重保序加载），也就是用户在工作台里看到的文件顺序。报告排序必须与之一致，否则报告顺序和编辑器顺序对不上，逐条校对时会来回跳。

**统一排序键**（服务端 `serialize_report` 里排好，前端不再二次排序）：

```
1. file_order        -- 该文件在 view.file_ids 中的下标；不在视图内的排最后
2. block_index
3. row_index         -- NULL 视为 -1
4. cell_index        -- NULL 视为 -1
5. sequence_index    -- 句段在源文件中的权威顺序；-1（历史数据）时退化到下一级
6. sentence_id       -- 自然序（numeric 比较）
7. category_index    -- 同一句段内多个类别时，按规则文档章节号 0→9 排
```

实现要点：

- 落库时把 `file_order` 一并写进 item（新增 `file_order INTEGER DEFAULT 0` 字段），避免每次序列化都要重新查视图并计算下标。
- 前 6 级保证"报告顺序 == 编辑器里从上到下的顺序"；第 7 级保证同一句段的多个问题按类别编号稳定排列（时态 → 符号 → 大小写 → …），不会因为 Agent 完成先后而抖动。
- **单文件模式同样适用**这套排序键，只是 `file_order` 恒为 0，逻辑不分叉。
- 「按句段分组」视图下，组的顺序用前 6 级，组内条目用第 7 级。
- Word 报告的"按文件分节"直接复用 `file_order` 分节，节内顺序同上。
- QA 结果面板已有类似排序（`normalizeTermQAReportForCurrentWorkbench` 在前端按 fileOrder + block/row/cell + sentence_id 排），本模块把这段逻辑上移到服务端，前端不再重排——避免前后端两套排序规则不一致。

### 19.6 进度与统计粒度

- `progress.categories[].total` 是**该类别跨所有文件的句段总数**，另加 `progress.files` 数组给出每个文件的完成情况，便于用户判断"卡在哪个文件"。
- `report.category_counts` 之外增加 `file_counts`（`{file_id: 问题数}`），供面板文件筛选下拉直接显示数量，避免前端二次聚合。
- `agent_runs` 仍是**每类别一条**（不按文件拆），文件级失败信息写进 `error_message`。理由：类别是重跑的最小单位，按文件拆会让重跑逻辑复杂化。

### 19.7 对实施步骤的影响

合并视图变体放在**阶段 5 之后、阶段 6 之前**单独一小步：单文件链路全部验证通过后，再加 2 个端点 + 前端分支 + Word 报告的文件分节。核心服务层（Orchestrator / Aggregator / apply）从第一天就按"多文件列表"设计（`files: list[FileRecord]`，与 `create_number_check_report` 的签名一致），所以这一步只是接线，不改核心逻辑。
