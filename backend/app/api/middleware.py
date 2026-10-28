"""API 请求关联、体积限制、指标和分布式限流中间件。"""

from __future__ import annotations

import logging
import re
import time
from typing import Callable, Optional, Sequence
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..config import get_settings
from ..observability import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    RATE_LIMIT_REJECTIONS,
    observation_context,
)
from ..services.rate_limit_service import (
    RateLimitPolicy,
    RedisRateLimiter,
    build_rate_limit_policies,
    find_policy,
    get_rate_limiter,
    request_subject,
)


logger = logging.getLogger(__name__)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_OPAQUE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{24,}$")


def _request_id_from_header(request: Request) -> str:
    """
    接受格式受限的上游请求 ID，否则生成服务端随机值。

    Args:
        request: 当前 HTTP 请求。

    Returns:
        可安全写入日志和响应头的请求 ID。
    """
    candidate = request.headers.get("X-Request-ID", "").strip()
    if _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid4().hex


def _route_label(request: Request) -> str:
    """
    获取 FastAPI 模板路径，未匹配时压缩高基数字段。

    Args:
        request: 已执行或正在执行的请求。

    Returns:
        适合作为 Prometheus 标签的低基数路径。
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str):
        return template
    segments = [
        "{id}" if _OPAQUE_SEGMENT.fullmatch(segment) else segment
        for segment in request.url.path.split("/")
    ]
    return "/".join(segments) or "/"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个请求绑定关联 ID，并记录统一访问日志和 HTTP 指标。"""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """
        执行下游应用并在成功或异常时记录请求指标。

        Args:
            request: 当前请求。
            call_next: Starlette 下游处理器。

        Returns:
            附带 `X-Request-ID` 的原始响应。
        """
        request_id = _request_id_from_header(request)
        started_at = time.perf_counter()
        status_code = 500
        with observation_context(request_id=request_id):
            try:
                response = await call_next(request)
                status_code = response.status_code
            except Exception:
                logger.exception(
                    "http_request_failed method=%s path=%s",
                    request.method,
                    request.url.path,
                )
                raise
            finally:
                elapsed = time.perf_counter() - started_at
                route = _route_label(request)
                HTTP_REQUESTS.labels(
                    request.method,
                    route,
                    str(status_code),
                ).inc()
                HTTP_REQUEST_DURATION.labels(
                    request.method,
                    route,
                ).observe(elapsed)
                logger.info(
                    "http_request method=%s route=%s status=%s elapsed=%.3f",
                    request.method,
                    route,
                    status_code,
                    elapsed,
                )
            response.headers["X-Request-ID"] = request_id
            return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """在解析 JSON 或表单前拒绝声明体积过大的请求。"""

    def __init__(self, app, max_body_bytes: int) -> None:
        """
        初始化请求体积上限。

        Args:
            app: 下游 ASGI 应用。
            max_body_bytes: 允许的最大 Content-Length 字节数。
        """
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """
        检查 Content-Length 并决定是否继续读取请求体。

        Args:
            request: 当前请求。
            call_next: 下游处理器。

        Returns:
            413 JSON 响应或下游原始响应。
        """
        raw_length = request.headers.get("content-length")
        if raw_length:
            try:
                content_length = int(raw_length)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Content-Length格式不正确"},
                )
            if content_length > self.max_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "请求体超过允许大小"},
                )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """对高成本和高风险接口执行共享 Redis 限流。"""

    def __init__(
        self,
        app,
        limiter: Optional[RedisRateLimiter] = None,
        policies: Optional[Sequence[RateLimitPolicy]] = None,
    ) -> None:
        """
        初始化限流依赖和规则。

        Args:
            app: 下游 ASGI 应用。
            limiter: 测试可替换的限流实现。
            policies: 测试可替换的规则集合。
        """
        super().__init__(app)
        self.limiter = limiter or get_rate_limiter()
        self.policies = tuple(policies or build_rate_limit_policies())

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """
        匹配规则、消费配额并设置标准限流响应头。

        Args:
            request: 当前请求。
            call_next: 下游处理器。

        Returns:
            429 JSON 响应或附带配额响应头的下游响应。
        """
        if not get_settings().rate_limit_enabled:
            return await call_next(request)
        policy = find_policy(
            self.policies,
            request.method,
            request.url.path,
        )
        if policy is None:
            return await call_next(request)

        decision = self.limiter.check(request_subject(request), policy)
        headers = {
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": str(decision.remaining),
            "X-RateLimit-Policy": decision.policy,
        }
        if not decision.allowed:
            RATE_LIMIT_REJECTIONS.labels(decision.policy).inc()
            headers["Retry-After"] = str(decision.retry_after)
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后重试"},
                headers=headers,
            )
        response = await call_next(request)
        response.headers.update(headers)
        return response
