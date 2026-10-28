"""应用依赖健康检查，供公开探针和管理员接口复用。"""

from dataclasses import asdict, dataclass

from redis import Redis
from sqlalchemy import text

from ..config import get_settings
from ..persistence.database import SessionLocal


@dataclass(frozen=True)
class DependencyHealth:
    """数据库与 Redis 的就绪状态快照。"""

    ready: bool
    database: str
    redis: str

    def to_dict(self) -> dict[str, bool | str]:
        """
        转换为稳定的 HTTP 响应结构。

        Returns:
            可直接序列化为 JSON 的依赖状态。
        """
        return asdict(self)


def check_dependency_health() -> DependencyHealth:
    """
    以短超时探测 SQL 和 Redis，判断实例是否可以接收业务流量。

    Returns:
        包含数据库、Redis 和总体就绪状态的快照。
    """
    database_ok = False
    redis_ok = False
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
            database_ok = True
    except Exception:
        database_ok = False

    redis_client = None
    try:
        redis_client = Redis.from_url(
            get_settings().redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        redis_ok = bool(redis_client.ping())
    except Exception:
        redis_ok = False
    finally:
        if redis_client is not None:
            redis_client.close()

    return DependencyHealth(
        ready=database_ok and redis_ok,
        database="up" if database_ok else "down",
        redis="up" if redis_ok else "down",
    )
