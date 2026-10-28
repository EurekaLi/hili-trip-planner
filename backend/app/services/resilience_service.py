"""任务级外部调用预算、Redis 熔断和受保护服务代理。"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache, wraps
from threading import Lock
from typing import Any, Callable, Dict, Optional

from redis import Redis
from redis.exceptions import RedisError

from ..config import get_settings
from ..observability import BUDGET_REJECTIONS, CIRCUIT_STATE, record_external_call
from .external_call_audit_service import record_external_call_audit


logger = logging.getLogger(__name__)
_active_budget: ContextVar[Optional["TaskBudget"]] = ContextVar(
    "active_task_budget",
    default=None,
)


class TaskBudgetExceeded(RuntimeError):
    """表示任务继续调用第三方服务会超过配置预算。"""


class CircuitOpenError(RuntimeError):
    """表示提供方连续失败后处于暂时熔断状态。"""


@dataclass(frozen=True)
class TaskBudgetSnapshot:
    """可序列化并持久化的任务预算使用情况。"""

    llm_calls: int
    external_calls: int
    known_cost_cny: float
    llm_call_limit: int
    external_call_limit: int
    cost_limit_cny: float

    def to_dict(self) -> Dict[str, Any]:
        """
        转换为数据库 JSON 字段可接受的字典。

        Returns:
            仅包含数字和布尔值的预算快照。
        """
        return {
            "llm_calls": self.llm_calls,
            "external_calls": self.external_calls,
            "known_cost_cny": round(self.known_cost_cny, 6),
            "llm_call_limit": self.llm_call_limit,
            "external_call_limit": self.external_call_limit,
            "cost_limit_cny": self.cost_limit_cny,
        }


class TaskBudget:
    """在并行 Agent 间共享且线程安全的单任务费用护栏。"""

    def __init__(
        self,
        llm_call_limit: int,
        external_call_limit: int,
        cost_limit_cny: float,
    ) -> None:
        """
        初始化任务预算及零使用量计数。

        Args:
            llm_call_limit: 单任务允许的 LLM 调用数。
            external_call_limit: 单任务允许的地图等外部调用数。
            cost_limit_cny: 已知费用上限；0 表示只记录不限制金额。
        """
        self.llm_call_limit = llm_call_limit
        self.external_call_limit = external_call_limit
        self.cost_limit_cny = cost_limit_cny
        self._llm_calls = 0
        self._external_calls = 0
        self._known_cost_cny = 0.0
        self._lock = Lock()

    @classmethod
    def from_settings(cls) -> "TaskBudget":
        """
        使用当前应用配置创建任务预算。

        Returns:
            尚未消费的新预算对象。
        """
        settings = get_settings()
        return cls(
            llm_call_limit=settings.generation_llm_call_budget,
            external_call_limit=settings.generation_external_call_budget,
            cost_limit_cny=settings.generation_cost_budget_cny,
        )

    def consume_llm(self, operation: str) -> None:
        """
        在发起 LLM 请求前保留一个调用单位。

        Args:
            operation: 用于错误说明的 Agent 或操作名称。

        Raises:
            TaskBudgetExceeded: 新调用会超过 LLM 次数上限时抛出。
        """
        with self._lock:
            self._raise_if_cost_exceeded()
            if self._llm_calls + 1 > self.llm_call_limit:
                BUDGET_REJECTIONS.labels("llm_calls").inc()
                raise TaskBudgetExceeded(
                    f"任务 LLM 调用预算已用尽: operation={operation}, "
                    f"limit={self.llm_call_limit}"
                )
            self._llm_calls += 1

    def consume_external(self, operation: str, units: int = 1) -> None:
        """
        在地图或图片请求前保留外部调用单位。

        Args:
            operation: 外部服务操作名称。
            units: 本次请求占用的计费单位。

        Raises:
            TaskBudgetExceeded: 新调用会超过外部调用上限时抛出。
        """
        with self._lock:
            if self._external_calls + units > self.external_call_limit:
                BUDGET_REJECTIONS.labels("external_calls").inc()
                raise TaskBudgetExceeded(
                    f"任务外部调用预算已用尽: operation={operation}, "
                    f"limit={self.external_call_limit}"
                )
            self._external_calls += units

    def record_cost(self, cost_cny: Optional[float]) -> None:
        """
        累加提供方返回的已知费用并执行金额上限。

        Args:
            cost_cny: 本次调用人民币费用；未知时传 None。

        """
        if cost_cny is None:
            return
        with self._lock:
            self._known_cost_cny += max(0.0, cost_cny)

    def ensure_within_cost(self) -> None:
        """
        校验累计已知费用没有超过非零金额上限。

        Raises:
            TaskBudgetExceeded: 已知费用超过配置上限时抛出。
        """
        with self._lock:
            self._raise_if_cost_exceeded()

    def snapshot(self) -> TaskBudgetSnapshot:
        """
        获取一致的当前预算使用快照。

        Returns:
            不会再随原对象变化的预算快照。
        """
        with self._lock:
            return TaskBudgetSnapshot(
                llm_calls=self._llm_calls,
                external_calls=self._external_calls,
                known_cost_cny=self._known_cost_cny,
                llm_call_limit=self.llm_call_limit,
                external_call_limit=self.external_call_limit,
                cost_limit_cny=self.cost_limit_cny,
            )

    def _raise_if_cost_exceeded(self) -> None:
        """
        在持有预算锁时执行金额上限校验。

        Raises:
            TaskBudgetExceeded: 当前已知费用超过非零上限时抛出。
        """
        if (
            self.cost_limit_cny > 0
            and self._known_cost_cny > self.cost_limit_cny
        ):
            BUDGET_REJECTIONS.labels("known_cost_cny").inc()
            raise TaskBudgetExceeded(
                "任务已知费用超过上限: "
                f"used={self._known_cost_cny:.4f}, "
                f"limit={self.cost_limit_cny:.4f}"
            )


def activate_task_budget(budget: TaskBudget) -> Token:
    """
    将任务预算绑定到当前同步执行上下文。

    Args:
        budget: 当前生成任务共享的预算对象。

    Returns:
        用于恢复先前预算上下文的令牌。
    """
    return _active_budget.set(budget)


def deactivate_task_budget(token: Token) -> None:
    """
    恢复进入任务前的预算上下文。

    Args:
        token: `activate_task_budget` 返回的上下文令牌。
    """
    _active_budget.reset(token)


def get_active_task_budget() -> Optional[TaskBudget]:
    """
    返回当前同步上下文绑定的预算。

    Returns:
        当前预算；非生成请求返回 None。
    """
    return _active_budget.get()


@dataclass(frozen=True)
class CircuitSnapshot:
    """一个提供方当前的失败计数和熔断期限。"""

    provider: str
    failures: int
    opened_until: float

    @property
    def is_open(self) -> bool:
        """
        判断熔断截止时间是否仍在未来。

        Returns:
            当前调用应被拒绝时返回 True。
        """
        return self.opened_until > time.time()


class RedisCircuitBreaker:
    """以 Redis 为共享状态、以内存为故障兜底的提供方熔断器。"""

    _FAILURE_SCRIPT = """
    local failures = redis.call('HINCRBY', KEYS[1], 'failures', 1)
    if failures >= tonumber(ARGV[1]) then
        redis.call('HSET', KEYS[1], 'opened_until', ARGV[2])
    end
    redis.call('EXPIRE', KEYS[1], ARGV[3])
    return failures
    """

    def __init__(
        self,
        redis_client: Redis,
        key_prefix: str,
        failure_threshold: int,
        recovery_seconds: int,
    ) -> None:
        """
        初始化分布式熔断器。

        Args:
            redis_client: Redis 客户端。
            key_prefix: 熔断状态键前缀。
            failure_threshold: 连续失败多少次后打开熔断。
            recovery_seconds: 打开后等待的恢复秒数。
        """
        self.redis = redis_client
        self.key_prefix = key_prefix.rstrip(":")
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._fallback: Dict[str, CircuitSnapshot] = {}
        self._lock = Lock()

    def before_call(self, provider: str) -> None:
        """
        在第三方调用前检查熔断状态。

        Args:
            provider: 有界的提供方名称。

        Raises:
            CircuitOpenError: 熔断窗口尚未结束时抛出。
        """
        snapshot = self.snapshot(provider)
        CIRCUIT_STATE.labels(provider).set(1 if snapshot.is_open else 0)
        if snapshot.is_open:
            retry_after = max(1, int(snapshot.opened_until - time.time()))
            raise CircuitOpenError(
                f"{provider} 服务暂时熔断，请在 {retry_after} 秒后重试"
            )

    def record_success(self, provider: str) -> None:
        """
        成功调用后清空连续失败状态。

        Args:
            provider: 有界的提供方名称。
        """
        try:
            self.redis.delete(self._key(provider))
        except RedisError as exc:
            logger.warning("circuit_backend_unavailable error=%s", exc)
        with self._lock:
            self._fallback.pop(provider, None)
        CIRCUIT_STATE.labels(provider).set(0)

    def record_failure(self, provider: str) -> CircuitSnapshot:
        """
        记录连续失败，并在达到阈值时打开熔断。

        Args:
            provider: 有界的提供方名称。

        Returns:
            更新后的熔断快照。
        """
        opened_until = time.time() + self.recovery_seconds
        try:
            failures = int(
                self.redis.eval(
                    self._FAILURE_SCRIPT,
                    1,
                    self._key(provider),
                    self.failure_threshold,
                    opened_until,
                    self.recovery_seconds * 2,
                )
            )
            snapshot = CircuitSnapshot(
                provider=provider,
                failures=failures,
                opened_until=(
                    opened_until if failures >= self.failure_threshold else 0.0
                ),
            )
        except RedisError as exc:
            logger.warning("circuit_backend_unavailable error=%s", exc)
            snapshot = self._record_fallback_failure(provider, opened_until)
        CIRCUIT_STATE.labels(provider).set(1 if snapshot.is_open else 0)
        return snapshot

    def snapshot(self, provider: str) -> CircuitSnapshot:
        """
        读取提供方熔断状态，Redis 不可用时使用进程内状态。

        Args:
            provider: 有界的提供方名称。

        Returns:
            当前失败计数和打开截止时间。
        """
        try:
            values = self.redis.hgetall(self._key(provider))
            return CircuitSnapshot(
                provider=provider,
                failures=int(values.get("failures", 0)),
                opened_until=float(values.get("opened_until", 0.0)),
            )
        except RedisError as exc:
            logger.warning("circuit_backend_unavailable error=%s", exc)
            with self._lock:
                return self._fallback.get(
                    provider,
                    CircuitSnapshot(provider, 0, 0.0),
                )

    def _record_fallback_failure(
        self,
        provider: str,
        opened_until: float,
    ) -> CircuitSnapshot:
        """
        在 Redis 不可用时更新进程内连续失败状态。

        Args:
            provider: 提供方名称。
            opened_until: 达到阈值时使用的熔断截止时间。

        Returns:
            更新后的进程内快照。
        """
        with self._lock:
            previous = self._fallback.get(
                provider,
                CircuitSnapshot(provider, 0, 0.0),
            )
            failures = previous.failures + 1
            snapshot = CircuitSnapshot(
                provider=provider,
                failures=failures,
                opened_until=(
                    opened_until if failures >= self.failure_threshold else 0.0
                ),
            )
            self._fallback[provider] = snapshot
            return snapshot

    def _key(self, provider: str) -> str:
        """
        构造单个提供方的 Redis 键。

        Args:
            provider: 有界提供方名称。

        Returns:
            位于应用命名空间中的熔断键。
        """
        return f"{self.key_prefix}:{provider}"


class GuardedMapService:
    """为现有高德服务透明增加任务预算、熔断和 Prometheus 指标。"""

    _GUARDED_OPERATIONS = frozenset(
        {
            "search_poi",
            "get_weather",
            "plan_route",
            "plan_route_by_coordinates",
            "geocode",
            "get_poi_detail",
        }
    )

    def __init__(self, service: Any, circuit_breaker: RedisCircuitBreaker) -> None:
        """
        初始化透明代理。

        Args:
            service: 原始 AMapService 实例。
            circuit_breaker: 跨进程共享的熔断器。
        """
        self._service = service
        self._circuit_breaker = circuit_breaker

    def __getattr__(self, name: str) -> Any:
        """
        转发普通属性，并包装需要付费或可能超时的公开调用。

        Args:
            name: 被访问的原始服务属性名。

        Returns:
            原始属性或带护栏的可调用包装器。
        """
        value = getattr(self._service, name)
        if name not in self._GUARDED_OPERATIONS or not callable(value):
            return value

        @wraps(value)
        def guarded(*args: Any, **kwargs: Any) -> Any:
            """执行一次受预算和熔断保护的地图调用。"""
            budget = get_active_task_budget()
            if budget is not None:
                budget.consume_external(name)
            self._circuit_breaker.before_call("amap")
            started_at = time.perf_counter()
            requested_at = datetime.now(timezone.utc)
            try:
                result = value(*args, **kwargs)
            except Exception as exc:
                elapsed = time.perf_counter() - started_at
                self._circuit_breaker.record_failure("amap")
                record_external_call("amap", name, False, elapsed)
                record_external_call_audit(
                    provider="amap",
                    operation=name,
                    succeeded=False,
                    elapsed_seconds=elapsed,
                    error_type=type(exc).__name__,
                    requested_at=requested_at,
                )
                raise
            elapsed = time.perf_counter() - started_at
            self._circuit_breaker.record_success("amap")
            record_external_call("amap", name, True, elapsed)
            record_external_call_audit(
                provider="amap",
                operation=name,
                succeeded=True,
                elapsed_seconds=elapsed,
                requested_at=requested_at,
            )
            return result

        return guarded


@lru_cache(maxsize=1)
def get_circuit_breaker() -> RedisCircuitBreaker:
    """
    创建 API 与 Worker 进程各自复用、状态由 Redis 共享的熔断器。

    Returns:
        已连接应用 Redis 命名空间的熔断器。
    """
    settings = get_settings()
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    return RedisCircuitBreaker(
        redis_client=client,
        key_prefix=f"{settings.trip_job_key_prefix}:circuit",
        failure_threshold=settings.circuit_failure_threshold,
        recovery_seconds=settings.circuit_recovery_seconds,
    )


@lru_cache(maxsize=1)
def get_guarded_amap_service() -> GuardedMapService:
    """
    包装项目现有 AMapService，而不修改其解析和 MCP 实现。

    Returns:
        可替代原服务使用的透明可靠性代理。
    """
    from .amap_service import get_amap_service

    return GuardedMapService(
        service=get_amap_service(),
        circuit_breaker=get_circuit_breaker(),
    )
