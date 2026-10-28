"""无状态控制 Agent。"""

from typing import Any

from hili_agents import MySimpleAgent


class StatelessControlAgent(MySimpleAgent):
    """不读取和写入历史消息的请求级控制 Agent。"""

    def run(self, input_text: str, **kwargs: Any) -> str:
        """
        使用系统提示词和当前输入执行一次独立推理。

        Args:
            input_text: 当前请求的输入文本。
            **kwargs: 传递给 LLM 的附加参数。

        Returns:
            LLM 生成的完整响应文本。
        """
        messages = [
            {
                "role": "system",
                "content": self.system_prompt or "你是一个有用的AI助手。",
            },
            {
                "role": "user",
                "content": input_text,
            },
        ]

        return self.llm.invoke(messages, **kwargs)