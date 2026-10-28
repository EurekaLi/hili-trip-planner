# 外部提供方契约测试

此阶段预留高德 MCP、DashScope LLM、Commons、Unsplash 和 Turnstile 沙箱契约。
测试必须由 `RUN_LIVE_CONTRACT_TESTS=1` 显式开启，并同时设置单次调用数、费用上限、
总超时和报告清理策略。默认 PR、Compose 冒烟和 k6 基线均不得读取生产凭据。
