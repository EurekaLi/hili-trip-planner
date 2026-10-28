# 生产部署与恢复

## 部署模型

单机 Docker Compose 运行六个服务：Nginx 静态前端、FastAPI、Celery Worker、
一次性 Alembic 迁移、PostgreSQL 和 Redis。浏览器只访问 Web 端口，Nginx 将
`/api` 与 `/health` 同源代理到 API；API 和 Worker 共用同一后端镜像。

## 首次启动

```powershell
Copy-Item .env.compose.example .env
# 编辑 .env，替换密码、AMAP、DashScope 和浏览器高德 Key
docker compose config --quiet
docker compose build
docker compose up -d
python tests/phase6/deployment/smoke_compose.py --report reports/smoke.json
```

`migrate` 必须以退出码 0 完成后 API 和 Worker 才会启动。`/health/live` 只检查
进程，`/health/ready` 同时检查 PostgreSQL 与 Redis；编排器应使用后者决定是否
导入流量。

后端镜像使用 `requirements-linux.lock` 和 `--require-hashes` 安装依赖，保证构建
与发布使用同一组经过校验的 Python 包。修改 `requirements.txt` 后必须重新生成
锁文件并通过依赖审计，不能在生产构建时临时解析版本。

可选监控栈使用 `docker compose --profile observability up -d` 启动 Prometheus
3.13 LTS 和 Grafana 13.2.2。捆绑的本地 Prometheus 配置要求
`METRICS_BEARER_TOKEN` 为空；生产环境启用令牌时，必须在外部采集器同步配置
Bearer 凭据。

## 发布

每个发布使用 Git SHA 或日期流水号作为不可变 `APP_VERSION`：

```powershell
$env:APP_VERSION = (git rev-parse --short HEAD)
docker compose build api worker web
python scripts/postgres_backup.py
docker compose up -d
python tests/phase6/deployment/smoke_compose.py --report reports/smoke-$env:APP_VERSION.json
```

部署前必须保留数据库备份和上一版本镜像。生产环境应由外部 TLS 入口终止 HTTPS，
并配置 `PUBLIC_ORIGIN=https://...`、`AUTH_COOKIE_SECURE=true` 和 Turnstile 正式域名。

## 备份恢复

```powershell
python scripts/postgres_backup.py --output backups/before-release.dump
python scripts/postgres_restore.py backups/before-release.dump --confirm-database trip_planner
```

备份脚本生成 PostgreSQL custom-format 文件和相邻 `.sha256`。恢复会执行
`pg_restore --clean --if-exists`，只能在维护窗口运行，并要求确认值与容器内
`POSTGRES_DB` 完全一致。恢复后重新运行 Alembic 和冒烟测试。

## 回滚

```powershell
python scripts/rollback_release.py <previous-app-version>
python tests/phase6/deployment/smoke_compose.py
```

回滚脚本只切换 API、Worker 与 Web 镜像，不自动降级数据库。迁移必须遵循至少
一个发布周期的前向/后向兼容；若确需恢复数据库，先停止写流量并使用部署前备份。

## 故障定位

```powershell
docker compose ps
docker compose logs --tail 200 migrate api worker web
docker compose exec postgres pg_isready -U trip_user -d trip_planner
docker compose exec redis redis-cli ping
```

若基础镜像在拉取元数据阶段返回 `403`，先检查 Docker Desktop 的 Registry
Mirror；这是镜像源访问问题，不能通过修改应用代码解决。切换镜像源后重新执行
`docker compose build --pull`。
