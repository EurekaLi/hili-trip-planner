"""用户闭环相关应用服务的进程级工厂。"""

from functools import lru_cache

from ..config import get_settings
from ..persistence.database import SessionLocal
from .auth_service import AuthService
from .operations_service import OperationsService
from .resilience_service import get_guarded_amap_service
from .trip_library_service import TripLibraryService
from .trip_mutation_service import TripMutationService


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    """
    构建当前进程复用的认证应用服务。

    Returns:
        使用统一数据库会话工厂和配置有效期的认证服务。
    """
    return AuthService(
        session_factory=SessionLocal,
        session_ttl_days=get_settings().auth_session_ttl_days,
    )


@lru_cache(maxsize=1)
def get_trip_library_service() -> TripLibraryService:
    """
    构建带真实地图校验和确定性重算能力的行程库服务。

    Returns:
        当前 API 或 worker 进程可复用的行程库服务。
    """
    amap_service = get_guarded_amap_service()
    mutation_service = TripMutationService(
        route_provider=amap_service,
        poi_provider=amap_service,
    )
    return TripLibraryService(
        session_factory=SessionLocal,
        mutation_service=mutation_service,
    )


@lru_cache(maxsize=1)
def get_operations_service() -> OperationsService:
    """
    构建只读运营聚合与任务追踪服务。

    Returns:
        使用统一 SQL 会话工厂的进程级运营服务。
    """
    return OperationsService(session_factory=SessionLocal)
