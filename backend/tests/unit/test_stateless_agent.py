"""无状态控制 Agent 测试。"""

from app.agents.stateless_control_agent import StatelessControlAgent


class FakeLLM:
    """记录调用参数的测试 LLM。"""

    def __init__(self):
        """初始化调用记录。"""
        self.calls = []

    def invoke(self, messages, **kwargs):
        """
        记录消息并返回固定结果。

        Args:
            messages: Agent 发送的消息列表。
            **kwargs: 附加调用参数。

        Returns:
            固定测试响应。
        """
        self.calls.append(messages)
        return '{"status": "ok"}'


def test_agent_does_not_share_history():
    """验证连续请求不会写入或读取 Agent 历史。"""
    llm = FakeLLM()
    agent = StatelessControlAgent(
        name="测试控制Agent",
        llm=llm,
        system_prompt="你是测试Agent。",
    )

    agent.run("规划北京行程")
    agent.run("规划上海行程")

    assert agent.get_history() == []
    assert len(llm.calls) == 2
    assert len(llm.calls[1]) == 2
    assert "北京" not in str(llm.calls[1])
    assert "上海" in str(llm.calls[1])