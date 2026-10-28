"""OpenAI 兼容 LLM token 与费用采集测试。"""

from contextvars import ContextVar
from types import SimpleNamespace

from app.services.llm_service import UsageTrackingHiliAgentsLLM


class FakeCompletions:
    """返回带标准 usage 字段的聊天完成接口替身。"""

    def create(self, **kwargs):
        """
        构造固定文本和 token usage 的兼容响应。

        Args:
            **kwargs: LLM 适配器传入的标准和扩展请求参数。

        Returns:
            具有 choices 与 usage 属性的响应对象。
        """
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="测试响应"),
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=1000,
                completion_tokens=500,
                total_tokens=1500,
            ),
        )


def build_llm() -> UsageTrackingHiliAgentsLLM:
    """
    构造不访问网络但保留真实适配器逻辑的 LLM 实例。

    Returns:
        使用固定 OpenAI 兼容响应的 usage 跟踪 LLM。
    """
    llm = UsageTrackingHiliAgentsLLM.__new__(UsageTrackingHiliAgentsLLM)
    llm.model = "test-model"
    llm.temperature = 0.2
    llm.max_tokens = 1000
    llm._input_cost = 2
    llm._output_cost = 8
    llm._last_usage = ContextVar("test_llm_last_usage", default=None)
    llm._client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    return llm


def test_invoke_captures_provider_tokens_and_configured_cost() -> None:
    """一次模型调用应保存真实 token，并按显式单价计算费用。"""
    llm = build_llm()

    response = llm.invoke([{"role": "user", "content": "测试"}])
    usage = llm.consume_last_usage()

    assert response == "测试响应"
    assert usage == {
        "input_tokens": 1000,
        "output_tokens": 500,
        "total_tokens": 1500,
        "cost_cny": 0.006,
    }
    assert llm.consume_last_usage() is None
