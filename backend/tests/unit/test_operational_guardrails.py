"""预算、限流、权限、验证码和熔断策略的快速单元测试。"""

import re
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.dependencies import require_admin_user
from app.api.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    RequestSizeLimitMiddleware,
)
from app.services.captcha_service import (
    CaptchaService,
    CaptchaVerificationError,
)
from app.services.rate_limit_service import InMemoryRateLimiter, RateLimitPolicy
from app.services.resilience_service import (
    CircuitOpenError,
    RedisCircuitBreaker,
    TaskBudget,
    TaskBudgetExceeded,
)


class FakeCircuitRedis:
    """支持熔断器测试所需哈希和 Lua 语义的内存替身。"""

    def __init__(self) -> None:
        """初始化空哈希状态。"""
        self.values: dict[str, dict[str, str]] = {}

    def eval(self, _script, _keys, key, threshold, opened_until, _ttl):
        """模拟连续失败计数和达到阈值后的打开时间。"""
        state = self.values.setdefault(key, {})
        failures = int(state.get("failures", "0")) + 1
        state["failures"] = str(failures)
        if failures >= int(threshold):
            state["opened_until"] = str(opened_until)
        return failures

    def hgetall(self, key):
        """返回指定熔断键的字段副本。"""
        return dict(self.values.get(key, {}))

    def delete(self, key):
        """删除成功调用对应的连续失败状态。"""
        self.values.pop(key, None)
        return 1


class FakeBreaker:
    """记录验证码测试中熔断器成功与失败事件。"""

    def __init__(self) -> None:
        """初始化空事件列表。"""
        self.events: list[str] = []

    def before_call(self, provider: str) -> None:
        """记录调用前检查。"""
        self.events.append(f"before:{provider}")

    def record_success(self, provider: str) -> None:
        """记录提供方成功。"""
        self.events.append(f"success:{provider}")

    def record_failure(self, provider: str) -> None:
        """记录提供方失败。"""
        self.events.append(f"failure:{provider}")


class FakeCaptchaResponse:
    """提供 httpx 响应所需最小接口的验证码结果。"""

    def __init__(self, payload: dict) -> None:
        """保存待返回 JSON。"""
        self.payload = payload

    def raise_for_status(self) -> None:
        """模拟成功 HTTP 状态。"""
        return None

    def json(self) -> dict:
        """返回预设 Siteverify 结果。"""
        return self.payload


def build_captcha_service() -> CaptchaService:
    """
    创建启用状态且仅接受生产域名的验证码服务。

    Returns:
        可由测试替换 HTTP 调用的验证码服务。
    """
    return CaptchaService(
        enabled=True,
        secret_key="secret",
        verify_url="https://captcha.invalid/siteverify",
        expected_hostnames={"travel.example.com"},
    )


def test_task_budget_limits_calls_and_known_cost() -> None:
    """任务预算应在新调用前拒绝次数或已知费用超限。"""
    budget = TaskBudget(
        llm_call_limit=2,
        external_call_limit=1,
        cost_limit_cny=0.5,
    )
    budget.consume_llm("attraction")
    budget.consume_llm("planner")
    with pytest.raises(TaskBudgetExceeded):
        budget.consume_llm("extra")
    budget.consume_external("search_poi")
    with pytest.raises(TaskBudgetExceeded):
        budget.consume_external("get_weather")
    budget.record_cost(0.6)
    with pytest.raises(TaskBudgetExceeded):
        budget.ensure_within_cost()
    assert budget.snapshot().known_cost_cny == pytest.approx(0.6)


def test_task_budget_is_atomic_across_parallel_control_agents() -> None:
    """并行控制 Agent 共享预算时不应丢失任何调用计数。"""
    budget = TaskBudget(
        llm_call_limit=100,
        external_call_limit=100,
        cost_limit_cny=10,
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(budget.consume_llm, [f"agent-{index}" for index in range(100)]))

    assert budget.snapshot().llm_calls == 100
    with pytest.raises(TaskBudgetExceeded):
        budget.consume_llm("one-too-many")


def test_rate_limit_quotas_are_isolated_by_subject() -> None:
    """一个游客耗尽配额后不应阻止另一个游客的首个请求。"""
    limiter = InMemoryRateLimiter()
    policy = RateLimitPolicy(
        name="trip_generation",
        methods=frozenset({"POST"}),
        path_pattern=re.compile(r"/api/trip/jobs"),
        limit=1,
        window_seconds=60,
    )

    assert limiter.check("guest-a", policy).allowed is True
    assert limiter.check("guest-a", policy).allowed is False
    assert limiter.check("guest-b", policy).allowed is True


def test_circuit_opens_after_threshold_and_success_resets() -> None:
    """共享熔断器应在连续失败后快速拒绝，并可由成功调用复位。"""
    redis = FakeCircuitRedis()
    breaker = RedisCircuitBreaker(
        redis_client=redis,
        key_prefix="test:circuit",
        failure_threshold=2,
        recovery_seconds=60,
    )
    breaker.record_failure("amap")
    breaker.before_call("amap")
    breaker.record_failure("amap")
    with pytest.raises(CircuitOpenError):
        breaker.before_call("amap")
    breaker.record_success("amap")
    breaker.before_call("amap")


def test_captcha_policy_mismatch_does_not_trip_provider_circuit(monkeypatch) -> None:
    """有效 Siteverify 响应的 action 不匹配属于业务拒绝而非提供方故障。"""
    from app.services import captcha_service as captcha_module

    breaker = FakeBreaker()
    monkeypatch.setattr(captcha_module, "get_circuit_breaker", lambda: breaker)
    monkeypatch.setattr(
        captcha_module.httpx,
        "post",
        lambda *_args, **_kwargs: FakeCaptchaResponse(
            {
                "success": True,
                "action": "register",
                "hostname": "travel.example.com",
            }
        ),
    )
    service = build_captcha_service()

    service.verify("valid-token", "127.0.0.1", "register")
    with pytest.raises(CaptchaVerificationError):
        service.verify("valid-token", "127.0.0.1", "trip_generation")

    assert breaker.events[-1] == "success:turnstile"
    assert "failure:turnstile" not in breaker.events


def test_captcha_transport_failure_trips_provider_circuit(monkeypatch) -> None:
    """Siteverify 网络故障应计入提供方失败并返回稳定业务异常。"""
    from app.services import captcha_service as captcha_module

    breaker = FakeBreaker()
    monkeypatch.setattr(captcha_module, "get_circuit_breaker", lambda: breaker)

    def raise_connect_error(*_args, **_kwargs):
        """模拟验证码提供方连接失败。"""
        raise httpx.ConnectError("provider unavailable")

    monkeypatch.setattr(captcha_module.httpx, "post", raise_connect_error)

    with pytest.raises(CaptchaVerificationError, match="服务暂时不可用"):
        build_captcha_service().verify(
            "valid-token",
            "127.0.0.1",
            "register",
        )

    assert breaker.events[-1] == "failure:turnstile"


def test_request_middlewares_add_id_limit_rate_and_body() -> None:
    """API 中间件应返回请求 ID、429 配额和 413 体积保护。"""
    app = FastAPI()
    policy = RateLimitPolicy(
        name="test_cost",
        methods=frozenset({"POST"}),
        path_pattern=re.compile(r"/cost"),
        limit=2,
        window_seconds=60,
    )
    app.add_middleware(RequestSizeLimitMiddleware, max_body_bytes=16)
    app.add_middleware(
        RateLimitMiddleware,
        limiter=InMemoryRateLimiter(),
        policies=(policy,),
    )
    app.add_middleware(RequestContextMiddleware)

    @app.post("/cost")
    async def cost_endpoint() -> dict:
        """返回测试成功标记。"""
        return {"ok": True}

    @app.post("/upload")
    async def upload_endpoint() -> dict:
        """返回请求体积测试成功标记。"""
        return {"ok": True}

    client = TestClient(app)
    headers = {"X-Guest-ID": "guardrail-test-guest-0001"}
    first = client.post("/cost", headers=headers)
    second = client.post("/cost", headers=headers)
    blocked = client.post("/cost", headers=headers)
    oversized = client.post("/upload", content="x" * 32)

    assert first.status_code == 200
    assert len(first.headers["X-Request-ID"]) == 32
    assert second.headers["X-RateLimit-Remaining"] == "0"
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]
    assert oversized.status_code == 413


def test_admin_dependency_rejects_regular_users() -> None:
    """管理员边界应允许显式管理员并拒绝普通账号。"""
    regular_user = SimpleNamespace(is_admin=False)
    admin_user = SimpleNamespace(is_admin=True)

    with pytest.raises(HTTPException) as error:
        require_admin_user(regular_user)

    assert error.value.status_code == 403
    assert require_admin_user(admin_user) is admin_user
