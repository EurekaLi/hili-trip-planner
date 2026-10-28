# ADR-001：项目边界与独立仓库迁移

> 归档说明：本文记录从原示例目录抽取独立项目的决策背景。当前目录已经是
> 独立发布副本；本文中的原路径、前置条件和 `git subtree` 命令仅作历史参考。

- 状态：已接受
- 日期：2026-09-15
- 决策范围：`helloagents-trip-planner`

## 背景

旅行规划项目已经包含独立前端、FastAPI 后端、多 Agent 控制层、高德
MCP 数据层和图片降级链，不再只是最小教学示例。继续放在
`examples/AI-Assistant` 下会带来以下问题：

1. 后端依赖仓库根目录中的 `hili_agents` 源码，启动时必须手动设置
   `PYTHONPATH`。
2. 应用与框架共享 Git 历史、发布周期和工作区，难以建立独立 CI/CD、
   Release、Issue 和部署权限。
3. 简历查看者难以区分教程原始代码、框架修改和应用本身的工程贡献。
4. 应用内嵌一个 `amap-mcp-server` 子模块，远端提交可用性会进一步影响
   克隆和部署的可复现性。

## 决策

将当前目录作为迁移源，在开始数据库、异步任务队列和用户系统建设前，
抽取为独立仓库 `hili-trip-planner`。迁移使用保留历史的 Git 工具完成，
不得通过资源管理器直接复制后重新初始化历史。

迁移后遵循以下边界：

- 旅行规划应用拥有独立仓库、版本、README、CI/CD 和部署配置。
- `hili_agents` 作为外部依赖使用；开发环境可使用本地 editable 安装，
  CI 和生产环境必须固定到可访问的 tag 或 commit。
- `amap-mcp-server` fork 独立维护并固定版本，不携带未推送提交。
- 原 `examples/AI-Assistant` 目录只保留精简示例或指向独立项目的说明，
  不再维护第二份完整应用代码。

## 迁移前置条件

满足以下条件后才能执行目录抽取：

- 主仓库已经配置确认归属的远端。
- `amap-mcp-server` 的 `4b0b092` 或后续替代提交已经推送。
- 旅行项目路径没有未提交修改，子模块工作区干净。
- 后端测试与前端生产构建均通过。
- `.env.example` 不包含任何真实密钥。

## 历史保留方案

优先使用 `git subtree split` 创建只包含旅行项目历史的分支：

```bash
git subtree split \
  --prefix=examples/AI-Assistant/helloagents-trip-planner \
  -b trip-planner-extract
```

随后将该分支推送到已经确认归属的新仓库。执行前必须再次检查工作区、
远端地址和子模块提交，不在自动化脚本中猜测远端。

## 目标目录

```text
hili-trip-planner/
├── backend/
│   ├── app/
│   │   ├── api/v1/
│   │   ├── core/
│   │   ├── domain/
│   │   ├── agents/
│   │   ├── services/
│   │   ├── repositories/
│   │   ├── workers/
│   │   └── db/
│   ├── migrations/
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── features/
│   │   ├── composables/
│   │   ├── stores/
│   │   ├── router/
│   │   └── types/
│   └── tests/
├── infra/
├── docs/
├── scripts/
├── .github/workflows/
├── docker-compose.yml
├── .env.example
├── LICENSE
└── README.md
```

## 暂缓方案

在前置条件满足前，继续在当前路径修复生产阻断问题，但不创建新的顶层
副本，也不同时维护两个实现。大规模功能开发开始前必须重新检查本 ADR。

## 验收标准

迁移完成需要同时满足：

1. 新仓库可以从零克隆并递归获取全部依赖。
2. 不设置指向原仓库的 `PYTHONPATH` 也能启动后端。
3. 后端测试、前端构建和端到端冒烟测试可在 CI 中通过。
4. 独立仓库可以构建镜像并在测试环境部署。
5. 原示例目录不再包含需要双向同步的完整副本。
