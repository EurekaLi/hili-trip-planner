"""LLM服务模块。"""

from contextvars import ContextVar
from dataclasses import dataclass
import os
from typing import Any, Dict, Optional

from hili_agents import HiliAgentsLLM
from hili_agents.core.exceptions import HiliAgentsException

from ..config import get_settings


@dataclass(frozen=True)
class LLMUsageSnapshot:
    """一次提供方响应中返回的真实 token 与可选费用。"""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_cny: Optional[float]


class UsageTrackingHiliAgentsLLM(HiliAgentsLLM):
    """保留 HiliAgents 接口并采集 OpenAI 兼容响应 usage 的 LLM。"""

    def __init__(
        self,
        *args: Any,
        input_cost_per_million_tokens_cny: float = 0,
        output_cost_per_million_tokens_cny: float = 0,
        **kwargs: Any,
    ) -> None:
        """
        初始化 LLM 客户端和并发上下文隔离的 usage 存储。

        Args:
            *args: 传递给 HiliAgentsLLM 的位置参数。
            input_cost_per_million_tokens_cny: 每百万输入 token 人民币单价。
            output_cost_per_million_tokens_cny: 每百万输出 token 人民币单价。
            **kwargs: 传递给 HiliAgentsLLM 的关键字参数。
        """
        super().__init__(*args, **kwargs)
        self._input_cost = input_cost_per_million_tokens_cny
        self._output_cost = output_cost_per_million_tokens_cny
        self._last_usage: ContextVar[Optional[LLMUsageSnapshot]] = ContextVar(
            "llm_last_usage",
            default=None,
        )

    def invoke(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """
        非流式调用模型，并保存提供方返回的真实 token usage。

        Args:
            messages: OpenAI 兼容消息列表。
            **kwargs: 温度、最大输出 token 和提供方扩展参数。

        Returns:
            模型返回的文本内容。

        Raises:
            HiliAgentsException: 提供方请求失败时统一包装抛出。
        """
        self._last_usage.set(None)
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=kwargs.get("temperature", self.temperature),
                max_tokens=kwargs.get("max_tokens", self.max_tokens),
                **{
                    key: value
                    for key, value in kwargs.items()
                    if key not in {"temperature", "max_tokens"}
                },
            )
        except Exception as exc:
            raise HiliAgentsException(f"LLM调用失败: {exc}") from exc

        usage = getattr(response, "usage", None)
        if usage is not None:
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            total_tokens = int(
                getattr(usage, "total_tokens", input_tokens + output_tokens)
                or input_tokens + output_tokens
            )
            cost_cny = self._calculate_cost(input_tokens, output_tokens)
            self._last_usage.set(
                LLMUsageSnapshot(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    cost_cny=cost_cny,
                )
            )

        content = response.choices[0].message.content
        return content or ""

    def _calculate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
    ) -> Optional[float]:
        """
        使用显式配置的模型单价计算一次调用费用。

        Args:
            input_tokens: 提供方报告的输入 token 数。
            output_tokens: 提供方报告的输出 token 数。

        Returns:
            配置单价时返回人民币费用，否则返回 None。
        """
        if self._input_cost <= 0 and self._output_cost <= 0:
            return None
        cost = (
            input_tokens * self._input_cost
            + output_tokens * self._output_cost
        ) / 1_000_000
        return round(cost, 6)

    def consume_last_usage(self) -> Optional[Dict[str, Any]]:
        """
        读取并清除当前并发上下文中的最近一次 usage。

        Returns:
            可写入 Agent 指标的 token 与费用字典；无 usage 时返回 None。
        """
        usage = self._last_usage.get()
        self._last_usage.set(None)
        if usage is None:
            return None
        return {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "cost_cny": usage.cost_cny,
        }


_llm_instance: Optional[UsageTrackingHiliAgentsLLM] = None


def get_llm() -> UsageTrackingHiliAgentsLLM:
    """
    获取通义千问 LLM 单例。

    Returns:
        已完成 DashScope 配置的 LLM 实例。

    Raises:
        ValueError: DashScope API Key 未配置时抛出。
    """
    global _llm_instance

    if _llm_instance is None:
        api_key = os.getenv("DASHSCOPE_API_KEY")
        settings = get_settings()

        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY 未配置")

        _llm_instance = UsageTrackingHiliAgentsLLM(
            provider="qwen",
            api_key=api_key,
            model=os.getenv("DASHSCOPE_MODEL_ID", "qwen3.6-plus"),
            base_url=os.getenv(
                "DASHSCOPE_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            input_cost_per_million_tokens_cny=(
                settings.llm_input_cost_per_million_tokens_cny
            ),
            output_cost_per_million_tokens_cny=(
                settings.llm_output_cost_per_million_tokens_cny
            ),
        )

    return _llm_instance


def reset_llm() -> None:
    """
    清除当前进程中的 LLM 单例。

    测试隔离或运行时重新加载配置时调用；不会撤销已经发出的请求。
    """
    global _llm_instance
    _llm_instance = None

