"""请求级上下文、结构化日志和进程指标。"""

from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional, Tuple

from prometheus_client import Counter, Gauge, Histogram


_request_id: ContextVar[str] = ContextVar("request_id", default="")
_job_id: ContextVar[str] = ContextVar("job_id", default="")
_SENSITIVE_PATTERN = re.compile(
    r"(?i)(api[_-]?key|authorization|cookie|password|session|token)"
    r"\s*[:=]\s*([^\s,;]+)"
)

HTTP_REQUESTS = Counter(
    "trip_planner_http_requests_total",
    "HTTP requests processed by the API.",
    ("method", "route", "status"),
)
HTTP_REQUEST_DURATION = Histogram(
    "trip_planner_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ("method", "route"),
)
RATE_LIMIT_REJECTIONS = Counter(
    "trip_planner_rate_limit_rejections_total",
    "Requests rejected by a named rate-limit policy.",
    ("policy",),
)
EXTERNAL_CALLS = Counter(
    "trip_planner_external_calls_total",
    "External provider calls grouped by result.",
    ("provider", "operation", "outcome"),
)
EXTERNAL_CALL_DURATION = Histogram(
    "trip_planner_external_call_duration_seconds",
    "External provider call latency in seconds.",
    ("provider", "operation"),
)
EXTERNAL_CALL_COST = Counter(
    "trip_planner_external_call_cost_cny_total",
    "Known external provider cost in CNY.",
    ("provider", "operation"),
)
TASKS = Counter(
    "trip_planner_generation_tasks_total",
    "Travel generation tasks grouped by terminal result.",
    ("status", "error_code"),
)
TASK_DURATION = Histogram(
    "trip_planner_generation_task_duration_seconds",
    "Travel generation task latency in seconds.",
    ("status",),
)
BUDGET_REJECTIONS = Counter(
    "trip_planner_budget_rejections_total",
    "Operations rejected by a task budget.",
    ("resource",),
)
CIRCUIT_STATE = Gauge(
    "trip_planner_circuit_open",
    "Whether a provider circuit is open.",
    ("provider",),
)
IMAGE_CACHE_LOOKUPS = Counter(
    "trip_planner_image_cache_lookups_total",
    "Image cache lookups grouped by outcome.",
    ("outcome",),
)


def get_request_id() -> str:
    """
    返回当前执行上下文中的请求 ID。

    Returns:
        当前请求 ID；未绑定时返回空字符串。
    """
    return _request_id.get()


def get_job_id() -> str:
    """
    返回当前执行上下文中的任务 ID。

    Returns:
        当前任务 ID；HTTP 请求尚未进入任务时返回空字符串。
    """
    return _job_id.get()


def set_request_id(value: str) -> Token:
    """
    为当前上下文设置请求 ID。

    Args:
        value: 已校验或由服务端生成的请求 ID。

    Returns:
        可用于恢复先前上下文的 ContextVar 令牌。
    """
    return _request_id.set(value)


def redact_sensitive(value: Any) -> str:
    """
    将日志文本中的常见凭据值替换为固定占位符。

    Args:
        value: 即将进入日志的任意值。

    Returns:
        不包含明显凭据值的文本。
    """
    return _SENSITIVE_PATTERN.sub(r"\1=[REDACTED]", str(value))


@contextmanager
def observation_context(
    request_id: Optional[str] = None,
    job_id: Optional[str] = None,
) -> Iterator[None]:
    """
    在代码块内绑定请求和任务关联标识，并在退出时恢复上下文。

    Args:
        request_id: 可选 HTTP 请求 ID。
        job_id: 可选异步任务 ID。

    Yields:
        已绑定观测上下文的执行控制权。
    """
    tokens: list[Tuple[ContextVar[str], Token]] = []
    if request_id is not None:
        tokens.append((_request_id, _request_id.set(request_id)))
    if job_id is not None:
        tokens.append((_job_id, _job_id.set(job_id)))
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


class JsonLogFormatter(logging.Formatter):
    """把标准日志记录转换为便于采集和检索的单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        """
        序列化日志并附加当前请求和任务关联字段。

        Args:
            record: Python logging 创建的日志记录。

        Returns:
            UTF-8 友好的单行 JSON 文本。
        """
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_sensitive(record.getMessage()),
            "request_id": get_request_id() or None,
            "job_id": get_job_id() or None,
        }
        if record.exc_info:
            payload["exception"] = redact_sensitive(
                self.formatException(record.exc_info)
            )
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    """
    为应用根日志器安装统一 JSON 输出格式。

    Args:
        level: Python logging 支持的日志级别名称。
    """
    root = logging.getLogger()
    root.setLevel(level.upper())
    if any(getattr(handler, "_trip_json_handler", False) for handler in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    handler._trip_json_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)


def record_external_call(
    provider: str,
    operation: str,
    succeeded: bool,
    elapsed_seconds: float,
    cost_cny: Optional[float] = None,
) -> None:
    """
    按统一标签记录一次第三方调用的次数、耗时和已知费用。

    Args:
        provider: 有界的提供方名称。
        operation: 有界的操作名称。
        succeeded: 调用是否成功。
        elapsed_seconds: 调用耗时秒数。
        cost_cny: 可选的人民币费用。
    """
    outcome = "success" if succeeded else "failure"
    EXTERNAL_CALLS.labels(provider, operation, outcome).inc()
    EXTERNAL_CALL_DURATION.labels(provider, operation).observe(
        max(0.0, elapsed_seconds)
    )
    if cost_cny is not None and cost_cny > 0:
        EXTERNAL_CALL_COST.labels(provider, operation).inc(cost_cny)
