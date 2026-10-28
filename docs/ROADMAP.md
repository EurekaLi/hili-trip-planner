# 智能旅行规划平台路线图

本文档将长期目标拆分为可验收阶段。每个阶段完成后必须运行对应测试并
形成独立提交，不能仅以页面可展示作为完成标准。

## Phase 0：迁移与可复现基线

- 推送地图 MCP fork，消除不可获取的子模块提交。
- 抽取独立仓库并保留 Git 历史。
- 使用 `pyproject.toml` 管理后端依赖，移除手工 `PYTHONPATH`。
- 提供无密钥环境变量模板和本地启动脚本。
- 建立后端测试、前端构建和基础冒烟测试 CI。

完成标准：新机器从零克隆后，可以按 README 启动并通过全部检查。

## Phase 1：异步任务与真实进度

当前状态：进行中。已完成 Redis 任务仓储、Celery 投递与 worker、任务查询与
取消、幂等提交、SSE 真实阶段进度、前端刷新恢复和断线轮询，以及 worker
丢失后的有限恢复与原子执行次数预算；已通过仓储、worker、HTTP 接口单元测试
和真实 Redis/Celery broker 冒烟测试，并通过强制终止 worker 的消息重投故障
注入测试。待完成真实 LLM/MCP 全链路并发测试后再关闭本阶段。

- 将规划请求改为任务创建接口，返回 `task_id` 和 `202 Accepted`。
- 使用 Redis 任务队列执行 LLM、MCP 和路线补全。
- 使用 SSE 推送数据查询、控制 Agent、Planner、路线补全等真实进度。
- 支持任务查询、取消、幂等提交、超时和有限重试。
- 前端支持刷新恢复、失败重试和取消生成。

完成标准：浏览器断开不终止任务，并发请求不会阻塞 FastAPI 事件循环。

## Phase 2：事实可信与结构化多 Agent

当前状态：已完成。景点、天气和酒店控制 Agent 已使用 Pydantic 契约并发执行；
最终景点、酒店、餐厅、天气、路线和预算均携带可信类型与来源。后端会确定性
重算预算，保存四个 Agent 的结构化决策、真实 token、可配置费用以及每次高德
MCP 调用的次数、耗时和错误分类。日期跨度和天气覆盖范围已纳入校验；开放时间、
闭馆日和预约要求已在 Phase 3 转换为带来源的时间表约束。

- 为景点、天气、酒店控制 Agent 建立 Pydantic 输入输出契约。
- 保存每个 Agent 的结构化决策、耗时、token、费用和降级原因。
- 餐厅使用真实 POI，票价和住宿价格标记真实、估算或未知来源。
- 后端确定性重算预算，补位或编辑后不得沿用旧预算。
- 校验日期范围、天气覆盖范围、开放时间、闭馆日和预约要求。

完成标准：最终响应中的事实字段都有来源和更新时间，估算值不会冒充实时值。

## Phase 3：时间表与路线优化

当前状态：已完成。确定性优化器会比较 Planner 原分组和地理连续分组，使用真实
路线时长、跨区域惩罚、路线可达性和每日游览容量选择方案；单任务路线调用预算
默认为18次，超出后返回明确标记的球面距离估算。最终每天包含无重叠的游览、
用餐、通勤时间轴以及可执行状态。高德 fork 已透出步行、驾车、公交 polyline，
前端地图按真实 geometry 绘制，降级线使用虚线。北京三日固定数据集的可执行日、
路线覆盖和游览覆盖均为100%，重叠为0，最长单段通勤为30分钟。

- 引入时间段、营业时间、用餐时间和通勤时间模型。
- 使用真实路线时间优化每天的景点分组和顺序。
- 返回路线 geometry，并在地图上绘制真实轨迹。
- 支持全天景点、夜游景点、跨区域惩罚和不可达约束。
- 建立固定城市数据集评测行程可执行性。

完成标准：每天生成可执行时间轴，所有相邻地点都有路线或明确降级说明。

验收证据：

```powershell
cd backend
$env:PYTHONPATH=(Resolve-Path "..\..\..\..").Path
python -m pytest tests -q
python -m evals.itinerary_feasibility
```

## Phase 4：持久化与用户闭环

当前状态：已完成。Redis/Celery 继续保存短期任务运行态，SQLAlchemy 数据库保存
用户、会话摘要、生成任务审计、行程资产、不可变版本、外部调用、分享和反馈。
游客生成结果会自动归档，注册或登录在同一事务中认领游客行程与未完成任务；
前端支持行程库、跨设备恢复、景点替换、跨天拖放、地点锁定、版本恢复和只读
分享。所有编辑通过结构化命令提交，后端使用乐观锁追加版本并确定性重算路线、
时间表和预算。PDF 沿用前端导出，日历由后端生成 ICS，导航使用高德 URI。

- 建立用户、行程、行程版本、任务、调用记录和反馈数据表。
- 支持游客草稿、登录保存、历史版本和跨设备恢复。
- 支持景点替换、跨天拖拽、锁定地点和局部重新规划。
- 修改后重新计算时间、路线和预算。
- 提供只读分享链接、PDF、日历和导航入口。

完成标准：用户可以完成创建、编辑、保存、恢复、分享的完整流程。

验收证据：

```powershell
cd backend
$env:PYTHONPATH=(Resolve-Path "..\..\..\..").Path
python -m pytest -m integration -q
python -m alembic upgrade head

cd ..\frontend
npm run build
```

已完成 SQLite 的 Alembic `upgrade head -> 表结构检查 -> downgrade base` 往返验证；
浏览器检查覆盖桌面和 390×844 窄屏下的行程库、账号弹窗、只读分享和反馈区域。

## Phase 5：安全、成本与运营

当前状态：已完成。账号和资产权限沿用 Phase 4 的服务端会话模型，并新增管理员
角色、密码确认后的账号数据永久删除、Redis 分布式限流、请求体限制，以及生产
可选的 Turnstile 服务端校验。每次生成有独立的 LLM、地图调用次数和已知费用
预算；高德、LLM、Commons、Unsplash 和 Turnstile 使用 Redis 共享熔断状态。
API 与 Worker 通过 request ID、job ID、JSON 日志、Prometheus 指标、可选 Sentry
和 SQL 调用审计关联。管理员看板可查看成功率、P50/P95、费用、缓存命中、失败
分类和单任务 Agent/外部调用轨迹。隐私页、图片作者/许可链接和第三方用途说明已
进入前端实际流程。

- 加入认证、权限、IP/用户限流、验证码和请求大小限制。
- 为 LLM、MCP 和图片服务设置单任务调用预算与熔断策略。
- 使用结构化日志、request ID、指标、链路追踪和错误告警。
- 建立运营后台，查看成功率、延迟、费用、缓存命中率和失败分类。
- 完成隐私政策、第三方条款和图片署名展示。

完成标准：服务可控制滥用与费用，并能从监控中定位一次失败请求。

验收证据：

```powershell
cd backend
python -m alembic upgrade head
$env:TEST_REDIS_URL="redis://127.0.0.1:6380/0"
python -m pytest tests -q

cd ..\frontend
npm run build
```

Alembic 已完成 `upgrade head -> 新增列检查 -> downgrade base` 的独立 SQLite
往返验证；后端回归包含真实 Redis 仓储边界，前端生产构建包含运营、隐私、
Turnstile 和图片署名视图。

## Phase 6：生产部署与简历证据

当前状态：进行中。已完成后端/前端多阶段镜像、PostgreSQL/Redis/API/Worker/Web
编排、独立 Alembic 迁移、liveness/readiness、备份恢复与应用回滚脚本；CI 已拆分
后端、前端、Playwright、Compose、Trivy、依赖和密钥扫描门禁。已建立无付费 E2E、
k6 公开读基线、前端 bundle 预算，以及可选 Prometheus/Grafana 仪表盘。待 Docker
镜像源恢复后完成本机全栈启动、PostgreSQL 备份恢复和回滚实演，再关闭本阶段。

- 提供 Docker、数据库迁移、测试环境和生产环境配置。
- 建立 CI/CD、回滚、备份恢复和健康检查。
- 完成 API、集成、MCP 契约、前端 E2E、并发和故障注入测试。
- 建立生成成功率、事实覆盖率、路线覆盖率、P95 延迟和单次成本基线。
- 在 README 中展示架构图、关键决策、演示地址和可复现评测结果。

完成标准：测试环境和生产环境均可重复部署，简历中的指标可由报告验证。

当前验证证据（2026-09-17）：

- 后端全量回归：`85 passed, 3 skipped`；跳过项需要显式 Redis/Celery 故障环境。
- 部署脚本测试：`4 passed`。
- Playwright 桌面/移动端：`4 passed`，全程使用 API mock，不产生第三方费用。
- 前端生产构建和 bundle 预算通过：总 JS 2,106,979 bytes，最大 chunk
  1,246,733 bytes。
- npm 运行/测试依赖与 Python 生产锁文件均未发现已知漏洞。
- Compose（含 observability profile）配置解析通过；本机镜像构建仍受 Docker
  Desktop 配置的 Registry Mirror `403` 阻塞，尚未形成全栈、备份恢复和回滚实演证据。
