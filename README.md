# HiliAgents 智能旅行助手

基于 HiliAgents 的多 Agent 旅行规划应用。高德 MCP 提供真实 POI、天气和路线；
景点、天气、住宿控制 Agent 并行分析候选数据，Planner 生成初稿，确定性优化器
校验景点、营业时间、游览时长与通勤。应用提供异步生成进度、地图、账户、行程
版本编辑、分享和导出。

本仓库只包含旅行助手应用。`hili_agents` 框架源码由另一个本地仓库提供，
**不会被提交到本 GitHub 仓库**。部署时需要单独取得兼容版本的框架源码。

## 主要技术

- 后端：Python 3.11、FastAPI、Pydantic、HiliAgents、Celery、Redis、PostgreSQL、SQLAlchemy、Alembic。
- 前端：Vue 3、TypeScript、Vite、Ant Design Vue、高德 JS API。
- 运维：Docker Compose、Nginx、Prometheus、Grafana、Pytest、Playwright、k6。
- 地图工具：随应用提供经过修复的 `amap-mcp-server` fork，保留其 MIT 许可证和原项目署名。

## 目录

```text
backend/       FastAPI、Agent 编排、数据适配、Worker、数据库迁移和后端测试
frontend/      Vue 应用和浏览器地图
deploy/        Prometheus/Grafana 配置
scripts/       备份、恢复、回滚和前端产物检查
tests/phase6/  浏览器、部署、性能、安全和真实服务契约测试
docs/          架构、部署及项目技术复盘
compose.yaml   本地发布形态
```

## 快速启动：Docker Compose

准备一个**独立的** HiliAgents 源码目录，其根目录下必须有 `hili_agents/`。
在项目根目录执行：

```powershell
Copy-Item .env.compose.example .env
```

编辑 `.env`，填写 `HILI_AGENTS_SOURCE`（框架源码目录的绝对路径或相对于本项目
目录的路径）、数据库密码、`AMAP_API_KEY`、`DASHSCOPE_API_KEY`、高德浏览器
Key 等。框架目录只作为 Docker BuildKit 的额外构建上下文，位于本 Git 仓库外。

```powershell
docker compose config --quiet
docker compose build
docker compose up -d
python tests/phase6/deployment/smoke_compose.py
```

访问 `http://localhost:8080`；API 文档在 `http://127.0.0.1:8000/docs`。
数据库迁移作为一次性 Compose 服务运行。可选监控服务：

```powershell
docker compose --profile observability up -d
```

`HILI_AGENTS_SOURCE` 指向的框架版本必须支持项目实际使用的 `MySimpleAgent`、
`HiliAgentsLLM`、`MCPTool` 以及相关异常接口。独立公开仓库目前不能只凭
`git clone` 自行取得该外部框架；发布前应为它提供可访问的固定版本或私有包。

## 本地开发与测试

在 Python 3.11 虚拟环境中，显式将独立框架目录加入当前进程的模块搜索路径：

```powershell
$env:PYTHONPATH = 'D:\path\to\separate-hili-agents'
python -m pip install --require-hashes -r backend/requirements.lock
python -m pip install --require-hashes -r backend/requirements-test.lock
Set-Location backend
python -m pytest
python -m uvicorn app.api.main:app --reload
```

本地启动仍需 PostgreSQL/Redis 和 API 密钥；完整环境优先使用 Compose。
前端开发使用 `cd frontend`、`npm ci`、`npm run dev`。后端生产镜像使用
Python 3.11 Linux 专用的 `backend/requirements-linux.lock`。
公开 GitHub Actions 当前只验证前端构建；后端自动化 CI 需要先提供可访问且
固定版本的 HiliAgents 分发物，避免工作流暗中依赖本机父目录。

更多验证方式见 [Phase 6 测试说明](tests/phase6/README.md)。

## 发布与安全

- `.env`、备份、日志、报告、数据库与 `node_modules` 均被 Git 忽略；只提交
  `.env.compose.example`，不要把真实密钥放进示例文件。
- `backend/vendor/amap-mcp-server/` 是独立的第三方 fork 副本，保留
  [MIT 许可证](backend/vendor/amap-mcp-server/LICENSE)。如改回 Git 子模块，
  需同步维护 `.gitmodules` 和 CI 的递归检出设置。
- Compose 使用本地框架源码作为额外构建上下文；这不会把框架源码放进 Git 仓库，
  但它会进入生成的后端镜像。公开推送镜像前应检查框架的发布许可。
- 生产环境需要自行配置 TLS、域名、持久化卷、密钥管理和告警。当前验收是本地
  Compose 发布形态，不代表已有公网生产流量。

发布、备份和回滚步骤见 [部署文档](docs/DEPLOYMENT.md)。技术设计与指标口径见
[项目技术复盘](docs/PROJECT_TECHNICAL_AND_RESUME.md)。

## 致谢

- [HelloAgents](https://github.com/datawhalechina/Hello-Agents)：初始学习模板参考。
- [amap-mcp-server](https://github.com/sugarforever/amap-mcp-server)：高德地图 MCP 上游项目。
