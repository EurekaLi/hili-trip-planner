"""仅管理员可访问的运营总览和单任务追踪接口。"""

import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from ...models.operations_schemas import (
    CircuitStatusResponse,
    JobTraceResponse,
    OperationsOverviewResponse,
)
from ...persistence.models import UserRecord
from ...services.health_service import check_dependency_health
from ...services.operations_service import OperationsService
from ...services.platform_services import get_operations_service
from ...services.resilience_service import get_circuit_breaker
from ..dependencies import require_admin_user


router = APIRouter(prefix="/admin/operations", tags=["运营管理"])


@router.get(
    "/overview",
    response_model=OperationsOverviewResponse,
    summary="查看运营质量和成本总览",
)
async def get_operations_overview(
    hours: int = Query(default=24, ge=1, le=24 * 90),
    _: UserRecord = Depends(require_admin_user),
    service: OperationsService = Depends(get_operations_service),
) -> OperationsOverviewResponse:
    """
    汇总指定窗口内的任务、第三方调用、缓存和反馈数据。

    Args:
        hours: 向前统计的小时数。
        _: 已通过管理员权限校验的用户。
        service: 运营查询服务。

    Returns:
        指定窗口运营总览。
    """
    return await run_in_threadpool(service.overview, hours)


@router.get(
    "/jobs/{locator}",
    response_model=JobTraceResponse,
    summary="按任务或请求 ID 追踪一次生成",
)
async def get_job_trace(
    locator: str,
    _: UserRecord = Depends(require_admin_user),
    service: OperationsService = Depends(get_operations_service),
) -> JobTraceResponse:
    """
    查询一次生成请求的预算、错误、Agent 和外部调用轨迹。

    Args:
        locator: job_id 或 X-Request-ID。
        _: 已通过管理员权限校验的用户。
        service: 运营查询服务。

    Returns:
        单任务完整追踪信息。

    Raises:
        HTTPException: 没有匹配任务时抛出 404。
    """
    trace = await run_in_threadpool(service.job_trace, locator)
    if trace is None:
        raise HTTPException(status_code=404, detail="未找到对应任务")
    return trace


@router.get(
    "/circuits",
    response_model=List[CircuitStatusResponse],
    summary="查看第三方服务熔断状态",
)
async def get_circuit_statuses(
    _: UserRecord = Depends(require_admin_user),
) -> List[CircuitStatusResponse]:
    """
    返回地图与 LLM 提供方当前共享熔断状态。

    Args:
        _: 已通过管理员权限校验的用户。

    Returns:
        固定提供方集合的熔断状态。
    """
    breaker = get_circuit_breaker()
    now = time.time()
    result: List[CircuitStatusResponse] = []
    for provider in ("amap", "llm"):
        snapshot = await run_in_threadpool(breaker.snapshot, provider)
        result.append(
            CircuitStatusResponse(
                provider=provider,
                failures=snapshot.failures,
                is_open=snapshot.is_open,
                retry_after_seconds=(
                    max(0, int(snapshot.opened_until - now))
                    if snapshot.is_open
                    else 0
                ),
            )
        )
    return result


@router.get("/health", summary="检查运营依赖连通性")
async def get_dependency_health(
    _: UserRecord = Depends(require_admin_user),
) -> dict:
    """
    分别探测 SQL 和 Redis，帮助区分 API 存活与依赖就绪状态。

    Args:
        _: 已通过管理员权限校验的用户。

    Returns:
        数据库、Redis 和总体就绪状态。
    """
    snapshot = await run_in_threadpool(check_dependency_health)
    return snapshot.to_dict()
