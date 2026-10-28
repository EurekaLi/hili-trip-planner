"""旅行规划同步兼容接口与异步任务接口。"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from ...config import get_settings
from ...models.schemas import (
    TripRequest,
    TripPlanResponse,
)
from ...agents.trip_planner_agent import get_trip_planner_agent
from ...jobs.models import TripJobRecord, TripJobStatusResponse, TripJobSubmissionResponse
from ...jobs.repository import TripJobNotFoundError
from ...jobs.service import (
    TripJobIdempotencyConflictError,
    TripJobQueueUnavailableError,
    TripJobService,
    get_trip_job_service,
)
from ...services.trip_library_service import OwnerIdentity
from ..dependencies import (
    RequestIdentity,
    get_request_identity,
    require_owner_identity,
    require_captcha,
    validate_guest_id,
)

router = APIRouter(prefix="/trip", tags=["旅行规划"])


def _assert_job_access(
    record: TripJobRecord,
    identity: RequestIdentity,
    guest_id_override: Optional[str] = None,
) -> None:
    """
    校验任务属于当前登录用户或游客，并兼容升级前无所有者任务。

    Args:
        record: Redis 中的任务记录。
        identity: 当前 Cookie 和请求头解析出的身份。
        guest_id_override: SSE 无法发送自定义请求头时使用的游客 ID。

    Raises:
        HTTPException: 当前身份不能读取任务时抛出 404。
    """
    if record.owner_user_id and identity.user:
        if record.owner_user_id == identity.user.id:
            return
    elif record.guest_id:
        if record.guest_id in {identity.guest_id, guest_id_override}:
            return
    elif not record.owner_user_id:
        return
    raise HTTPException(status_code=404, detail="旅行计划任务不存在")


@router.post(
    "/plan",
    response_model=TripPlanResponse,
    summary="生成旅行计划",
    description="根据用户输入的旅行需求,生成详细的旅行计划"
)
async def plan_trip(
    request: TripRequest,
    _captcha: None = Depends(require_captcha("trip_generation")),
):
    """
    生成旅行计划

    Args:
        request: 旅行请求参数
        _captcha: 已通过或按配置关闭的人机验证结果。

    Returns:
        旅行计划响应
    """
    try:
        print(f"\n{'='*60}")
        print(f"📥 收到旅行规划请求:")
        print(f"   城市: {request.city}")
        print(f"   日期: {request.start_date} - {request.end_date}")
        print(f"   天数: {request.travel_days}")
        print(f"{'='*60}\n")

        # 获取Agent实例
        print("🔄 获取多智能体系统实例...")
        agent = get_trip_planner_agent()

        # 生成旅行计划
        print("🚀 开始生成旅行计划...")
        trip_plan = await run_in_threadpool(agent.plan_trip, request)

        print("✅ 旅行计划生成成功,准备返回响应\n")

        message = (
            "旅行计划生成成功，但部分路线暂不可用"
            if trip_plan.warnings
            else "旅行计划生成成功"
        )

        return TripPlanResponse(
            success=True,
            message=message,
            data=trip_plan,
        )

    except Exception as e:
        print(f"❌ 生成旅行计划失败: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"生成旅行计划失败: {str(e)}"
        )


@router.post(
    "/jobs",
    response_model=TripJobSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="提交异步旅行计划任务",
    description="创建可查询、可订阅进度并可取消的旅行计划任务",
)
async def create_trip_job(
    request: TripRequest,
    idempotency_key: Optional[str] = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
    ),
    service: TripJobService = Depends(get_trip_job_service),
    identity: OwnerIdentity = Depends(require_owner_identity),
    _captcha: None = Depends(require_captcha("trip_generation")),
) -> TripJobSubmissionResponse:
    """
    创建异步旅行计划任务并立即返回任务ID。

    Args:
        request: 已通过 Pydantic 校验的旅行规划请求。
        idempotency_key: 防止网络重试产生重复付费任务的可选幂等键。
        service: 由 FastAPI 注入的任务应用服务。
        identity: 当前登录用户或游客所有者。
        _captcha: 已通过或按配置关闭的人机验证结果。

    Returns:
        新任务或同幂等请求已有任务的提交状态。

    Raises:
        HTTPException: 幂等冲突或 Redis、Celery 不可用时抛出。
    """
    try:
        record, created = await run_in_threadpool(
            service.submit,
            request,
            idempotency_key,
            identity,
        )
        return TripJobSubmissionResponse.from_record(record, created)
    except TripJobIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TripJobQueueUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="异步任务服务暂时不可用",
        ) from exc


@router.get(
    "/jobs/{job_id}",
    response_model=TripJobStatusResponse,
    summary="查询旅行计划任务",
    description="获取任务真实进度、失败原因或最终旅行计划",
)
async def get_trip_job(
    job_id: str,
    service: TripJobService = Depends(get_trip_job_service),
    identity: RequestIdentity = Depends(get_request_identity),
) -> TripJobStatusResponse:
    """
    查询旅行计划任务的最新公开状态。

    Args:
        job_id: 提交接口返回的公开任务ID。
        service: 由 FastAPI 注入的任务应用服务。
        identity: 当前登录用户或游客身份。

    Returns:
        不包含原始请求和队列内部ID的任务状态。

    Raises:
        HTTPException: 任务不存在或任务仓储不可用时抛出。
    """
    try:
        record = await run_in_threadpool(service.get, job_id)
        _assert_job_access(record, identity)
        return TripJobStatusResponse.from_record(record)
    except TripJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="旅行计划任务不存在") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="任务状态服务暂时不可用",
        ) from exc


@router.delete(
    "/jobs/{job_id}",
    response_model=TripJobStatusResponse,
    summary="取消旅行计划任务",
    description="取消排队任务，或请求运行中的任务在阶段边界停止",
)
async def cancel_trip_job(
    job_id: str,
    service: TripJobService = Depends(get_trip_job_service),
    identity: RequestIdentity = Depends(get_request_identity),
) -> TripJobStatusResponse:
    """
    取消指定旅行计划任务。

    Args:
        job_id: 提交接口返回的公开任务ID。
        service: 由 FastAPI 注入的任务应用服务。
        identity: 当前登录用户或游客身份。

    Returns:
        取消后的任务状态；已完成任务保持原状。

    Raises:
        HTTPException: 任务不存在或队列、仓储不可用时抛出。
    """
    try:
        existing = await run_in_threadpool(service.get, job_id)
        _assert_job_access(existing, identity)
        record = await run_in_threadpool(service.cancel, job_id)
        return TripJobStatusResponse.from_record(record)
    except TripJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="旅行计划任务不存在") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="任务取消服务暂时不可用",
        ) from exc


@router.get(
    "/jobs/{job_id}/events",
    summary="订阅旅行计划任务进度",
    description="通过 Server-Sent Events 推送发生变化的真实任务阶段",
)
async def stream_trip_job_events(
    job_id: str,
    request: Request,
    guest_id: Optional[str] = Query(default=None, max_length=80),
    service: TripJobService = Depends(get_trip_job_service),
    identity: RequestIdentity = Depends(get_request_identity),
) -> EventSourceResponse:
    """
    建立旅行计划任务的 SSE 状态流。

    Args:
        job_id: 提交接口返回的公开任务ID。
        request: 用于检测浏览器是否已经断开连接的 HTTP 请求。
        guest_id: EventSource 无法设置请求头时使用的游客身份。
        service: 由 FastAPI 注入的任务应用服务。
        identity: 当前登录用户或游客身份。

    Returns:
        仅在修订号变化时发送事件、终态后自动结束的 SSE 响应。

    Raises:
        HTTPException: 建立流之前任务不存在或仓储不可用时抛出。
    """
    try:
        initial_record = await run_in_threadpool(service.get, job_id)
        _assert_job_access(
            initial_record,
            identity,
            validate_guest_id(guest_id),
        )
    except TripJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="旅行计划任务不存在") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="任务状态服务暂时不可用",
        ) from exc

    settings = get_settings()

    async def event_stream():
        """
        轮询 Redis 修订号并将变化后的公开状态编码为 SSE 事件。

        Yields:
            符合 sse-starlette 格式的事件字典。
        """
        record = initial_record
        last_revision = -1
        while not await request.is_disconnected():
            if record.revision != last_revision:
                public_status = TripJobStatusResponse.from_record(record)
                yield {
                    "event": "trip-job",
                    "id": str(record.revision),
                    "retry": 1000,
                    "data": public_status.model_dump_json(),
                }
                last_revision = record.revision

            if record.is_terminal():
                break

            await asyncio.sleep(settings.trip_job_event_poll_seconds)
            record = await run_in_threadpool(service.get, job_id)

    return EventSourceResponse(event_stream(), ping=15)


@router.get(
    "/health",
    summary="健康检查",
    description="检查旅行规划服务是否正常"
)
async def health_check():
    """健康检查"""
    try:
        # 检查Agent是否可用
        agent = get_trip_planner_agent()

        return {
            "status": "healthy",
            "service": "trip-planner",
            "planner_agent": agent.planner_agent.name,
            "control_agents": [
                agent.attraction_agent.name,
                agent.weather_agent.name,
                agent.hotel_agent.name,
            ],
            "agent_count": 4,
        }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"服务不可用: {str(e)}"
        )

