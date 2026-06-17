from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import get_settings


settings = get_settings()

connect_args: dict[str, object] = {"connect_timeout": 5}
if settings.database_application_name:
    connect_args["application_name"] = settings.database_application_name

if settings.database_pgbouncer_transaction_mode:
    # 事务级连接池(PgBouncer transaction mode)下必须关闭服务端预备语句，否则会因
    # 同一客户端被分配到不同后端连接而报 "prepared statement does not exist"。
    connect_args["prepare_threshold"] = None
    # 事务池化下会话级 SET 不可靠，时区改由 Postgres 服务端默认时区保证(部署时已设置)。
elif settings.tz:
    connect_args["options"] = f"-c timezone={settings.tz}"

engine_kwargs = {
    "future": True,
    "pool_pre_ping": True,
    "connect_args": connect_args,
}

if not settings.database_url.startswith("sqlite"):
    engine_kwargs.update(
        {
            "pool_size": settings.database_pool_size,
            "max_overflow": settings.database_max_overflow,
            "pool_timeout": settings.database_pool_timeout,
            "pool_recycle": settings.database_pool_recycle,
            "pool_use_lifo": True,
        }
    )

engine = create_engine(
    settings.database_url,
    **engine_kwargs,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
