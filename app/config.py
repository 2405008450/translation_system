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
    llm_timeout_seconds: float = 60.0
    llm_stall_timeout_seconds: float = 120.0
    llm_max_concurrency: int = 5
    llm_temperature: float = 0.2
    llm_retry_attempts_per_provider: int = 2
    languagetool_base_url: str | None = None
    languagetool_timeout_seconds: float = 10.0
    languagetool_max_text_length: int = 20000
    # 默认关闭保存/确认后的自动拼写语法 QA，避免低优先级检查挤占上传导入队列。
    spelling_grammar_qa_auto_schedule: bool = False
    # LLM settings for reference analysis.
    reference_llm_provider: str = "openrouter"
    reference_llm_api_key: str | None = None
    reference_llm_model: str = "google/gemini-3.5-flash"
    reference_llm_base_url: str = "https://openrouter.ai/api/v1"
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
    tm_vector_enabled: bool = True
    tm_vector_dimensions: int = 128
    tm_vector_candidate_limit: int = 6
    tm_vector_similarity_floor: float = 0.45
    tm_vector_weight: float = 0.35
    tm_fuzzy_match_batch_size: int = 50
    tm_match_statement_timeout_ms: int = 45000
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


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_runtime_settings(settings: Settings) -> None:
    if settings.jwt_secret_key == DEFAULT_JWT_SECRET_KEY:
        raise RuntimeError(
            "JWT_SECRET_KEY 仍为默认值，请在 .env 或环境变量中设置独立密钥后再启动服务。"
        )
