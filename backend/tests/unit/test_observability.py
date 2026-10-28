"""请求关联上下文和结构化日志安全边界测试。"""

import json
import logging

from app.observability import (
    JsonLogFormatter,
    get_job_id,
    get_request_id,
    observation_context,
)


def test_observation_context_binds_and_restores_identifiers() -> None:
    """嵌套观测上下文退出后不应把 request/job ID 泄漏给下一请求。"""
    assert get_request_id() == ""
    assert get_job_id() == ""

    with observation_context(request_id="request-1", job_id="job-1"):
        assert get_request_id() == "request-1"
        assert get_job_id() == "job-1"
        with observation_context(job_id="job-2"):
            assert get_request_id() == "request-1"
            assert get_job_id() == "job-2"
        assert get_job_id() == "job-1"

    assert get_request_id() == ""
    assert get_job_id() == ""


def test_json_log_formatter_correlates_and_redacts_credentials() -> None:
    """JSON 日志应包含关联 ID，并隐藏密码、Token 和 API Key。"""
    record = logging.LogRecord(
        name="trip.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="password=plain-secret token=session-secret api_key=provider-secret",
        args=(),
        exc_info=None,
    )

    with observation_context(request_id="request-log", job_id="job-log"):
        payload = json.loads(JsonLogFormatter().format(record))

    assert payload["request_id"] == "request-log"
    assert payload["job_id"] == "job-log"
    assert "plain-secret" not in payload["message"]
    assert "session-secret" not in payload["message"]
    assert "provider-secret" not in payload["message"]
    assert payload["message"].count("[REDACTED]") == 3
