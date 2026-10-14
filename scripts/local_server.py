"""本机验收入口：保留 .env，关闭本机未部署的 Redis 和向量服务。"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=19013)
    args = parser.parse_args()
    # 在同一 Python 进程内保留空值，防止 Windows 启动器删除空环境变量后又读取 .env。
    os.environ["REDIS_URL"] = ""
    os.environ["TM_VECTOR_ENABLED"] = "false"
    os.environ["DATABASE_APPLICATION_NAME"] = "ai-translation-local-acceptance"

    from sqlalchemy.engine import make_url
    from app.config import get_settings

    settings = get_settings()
    if make_url(settings.database_url).host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("本机验收入口只允许连接本机数据库，请检查 .env 的 DATABASE_URL。")
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
