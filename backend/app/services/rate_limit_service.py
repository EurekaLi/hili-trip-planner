"""基于 Redis 的分布式请求限流服务。"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from typing import Dict, Iterable, Optional, Pattern, Tuple

from redis import Redis
from redis.exceptions import RedisError
from starlette.requests import Request

from ..config import get_settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitPolicy:
    """一个方法和路径组合对应的固定窗口限流规则。"""

    name: str
    methods: frozenset[str]
    path_pattern: Pattern[str]
    limit: int
    window_seconds: int

    def matches(self, method: str, path: str) -> bool:
        """
        判断请求是否适用当前规则。

        Args:
            method: HTTP 方法。
            path: 不包含查询参数的请求路径。

        Returns:
            方法和路径均匹配时返回 True。
        """
        return method.upper() in self.methods and bool(
            self.path_pattern.fullmatch(path)
        )


@dataclass(frozen=True)
class RateLimitDecision:
    """一次限流检查的结果和响应头所需信息。"""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    policy: str


class RedisRateLimiter:
    """使用 Redis Lua 脚本实现跨 API 实例一致的固定窗口限流。"""

    _SCRIPT = """
    local current = redis.call('INCR', KEYS[1])
    if current == 1 then
        redis.call('EXPIRE', KEYS[1], ARGV[1])
    end
    local ttl = redis.call('TTL', KEYS[1])
    return {current, ttl}
    """

    def __init__(
        self,
        redis_client: Redis,
        key_prefix: str,
        fail_open: bool = True,
    ) -> None:
        """
        初始化 Redis 限流器。

        Args:
            redis_client: 可复用 Redis 客户端。
            key_prefix: 与任务键空间隔离的限流前缀。
            fail_open: Redis 故障时是否允许请求继续。
        """
        self.redis = redis_client
        self.key_prefix = key_prefix.rstrip(":")
        self.fail_open = fail_open

    def check(self, subject: str, policy: RateLimitPolicy) -> RateLimitDecision:
        """
        原子增加当前窗口计数并返回是否允许请求。

        Args:
            subject: 已哈希的客户端主体标识。
            policy: 当前请求匹配的限流规则。

        Returns:
            包含剩余配额和重试秒数的决策。
        """
        window = int(time.time()) // policy.window_seconds
        key = f"{self.key_prefix}:{policy.name}:{window}:{subject}"
        try:
            current, ttl = self.redis.eval(
                self._SCRIPT,
                1,
                key,
                policy.window_seconds,
            )
            current_value = int(current)
            retry_after = max(1, int(ttl))
            return RateLimitDecision(
                allowed=current_value <= policy.limit,
                limit=policy.limit,
                remaining=max(0, policy.limit - current_value),
                retry_after=retry_after,
                policy=policy.name,
            )
        except RedisError as exc:
            logger.warning("rate_limit_backend_unavailable error=%s", exc)
            return RateLimitDecision(
                allowed=self.fail_open,
                limit=policy.limit,
                remaining=policy.limit if self.fail_open else 0,
                retry_after=policy.window_seconds,
                policy=policy.name,
            )


class InMemoryRateLimiter:
    """用于单元测试和无 Redis 开发场景的线程安全固定窗口实现。"""

    def __init__(self) -> None:
        """初始化空计数器和互斥锁。"""
        self._counts: Dict[Tuple[str, str, int], int] = {}
        self._lock = Lock()

    def check(self, subject: str, policy: RateLimitPolicy) -> RateLimitDecision:
        """
        在当前进程中执行一次限流检查。

        Args:
            subject: 客户端主体标识。
            policy: 当前规则。

        Returns:
            限流决策。
        """
        now = int(time.time())
        window = now // policy.window_seconds
        key = (policy.name, subject, window)
        with self._lock:
            current = self._counts.get(key, 0) + 1
            self._counts[key] = current
        retry_after = policy.window_seconds - (now % policy.window_seconds)
        return RateLimitDecision(
            allowed=current <= policy.limit,
            limit=policy.limit,
            remaining=max(0, policy.limit - current),
            retry_after=max(1, retry_after),
            policy=policy.name,
        )


def build_rate_limit_policies() -> Tuple[RateLimitPolicy, ...]:
    """
    从配置构造有界路径集合，避免任意 URL 成为指标或 Redis 标签。

    Returns:
        按优先级排列的限流规则。
    """
    settings = get_settings()
    window = settings.rate_limit_window_seconds
    return (
        RateLimitPolicy(
            name="trip_generation",
            methods=frozenset({"POST"}),
            path_pattern=re.compile(r"/api/trip/(jobs|plan)"),
            limit=settings.rate_limit_trip_generation,
            window_seconds=window,
        ),
        RateLimitPolicy(
            name="authentication",
            methods=frozenset({"POST"}),
            path_pattern=re.compile(r"/api/auth/(login|register)"),
            limit=settings.rate_limit_authentication,
            window_seconds=window,
        ),
        RateLimitPolicy(
            name="feedback",
            methods=frozenset({"POST"}),
            path_pattern=re.compile(r"/api/shares/[^/]+/feedback"),
            limit=settings.rate_limit_feedback,
            window_seconds=window,
        ),
        RateLimitPolicy(
            name="poi_lookup",
            methods=frozenset({"GET"}),
            path_pattern=re.compile(r"/api/(poi|map)/.*"),
            limit=settings.rate_limit_poi_lookup,
            window_seconds=window,
        ),
    )


def find_policy(
    policies: Iterable[RateLimitPolicy],
    method: str,
    path: str,
) -> Optional[RateLimitPolicy]:
    """
    返回首个匹配请求的方法和路径规则。

    Args:
        policies: 已排序规则集合。
        method: HTTP 方法。
        path: 请求路径。

    Returns:
        匹配规则；无限流要求时返回 None。
    """
    return next(
        (policy for policy in policies if policy.matches(method, path)),
        None,
    )


def request_subject(request: Request) -> str:
    """
    从会话、游客 ID 或 IP 生成不可逆且稳定的限流主体。

    Args:
        request: 当前 Starlette 请求。

    Returns:
        SHA-256 截断摘要，不泄露原始凭据和 IP。
    """
    session = request.cookies.get("trip_session")
    guest_id = request.headers.get("X-Guest-ID")
    client_ip = request.client.host if request.client else "unknown"
    raw = session or guest_id or client_ip
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


@lru_cache(maxsize=1)
def get_rate_limiter() -> RedisRateLimiter:
    """
    创建当前 API 进程复用的 Redis 限流器。

    Returns:
        使用任务 Redis 连接和独立键前缀的限流器。
    """
    settings = get_settings()
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    return RedisRateLimiter(
        redis_client=client,
        key_prefix=f"{settings.trip_job_key_prefix}:rate-limit",
        fail_open=settings.rate_limit_fail_open,
    )
