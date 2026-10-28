"""FastAPI 主应用、运行时中间件和健康探针。"""

import secrets

import sentry_sdk
from fastapi import FastAPI, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from ..config import get_settings, validate_config, print_config
from ..observability import configure_logging
from ..persistence.database import init_database
from ..services.health_service import check_dependency_health
from .routes import admin, auth, compliance, library, trip, poi, map as map_routes
from .middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    RequestSizeLimitMiddleware,
)

# 获取配置
settings = get_settings()
configure_logging(settings.log_level)

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
    )

# 创建FastAPI应用
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="基于 HiliAgents 框架的智能旅行规划助手 API",
    docs_url="/docs",
    redoc_url="/redoc"
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    RequestSizeLimitMiddleware,
    max_body_bytes=settings.request_max_body_bytes,
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)

# 注册路由
app.include_router(trip.router, prefix="/api")
app.include_router(poi.router, prefix="/api")
app.include_router(map_routes.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(library.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(compliance.router, prefix="/api")


@app.on_event("startup")
async def startup_event():
    """应用启动事件"""
    print("\n" + "="*60)
    print(f"🚀 {settings.app_name} v{settings.app_version}")
    print("="*60)
    
    # 打印配置信息
    print_config()
    
    # 验证配置
    try:
        validate_config()
        if settings.database_auto_create:
            init_database()
        print("\n✅ 配置验证通过")
    except ValueError as e:
        print(f"\n❌ 配置验证失败:\n{e}")
        print("\n请检查.env文件并确保所有必要的配置项都已设置")
        raise
    
    print("\n" + "="*60)
    print("📚 API文档: http://localhost:8000/docs")
    print("📖 ReDoc文档: http://localhost:8000/redoc")
    print("="*60 + "\n")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭事件"""
    print("\n" + "="*60)
    print("👋 应用正在关闭...")
    print("="*60 + "\n")


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs",
        "redoc": "/redoc"
    }


@app.get("/health")
async def health():
    """返回兼容旧客户端的进程存活状态。"""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version
    }


@app.get("/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    """
    返回不访问外部依赖的进程存活探针。

    Returns:
        当前服务名称、版本和存活状态。
    """
    return {
        "status": "alive",
        "service": settings.app_name,
        "version": settings.app_version,
    }


@app.get("/health/ready", include_in_schema=False)
async def readiness() -> JSONResponse:
    """
    检查数据库和 Redis，决定实例是否可以接收业务流量。

    Returns:
        就绪时返回 200，依赖不可用时返回 503 及分类状态。
    """
    from starlette.concurrency import run_in_threadpool

    snapshot = await run_in_threadpool(check_dependency_health)
    return JSONResponse(
        status_code=(
            status.HTTP_200_OK
            if snapshot.ready
            else status.HTTP_503_SERVICE_UNAVAILABLE
        ),
        content=snapshot.to_dict(),
    )


@app.get("/metrics", include_in_schema=False)
async def metrics(
    authorization: str = Header(default=""),
) -> Response:
    """
    暴露 Prometheus 文本指标，并支持可选 Bearer Token 保护。

    Args:
        authorization: Prometheus 抓取请求的 Authorization 头。

    Returns:
        Prometheus 官方文本格式的当前进程指标。

    Raises:
        HTTPException: 配置抓取令牌但请求凭据不匹配时抛出。
    """
    expected = settings.metrics_bearer_token
    provided = authorization.removeprefix("Bearer ").strip()
    if expected and not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="指标访问凭据无效",
        )
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=True
    )

