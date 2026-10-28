# Phase 6 发布门禁

此目录用于跨前端、后端和基础设施的生产发布测试，不纳入后端 pytest 默认收集。
GitHub Actions 已按下列位置形成由快到慢的独立 Job：

- `e2e/`：Playwright 覆盖异步生成、结果展示与移动视口，API mock 不产生费用。
- `performance/`：k6 记录公开读接口失败率和 P95；付费生成压测保持显式门禁。
- `deployment/`：Compose 冒烟、迁移、健康检查，以及备份/恢复/回滚脚本测试。
- `security/`：Nginx 安全响应头、依赖审计、Gitleaks 和 Trivy 镜像扫描。
- `live_contract/`：预留显式开启的高德 MCP、LLM 和图片提供方沙箱契约测试。

每类测试必须提供环境前置条件、超时、费用上限、清理策略和机器可读报告；默认
PR 流程不得访问生产凭据或真实付费接口。完整 Compose 冒烟会在结束时删除测试
卷，生产备份和回滚流程见 `docs/DEPLOYMENT.md`。

Windows 本地执行浏览器测试时，可先单独启动 Vite，再把地址传给 Playwright，
以避开 PowerShell 对 Playwright 子进程生命周期处理不一致的问题：

```powershell
npm --prefix frontend run dev -- --host 127.0.0.1 --port 4173
$env:PLAYWRIGHT_BASE_URL = "http://127.0.0.1:4173"
npm run test:e2e -- --project=chromium
```

CI/Linux 默认由 Playwright 配置直接启动 Vite，不需要设置该变量。
