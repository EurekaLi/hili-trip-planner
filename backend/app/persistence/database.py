"""SQLAlchemy 引擎、会话工厂和数据库初始化。"""

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..config import get_settings


class Base(DeclarativeBase):
    """全部业务表共享的 SQLAlchemy 声明式基类。"""


def build_engine(database_url: str) -> Engine:
    """
    根据数据库 URL 创建适用于 SQLite 或 PostgreSQL 的同步引擎。

    Args:
        database_url: SQLAlchemy 数据库连接 URL。

    Returns:
        已配置连接健康检查和 SQLite 外键约束的数据库引擎。
    """
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if database_url.startswith("sqlite:///") and ":memory:" not in database_url:
            database_path = Path(database_url.removeprefix("sqlite:///"))
            database_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
    if database_url.startswith("sqlite"):
        event.listen(engine, "connect", _configure_sqlite_connection)
    return engine


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    """
    为每个 SQLite 连接启用外键和写前日志。

    Args:
        dbapi_connection: SQLAlchemy 创建的底层 SQLite 连接。
        _connection_record: SQLAlchemy 连接池记录，当前无需使用。
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """
    创建提交后仍可读取模型字段的数据库会话工厂。

    Args:
        engine: 目标数据库引擎。

    Returns:
        绑定引擎的 SQLAlchemy 会话工厂。
    """
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


_engine = build_engine(get_settings().database_url)
SessionLocal = build_session_factory(_engine)


def init_database(engine: Engine | None = None) -> None:
    """
    创建当前版本缺失的业务表。

    Args:
        engine: 测试或迁移场景提供的可选引擎，默认使用应用引擎。
    """
    from . import models as _models  # noqa: F401

    Base.metadata.create_all(bind=engine or _engine)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """
    提供自动提交、异常回滚和关闭的短生命周期数据库会话。

    Yields:
        可供一个业务操作使用的数据库会话。
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_engine() -> Engine:
    """
    返回应用级 SQLAlchemy 引擎。

    Returns:
        当前配置对应的数据库引擎。
    """
    return _engine
