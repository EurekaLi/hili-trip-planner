"""高德 MCP 请求级调用计量测试。"""

from contextvars import ContextVar
from threading import Lock

import pytest

from app.services.amap_service import AmapService


class FakeMCPTool:
    """返回固定JSON并可模拟超时的 MCP 测试替身。"""

    def call_tool_raw(self, tool_name: str, arguments: dict, timeout: int) -> str:
        """
        返回空结果，或为指定工具抛出超时异常。

        Args:
            tool_name: 测试调用的工具名称。
            arguments: 服务层传入的参数。
            timeout: 服务层传入的超时秒数。

        Returns:
            可被服务解析的JSON字符串。

        Raises:
            TimeoutError: 工具名称为 `maps_timeout` 时抛出。
        """
        if tool_name == "maps_timeout":
            raise TimeoutError("测试超时")
        return '{"pois": []}'


def build_service() -> AmapService:
    """
    构造不连接真实 MCP 进程的高德服务。

    Returns:
        已初始化调用跟踪字段的高德服务实例。
    """
    service = AmapService.__new__(AmapService)
    service.mcp_tool = FakeMCPTool()
    service._call_trace = ContextVar("test_amap_call_trace", default=None)
    service._call_counts = {}
    service._call_count_lock = Lock()
    return service


def test_amap_calls_record_success_failure_count_and_elapsed_time() -> None:
    """同一请求中的成功和超时调用都应形成独立计量记录。"""
    service = build_service()
    metrics, token = service.begin_call_trace()

    try:
        assert service._call_tool("maps_text_search", {}) == {"pois": []}
        with pytest.raises(TimeoutError, match="测试超时"):
            service._call_tool("maps_timeout", {})
    finally:
        service.end_call_trace(token)

    assert [metric.operation for metric in metrics] == [
        "maps_text_search",
        "maps_timeout",
    ]
    assert metrics[0].succeeded is True
    assert metrics[1].succeeded is False
    assert metrics[1].error_type == "TimeoutError"
    assert all(metric.elapsed_seconds >= 0 for metric in metrics)
    assert service._call_counts == {
        "maps_text_search": 1,
        "maps_timeout": 1,
    }
