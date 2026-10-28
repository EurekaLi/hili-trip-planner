"""旅行助手运行配置校验测试。"""

import pytest

from app import config


def test_validate_config_accepts_actual_dashscope_key(monkeypatch) -> None:
    """配置校验必须识别 LLM 服务真实使用的 DASHSCOPE_API_KEY。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(
        config,
        "settings",
        config.Settings(
            amap_api_key="test-amap-key",
            trip_job_time_limit_seconds=360,
            trip_job_visibility_timeout_seconds=420,
        ),
    )

    assert config.validate_config() is True


def test_validate_config_rejects_unsafe_visibility_timeout(monkeypatch) -> None:
    """Redis 可见性超时不大于任务硬超时时应阻止服务启动。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(
        config,
        "settings",
        config.Settings(
            amap_api_key="test-amap-key",
            trip_job_time_limit_seconds=360,
            trip_job_visibility_timeout_seconds=360,
        ),
    )

    with pytest.raises(ValueError, match="VISIBILITY_TIMEOUT"):
        config.validate_config()


def test_validate_config_rejects_unbounded_attempt_budget(monkeypatch) -> None:
    """执行次数超过费用保护上限时应阻止服务启动。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(
        config,
        "settings",
        config.Settings(
            amap_api_key="test-amap-key",
            trip_job_max_attempts=6,
        ),
    )

    with pytest.raises(ValueError, match="MAX_ATTEMPTS"):
        config.validate_config()


def test_validate_config_rejects_soft_timeout_not_below_hard_timeout(
    monkeypatch,
) -> None:
    """软超时不小于硬超时时无法保证 worker 有机会持久化失败状态。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(
        config,
        "settings",
        config.Settings(
            amap_api_key="test-amap-key",
            trip_job_soft_time_limit_seconds=360,
            trip_job_time_limit_seconds=360,
        ),
    )

    with pytest.raises(ValueError, match="软超时"):
        config.validate_config()


def test_validate_config_rejects_negative_llm_pricing(monkeypatch) -> None:
    """LLM token 单价为负数时应阻止费用统计使用无效配置。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(
        config,
        "settings",
        config.Settings(
            amap_api_key="test-amap-key",
            llm_input_cost_per_million_tokens_cny=-1,
        ),
    )

    with pytest.raises(ValueError, match="单价不能为负数"):
        config.validate_config()


def test_validate_config_rejects_excessive_route_call_budget(monkeypatch) -> None:
    """行程优化路线调用预算超过上限时应阻止潜在费用失控。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(
        config,
        "settings",
        config.Settings(
            amap_api_key="test-amap-key",
            itinerary_route_call_budget=61,
        ),
    )

    with pytest.raises(ValueError, match="ROUTE_CALL_BUDGET"):
        config.validate_config()
