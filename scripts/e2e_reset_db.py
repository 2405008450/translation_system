from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/tm_demo_e2e"
DEFAULT_STORAGE_DIR = ROOT / "data" / "e2e_file_records"

SQL_FILES = [
    "scripts/init_db.sql",
    "scripts/add_user_nickname.sql",
    "scripts/create_segment_revisions.sql",
    "scripts/add_project_fields.sql",
    "scripts/add_file_record_resource_binding.sql",
    "scripts/add_creator_to_entries.sql",
    "scripts/add_translation_guidelines.sql",
    "scripts/add_quality_qa_settings.sql",
    "scripts/create_issue_markers.sql",
]

E2E_TABLES = [
    "segment_qa_issues",
    "segment_revisions",
    "segment_comments",
    "issue_markers",
    "segments",
    "file_records",
    "projects",
    "term_entries",
    "term_bases",
    "memory_entries",
    "memory_bases",
    "users",
]

USERS_TABLE_PRELUDE = """
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    username VARCHAR(50) NOT NULL UNIQUE,
    nickname VARCHAR(50),
    hashed_password VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""


def _normalise_database_url(value: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    if value.startswith("postgres+psycopg://"):
        return "postgres://" + value.removeprefix("postgres+psycopg://")
    return value


def _parse_database_url(value: str) -> dict[str, object]:
    parsed = urlsplit(_normalise_database_url(value))
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise RuntimeError(f"仅支持 PostgreSQL E2E 数据库 URL，当前为: {parsed.scheme or '<empty>'}")
    dbname = parsed.path.lstrip("/")
    if not dbname:
        raise RuntimeError("E2E 数据库 URL 必须包含数据库名。")
    return {
        "dbname": unquote(dbname),
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
    }


def _connect_params(database_url: str, *, dbname: str | None = None) -> dict[str, object]:
    params = _parse_database_url(database_url)
    if dbname is not None:
        params["dbname"] = dbname
    return params


def _read_sql_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="gb18030")


def _is_extension_available(conn: psycopg.Connection, extension_name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM pg_available_extensions WHERE name = %s",
            (extension_name,),
        ).fetchone()
    )


def _prepare_schema_sql(raw_sql: str, *, relative_path: str, vector_available: bool) -> str:
    if vector_available or relative_path != "scripts/init_db.sql":
        return raw_sql

    sql_text = raw_sql.replace(
        "CREATE EXTENSION IF NOT EXISTS vector;",
        "-- pgvector is not installed in this E2E database; vector search is disabled.",
    )
    sql_text = re.sub(r"^\s*source_embedding\s+vector\(128\),\r?\n", "", sql_text, flags=re.MULTILINE)
    sql_text = re.sub(
        r"CREATE INDEX IF NOT EXISTS ix_memory_entries_source_embedding_ivfflat\s+"
        r"ON memory_entries\s+"
        r"USING ivfflat \(source_embedding vector_cosine_ops\)\s+"
        r"WITH \(lists = 100\);",
        "-- pgvector ivfflat index skipped in E2E.",
        sql_text,
        flags=re.IGNORECASE,
    )
    return sql_text


def _ensure_e2e_database(database_url: str, dbname: str) -> None:
    admin_db = os.environ.get("E2E_ADMIN_DATABASE", "postgres")
    params = _connect_params(database_url, dbname=admin_db)
    try:
        with psycopg.connect(**params, autocommit=True) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (dbname,),
            ).fetchone()
            if exists:
                return
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
            print(f"已创建 E2E 数据库: {dbname}")
    except psycopg.Error as exc:
        raise RuntimeError(
            "无法连接 PostgreSQL 或创建 E2E 数据库。请确认服务已启动，并且账号有 CREATEDB 权限。"
        ) from exc


def _run_schema_scripts(database_url: str) -> None:
    params = _connect_params(database_url)
    with psycopg.connect(**params) as conn:
        vector_available = _is_extension_available(conn, "vector")
        if not vector_available:
            print("未检测到 pgvector，E2E schema 将跳过 vector 扩展和向量索引。")
        try:
            conn.execute(USERS_TABLE_PRELUDE)
            conn.commit()
            for relative_path in SQL_FILES:
                path = ROOT / relative_path
                if not path.exists():
                    continue
                conn.execute(
                    _prepare_schema_sql(
                        _read_sql_file(path),
                        relative_path=relative_path,
                        vector_available=vector_available,
                    )
                )
                conn.commit()
                print(f"已应用 schema 脚本: {relative_path}")
        except psycopg.Error as exc:
            conn.rollback()
            raise RuntimeError(
                "E2E schema 初始化失败。请确认 PostgreSQL 已安装 pg_trgm 和 pgvector 扩展，"
                "或者先按 README 完成数据库依赖安装。"
            ) from exc


def _truncate_e2e_tables(database_url: str) -> None:
    params = _connect_params(database_url)
    with psycopg.connect(**params) as conn:
        existing_tables = []
        for table_name in E2E_TABLES:
            exists = conn.execute("SELECT to_regclass(%s)", (f"public.{table_name}",)).fetchone()[0]
            if exists:
                existing_tables.append(table_name)

        if existing_tables:
            identifiers = sql.SQL(", ").join(sql.Identifier(name) for name in existing_tables)
            conn.execute(sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(identifiers))
            conn.commit()
            print(f"已清空 E2E 表: {', '.join(existing_tables)}")


def _clean_storage_dir() -> None:
    storage_dir = Path(os.environ.get("E2E_FILE_STORAGE_DIR", str(DEFAULT_STORAGE_DIR))).resolve()
    root_data_dir = (ROOT / "data").resolve()
    if root_data_dir not in storage_dir.parents and storage_dir != root_data_dir:
        raise RuntimeError(f"拒绝清理 data 目录外的路径: {storage_dir}")

    storage_dir.mkdir(parents=True, exist_ok=True)
    for child in storage_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    print(f"已清理 E2E 文件目录: {storage_dir}")


def main() -> int:
    database_url = os.environ.get("E2E_DATABASE_URL") or os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL
    dbname = str(_parse_database_url(database_url)["dbname"])
    if not dbname.endswith("_e2e") and os.environ.get("E2E_ALLOW_NON_E2E_DATABASE") != "1":
        print(
            f"拒绝重置非 E2E 数据库 {dbname!r}。如确实需要，请设置 E2E_ALLOW_NON_E2E_DATABASE=1。",
            file=sys.stderr,
        )
        return 2

    _ensure_e2e_database(database_url, dbname)
    _run_schema_scripts(database_url)
    _truncate_e2e_tables(database_url)
    _clean_storage_dir()
    print("E2E 数据库已准备就绪。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
