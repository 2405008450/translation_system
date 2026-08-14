from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET_KEY = "change-me-in-production"


class Settings(BaseSettings):
    app_name: str = "Translation Memory Demo"
    app_version: str | None = None
    tz: str = "Asia/Shanghai"
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/tm_demo" 
    )
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout: int = 60
    database_pool_recycle: int = 1800
    # 当通过 PgBouncer 等事务级连接池中间件访问数据库时置为 True：
    # 会关闭 psycopg 的服务端预备语句，并放弃会话级时区设置（改由服务端默认时区保证）。
    database_pgbouncer_transaction_mode: bool = False
    # 写入每个数据库连接的 application_name，便于在 pg_stat_activity 中区分来源。
    database_application_name: str = "ai-translation"
    # 同步接口/后台任务运行所用线程池大小（anyio 默认 40）。设置时应与每进程数据库
    # 连接池容量(pool_size + max_overflow)及 PgBouncer 池大小协调，避免线程数远超可用连接。
    server_threadpool_size: int | None = None
    term_import_preview_max_scan_rows: int = 5000
    # 资源库导入预览只扫描前 N 行，避免几十万行 Excel 在预览阶段占满内存。
    resource_import_preview_max_scan_rows: int = 1000
    # 资源库实际导入的写库批次大小，控制单批 ORM 对象和向量同步 payload 的内存峰值。
    resource_import_batch_size: int = 1000
    resource_import_max_size_mb: int = 1024
    file_storage_dir: str = "data/file_records"
    export_task_dir: str = "data/export_tasks"
    import_task_dir: str = "data/import_tasks"
    import_task_staging_ttl_seconds: int = 86400
    # 全局 fallback；实际单文件上限以各格式 adapter 的 FORMAT_SIZE_LIMITS 为准。
    upload_max_size_mb: int = 100
    upload_max_files_per_batch: int = 50
    upload_max_total_size_mb: int = 500
    upload_max_expanded_files: int = 100
    default_similarity_threshold: float = 0.80
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "google/gemini-3-flash-preview"
    online_term_search_engine: str = "auto"
    online_term_search_model: str | None = None
    online_term_search_max_results: int = 5
    online_term_search_max_uses: int = 2
    llm_timeout_seconds: float = 60.0
    llm_stall_timeout_seconds: float = 120.0
    llm_max_concurrency: int = 5
    llm_temperature: float = 0.2
    llm_retry_attempts_per_provider: int = 2
    languagetool_base_url: str | None = None
    languagetool_timeout_seconds: float = 10.0
    languagetool_max_text_length: int = 20000
    # 工作台实时拼写检查使用更短的超时和独立并发上限，避免交互请求拖慢 Web worker。
    languagetool_live_timeout_seconds: float = 2.5
    languagetool_live_max_concurrency_per_worker: int = 2
    languagetool_live_queue_timeout_seconds: float = 0.3
    languagetool_live_cache_ttl_seconds: int = 300
    # 默认关闭保存/确认后的自动拼写语法 QA，避免低优先级检查挤占上传导入队列。
    spelling_grammar_qa_auto_schedule: bool = False
    # LLM settings for reference analysis.
    reference_llm_provider: str = "openrouter"
    reference_llm_api_key: str | None = None
    reference_llm_model: str = "google/gemini-3.5-flash"
    reference_llm_base_url: str = "https://openrouter.ai/api/v1"
    # 双文档对齐的可选跨语言语义证据。未配置或调用失败时自动退回纯程序 DP。
    alignment_embedding_enabled: bool = False
    alignment_embedding_api_key: str | None = None
    alignment_embedding_base_url: str | None = None
    alignment_embedding_model: str = "google/gemini-embedding-2"
    alignment_embedding_timeout_seconds: float = 30.0
    alignment_embedding_dimensions: int | None = 768
    alignment_embedding_batch_size: int = 100
    alignment_embedding_concurrency: int = 2
    alignment_embedding_window_blocks: int = 32
    alignment_llm_refinement_enabled: bool = True
    # 向量候选完成后，将全部键值对分块交给指定 LLM 做边界复核。
    alignment_llm_full_review_enabled: bool = False
    alignment_llm_full_review_model: str = "google/gemini-3.7-flash"
    alignment_llm_full_review_max_pairs: int = 28
    alignment_llm_full_review_max_chars: int = 18000
    alignment_llm_full_review_table_max_pairs: int = 24
    alignment_llm_full_review_table_max_chars: int = 9000
    alignment_llm_full_review_overlap_pairs: int = 2
    alignment_llm_full_review_retry_min_pairs: int = 4
    alignment_llm_full_review_max_output_tokens: int = 4096
    alignment_llm_full_review_concurrency: int = 4
    # 样式标记专检：AI 自动标注默认使用的 provider/model；留空 model 时沿用
    # request_chat_completion 的通用默认（DEEPSEEK_MODEL / OPENROUTER_MODEL）。
    # 前端设置面板若显式选择模型会覆盖此处配置。
    style_tag_check_llm_provider: str = "auto"
    style_tag_check_llm_model: str | None = None
    redis_url: str | None = None
    import_queue_backend: str = "local"
    arq_max_jobs: int = 1
    # 为空或 0 时继承 ARQ_MAX_JOBS；生产环境可单独设置各队列并发。
    arq_import_max_jobs: int | None = None
    arq_maintenance_max_jobs: int | None = None
    arq_auto_tm_max_jobs: int | None = None
    arq_segment_sync_max_jobs: int | None = None
    arq_pretranslation_max_jobs: int | None = None
    # 单个项目预翻译批次内，同时处理的文件任务数。仍会叠加 LLM_MAX_CONCURRENCY 的限制。
    pretranslation_run_file_concurrency: int = 2
    auto_tm_outbox_max_batches_per_run: int = 5
    auto_tm_rematch_max_files_per_run: int = 1
    auto_tm_rematch_force_immediate: bool = False
    auto_tm_rematch_enable_fuzzy: bool = False
    auto_tm_rematch_count_threshold: int = 200
    auto_tm_rematch_delay_minutes: int = 15
    auto_tm_rematch_chunk_size: int = 100
    aspose_words_license_path: str | None = None
    libreoffice_soffice_path: str | None = None
    libreoffice_python_path: str | None = None
    libreoffice_timeout_seconds: float = 60.0
    # PPTX 版式优化：视觉复核统一走的 LLM provider（需支持多模态 image_url，如 openrouter）。
    pptx_layout_vlm_provider: str = "openrouter"
    # PPTX 版式优化：可选视觉模型列表（前端下拉与后端校验共用；从 env 传 JSON 数组）。
    pptx_layout_vlm_models: list[str] = Field(
        default_factory=lambda: [
            "google/gemini-3.1-pro-preview",
            "google/gemini-3.6-flash",
            "anthropic/claude-sonnet-5",
            "anthropic/claude-fable-5",
        ]
    )
    # PPTX 版式优化：默认视觉模型（留空则取模型列表首个）。
    pptx_layout_vlm_model: str = "google/gemini-3.1-pro-preview"
    # PPTX 版式优化：LibreOffice 渲染页面截图的 DPI。
    pptx_layout_render_dpi: int = 200
    # ODA File Converter（DWG <-> DXF 跨平台转换器，需用户自行安装）
    # Windows 例：C:\Program Files\ODA\ODAFileConverter 25.12.0\ODAFileConverter.exe
    # Linux 例：  /usr/bin/ODAFileConverter
    oda_converter_path: str | None = None
    oda_converter_timeout_seconds: float = 120.0
    # DWG 导出默认输出 DXF；启用后才尝试回写 DWG（依赖 ODA）
    dwg_export_to_dwg: bool = False
    # DWG 转换出的 DXF 目标版本（ACAD2018 兼容性最好）
    oda_converter_dxf_version: str = "ACAD2018"
    # DWG 专用：处理 MULTILEADER / ACAD_TABLE 等扩展实体（仅 DWG 链路启用）
    dwg_handle_extra_entities: bool = True
    # DWG 专用：跳过纯尺寸/坐标式文本（如 "4-100×100"）避免误送翻译
    dwg_skip_dimension_like: bool = True
    # DWG 专用：译文超长时按比例缩字宽因子 / MTEXT 字高
    dwg_enable_overflow_shrink: bool = True
    # DWG 专用：字宽因子下限
    dwg_min_width_factor: float = 0.55
    # DWG 专用：字高最低缩到原值的比例
    dwg_min_char_height_ratio: float = 0.5
    # DWG 专用：为非 ASCII 字符（西班牙语/法语等带重音字母）切换到支持 Unicode 的字体
    dwg_fix_shx_font_for_unicode: bool = True
    # DWG 专用：替换 SHX 字体的 TrueType 字体名称
    dwg_unicode_font_name: str = "Arial"
    # DWG 专用：启用空间聚类合并（将同一行的多个 TEXT 实体合并为语义完整的句子）
    dwg_enable_spatial_merge: bool = True
    # DWG 空间合并：语义边界检测（句号 + 大写/中文开头等判断成新一句）
    dwg_enable_semantic_break: bool = True
    # DWG 空间合并：L4 逻辑分组硬门槛（同 style、字高一致、同 tag/INSERT 才可合并）
    dwg_enable_logical_grouping: bool = True
    # DWG 空间合并：允许字高差异比例（超过则视为不同文本流），默认 30%
    # 中英文混排下"数字/字母比中文小 20%"极常见，10% 会导致同一行完全断开
    dwg_height_ratio_tolerance: float = 0.30
    # DWG 空间合并：段落续行的 y 间距上限（× avg 字高）。有 L2 网格线阻挡后可放宽到 3.0
    dwg_next_line_gap_factor: float = 3.0
    # DWG 空间合并 L1：按打分贪心合并（拒绝"桥接式误合"），默认开启
    dwg_enable_greedy_merge: bool = True
    # DWG 空间合并 L1：弱边分数下限，低于该分的边不参与合并
    dwg_min_edge_score: float = 0.15
    # DWG 空间合并 L3：bbox IoU 超过此值直接拒（防重叠标注误合），默认 0.5
    dwg_iou_split_threshold: float = 0.5
    # DWG 空间合并 L5：用 Gemini Flash 对低置信度合并做语义复核
    dwg_llm_verify_enabled: bool = True
    dwg_llm_verify_min_confidence: float = 0.40
    dwg_llm_verify_max_confidence: float = 0.70
    # 单次调用最多校验多少句，超过则截断（成本兜底）
    dwg_llm_verify_max_items: int = 60
    dwg_llm_verify_model: str = "google/gemini-2.5-flash"
    dwg_llm_verify_provider: str = "openrouter"
    # DWG 空间合并 L6：GPT-5 Mini 对局部候选区域做结构化语义分组
    dwg_llm_layout_enabled: bool = True
    # 两个碎片（如序号+正文）也必须进入判断；区域过大则预先切成局部窗口
    dwg_llm_layout_min_bucket: int = 2
    dwg_llm_layout_max_bucket: int = 30
    dwg_llm_layout_concurrency: int = 3
    dwg_llm_layout_model: str = "openai/gpt-5-mini"
    dwg_llm_layout_provider: str = "openrouter"
    # DWG 空间合并诊断：竖线/管道分隔的正则片段列表，命中则 dump 该实体及其邻居
    # 例如 DWG_DEBUG_TEXT_PATTERNS="JZ3|DN150|316L"
    dwg_debug_text_patterns: str = ""
    # DWG 空间合并诊断：命中时把整组写到该 JSONL 文件（一组一行），空则只走日志
    dwg_debug_dump_file: str = ""
    tm_vector_enabled: bool = True
    tm_vector_dimensions: int = 128
    tm_vector_candidate_limit: int = 6
    tm_vector_similarity_floor: float = 0.45
    tm_vector_weight: float = 0.35
    tm_fuzzy_match_batch_size: int = 50
    tm_match_statement_timeout_ms: int = 45000
    tm_search_projection_enabled: bool = False
    tm_search_projection_fallback_enabled: bool = True
    # 项目重复句段同步是否仅由“确认”触发（False 时保留旧行为：有译文的保存也触发）。
    project_sync_confirmed_only: bool = True
    # 句段变更 SSE 推送（依赖 redis_url；不可用时前端自动回退轮询）。
    segment_events_enabled: bool = True
    jwt_secret_key: str = DEFAULT_JWT_SECRET_KEY
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    log_level: str = "INFO"
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:19003",
            "http://localhost:19003",
        ]
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    # ─── 翻译内容校对（Translation Review）配置 ──────────────────────
    translation_review_max_concurrency: int = 3
    translation_review_requests_per_minute: int = 60
    translation_review_batch_size_default: int = 12
    translation_review_default_segment_scope: str = "all"
    # 联网查证：none | openrouter | custom
    translation_review_web_search_provider: str = "none"
    translation_review_web_search_engine: str = "auto"
    translation_review_web_search_max_results: int = 5
    translation_review_web_search_max_uses: int = 2
    translation_review_web_allow_domains: list[str] = Field(default_factory=list)
    translation_review_web_search_custom_url: str | None = None
    translation_review_web_search_custom_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_runtime_settings(settings: Settings) -> None:
    if settings.jwt_secret_key == DEFAULT_JWT_SECRET_KEY:
        raise RuntimeError(
            "JWT_SECRET_KEY 仍为默认值，请在 .env 或环境变量中设置独立密钥后再启动服务。"
        )
