# 后端测试分层

测试按失败责任而不是开发时间组织。目录名称会自动成为 pytest marker，规则定义在
`tests/conftest.py`，marker 注册在 `pytest.ini`。

## 测试阶段

| 阶段 | 目录 / marker | 目标 | 默认外部依赖 |
| --- | --- | --- | --- |
| 1 | `unit` | 模型、清洗、预算、限流、优化器等隔离逻辑 | 无 |
| 2 | `contract` | Agent JSON、地图适配器、进度事件和调用指标契约 | 无 |
| 3 | `integration` | FastAPI、SQLAlchemy、账号闭环及 Redis 仓储边界 | SQLite；Redis 用例按环境跳过 |
| 4 | `system` | Worker 编排和固定数据集质量门禁 | 确定性 fake 与本地数据集 |
| 5 | `resilience` | Worker 丢失、消息重投和依赖故障恢复 | 独立测试 Redis，显式启用 |
| 6 | 项目级 `tests/phase6` | E2E、性能、部署、生产安全和真实提供方契约 | Phase 6 环境 |

## 推荐执行顺序

保存代码后的快速反馈：

```powershell
python -m pytest -m "unit or contract" -q
```

提交前的本地门禁：

```powershell
$env:TEST_REDIS_URL="redis://127.0.0.1:6380/0"
python -m pytest -m "unit or contract or integration or system" -q
```

全量非破坏性回归：

```powershell
python -m pytest tests -q
```

故障注入会启动并终止真实 Celery worker，因此必须使用隔离 Redis 并显式开启：

```powershell
$env:TEST_REDIS_URL="redis://127.0.0.1:6380/0"
$env:RUN_CELERY_FAULT_TEST="1"
python -m pytest -m resilience -q -s
```

## 新增边界用例

- 并行控制 Agent 共享 `TaskBudget` 时，100 次调用必须原子计数且第 101 次被拒绝。
- 一个游客耗尽限流配额不能影响另一个游客，验证限流主体隔离。
- Turnstile action 不匹配属于业务拒绝，不打开提供方熔断；网络故障则必须累计失败。
- request/job 观测上下文退出后必须恢复，避免不同请求之间串号。
- JSON 日志必须保留关联 ID，同时清除密码、Token 和 API Key。
- 外部调用审计必须通过当前 job 上下文落库，并保留稳定错误分类。

测试不得调用真实付费 LLM、地图或图片接口。需要真实提供方的契约验证统一放在
Phase 6 的 `live_contract`，并由独立凭据、费用上限和显式开关保护。
