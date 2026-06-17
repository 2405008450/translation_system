"""Gunicorn 运行配置。

使用 preload_app 在主进程中只导入一次应用（包含 ensure_runtime_schema 的 DDL 初始化），
避免多个 worker 并发执行建表/改表语句产生竞态；同时显著降低内存占用。

由于在 fork 之前已经创建了 SQLAlchemy engine，其连接池中的 socket 不能被子进程共享，
因此在 post_fork 钩子里 dispose 掉父进程继承的连接池，让每个 worker 进程惰性重建自己的连接。
"""

import os

# worker 数量优先读取 WEB_CONCURRENCY，默认 4。
workers = int(os.getenv("WEB_CONCURRENCY", "4"))
worker_class = "uvicorn.workers.UvicornWorker"
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:19013")

# 只在主进程导入一次应用，避免多 worker 并发执行 schema 初始化 DDL。
preload_app = True

# SSE 长连接：放宽超时，避免长流式响应被误判为 worker 卡死。
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))


def post_fork(server, worker):  # noqa: ARG001
    """fork 出 worker 后丢弃从主进程继承的连接池，强制每个 worker 重建自己的连接。"""
    from app.database import engine

    engine.dispose()
