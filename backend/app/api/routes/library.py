"""用户行程库、版本、分享、反馈和日历接口。"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response, status
from starlette.concurrency import run_in_threadpool

from ...models.library_schemas import (
    EditTripRequest,
    FeedbackCreateRequest,
    FeedbackResponse,
    RestoreTripVersionRequest,
    SaveTripRequest,
    SavedTripResponse,
    ShareCreateRequest,
    ShareCreateResponse,
    SharedTripResponse,
    TripSummaryResponse,
    TripTitleUpdateRequest,
)
from ...services.calendar_service import build_trip_calendar
from ...services.platform_services import get_trip_library_service
from ...services.trip_library_service import (
    OwnerIdentity,
    TripAccessDeniedError,
    TripLibraryService,
    TripNotFoundError,
    TripVersionConflictError,
)
from ...services.trip_mutation_service import TripMutationError
from ..dependencies import get_request_identity, require_owner_identity


router = APIRouter(tags=["用户行程闭环"])


def _raise_library_http_error(exc: Exception) -> None:
    """
    将领域异常映射为稳定的 HTTP 状态码。

    Args:
        exc: 行程库或编辑服务抛出的异常。

    Raises:
        HTTPException: 与异常语义对应的 API 错误。
    """
    if isinstance(exc, TripNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, TripAccessDeniedError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, TripVersionConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, TripMutationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


@router.post(
    "/trips",
    response_model=SavedTripResponse,
    status_code=status.HTTP_201_CREATED,
    summary="保存客户端行程",
)
async def create_saved_trip(
    request: SaveTripRequest,
    identity: OwnerIdentity = Depends(require_owner_identity),
    service: TripLibraryService = Depends(get_trip_library_service),
) -> SavedTripResponse:
    """
    为登录用户或游客创建可跨设备恢复的行程资产。

    Args:
        request: 完整计划和可选标题。
        identity: 当前资产所有者。
        service: 行程库应用服务。

    Returns:
        新建行程和首个版本。
    """
    try:
        return await run_in_threadpool(
            service.create_trip,
            identity,
            request.plan,
            request.title,
        )
    except Exception as exc:
        _raise_library_http_error(exc)


@router.get(
    "/trips",
    response_model=List[TripSummaryResponse],
    summary="列出我的行程",
)
async def list_saved_trips(
    identity: OwnerIdentity = Depends(require_owner_identity),
    service: TripLibraryService = Depends(get_trip_library_service),
) -> List[TripSummaryResponse]:
    """
    返回当前用户或游客的轻量行程列表。

    Args:
        identity: 当前资产所有者。
        service: 行程库应用服务。

    Returns:
        按最近更新时间倒序的行程摘要。
    """
    return await run_in_threadpool(service.list_trips, identity)


@router.get(
    "/trips/{trip_id}",
    response_model=SavedTripResponse,
    summary="读取行程和版本历史",
)
async def get_saved_trip(
    trip_id: str,
    identity: OwnerIdentity = Depends(require_owner_identity),
    service: TripLibraryService = Depends(get_trip_library_service),
) -> SavedTripResponse:
    """
    读取当前计划、锁定景点和版本历史。

    Args:
        trip_id: 行程资产 ID。
        identity: 当前资产所有者。
        service: 行程库应用服务。

    Returns:
        当前完整行程。
    """
    try:
        return await run_in_threadpool(service.get_trip, identity, trip_id)
    except Exception as exc:
        _raise_library_http_error(exc)


@router.patch(
    "/trips/{trip_id}",
    response_model=SavedTripResponse,
    summary="编辑并重算行程",
)
async def edit_saved_trip(
    trip_id: str,
    request: EditTripRequest,
    identity: OwnerIdentity = Depends(require_owner_identity),
    service: TripLibraryService = Depends(get_trip_library_service),
) -> SavedTripResponse:
    """
    原子应用结构化编辑并追加不可变版本。

    Args:
        trip_id: 行程资产 ID。
        request: 基准版本、操作列表和变更说明。
        identity: 当前资产所有者。
        service: 行程库应用服务。

    Returns:
        路线、时间和预算已经重算的新版本。
    """
    try:
        return await run_in_threadpool(
            service.edit_trip,
            identity,
            trip_id,
            request.base_version,
            request.operations,
            request.change_summary,
        )
    except Exception as exc:
        _raise_library_http_error(exc)


@router.patch(
    "/trips/{trip_id}/title",
    response_model=SavedTripResponse,
    summary="重命名行程",
)
async def rename_saved_trip(
    trip_id: str,
    request: TripTitleUpdateRequest,
    identity: OwnerIdentity = Depends(require_owner_identity),
    service: TripLibraryService = Depends(get_trip_library_service),
) -> SavedTripResponse:
    """
    修改行程标题而不制造内容版本。

    Args:
        trip_id: 行程资产 ID。
        request: 已校验的新标题。
        identity: 当前资产所有者。
        service: 行程库应用服务。

    Returns:
        标题更新后的行程。
    """
    try:
        return await run_in_threadpool(
            service.rename_trip,
            identity,
            trip_id,
            request.title,
        )
    except Exception as exc:
        _raise_library_http_error(exc)


@router.post(
    "/trips/{trip_id}/restore",
    response_model=SavedTripResponse,
    summary="恢复历史版本",
)
async def restore_saved_trip(
    trip_id: str,
    request: RestoreTripVersionRequest,
    identity: OwnerIdentity = Depends(require_owner_identity),
    service: TripLibraryService = Depends(get_trip_library_service),
) -> SavedTripResponse:
    """
    把历史快照复制为新的当前版本。

    Args:
        trip_id: 行程资产 ID。
        request: 当前基准版本和待恢复版本。
        identity: 当前资产所有者。
        service: 行程库应用服务。

    Returns:
        新追加的恢复版本。
    """
    try:
        return await run_in_threadpool(
            service.restore_version,
            identity,
            trip_id,
            request.base_version,
            request.version_number,
        )
    except Exception as exc:
        _raise_library_http_error(exc)


@router.delete(
    "/trips/{trip_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除行程",
)
async def delete_saved_trip(
    trip_id: str,
    identity: OwnerIdentity = Depends(require_owner_identity),
    service: TripLibraryService = Depends(get_trip_library_service),
) -> Response:
    """
    软删除当前所有者的行程资产。

    Args:
        trip_id: 行程资产 ID。
        identity: 当前资产所有者。
        service: 行程库应用服务。

    Returns:
        无响应体的 204 响应。
    """
    try:
        await run_in_threadpool(service.delete_trip, identity, trip_id)
    except Exception as exc:
        _raise_library_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/trips/{trip_id}/shares",
    response_model=ShareCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建只读分享",
)
async def create_trip_share(
    trip_id: str,
    request: ShareCreateRequest,
    identity: OwnerIdentity = Depends(require_owner_identity),
    service: TripLibraryService = Depends(get_trip_library_service),
) -> ShareCreateResponse:
    """
    为指定或当前版本创建可过期、可撤销的只读令牌。

    Args:
        trip_id: 行程资产 ID。
        request: 版本号和有效天数。
        identity: 当前资产所有者。
        service: 行程库应用服务。

    Returns:
        只出现一次的原始分享令牌。
    """
    try:
        return await run_in_threadpool(
            service.create_share,
            identity,
            trip_id,
            request.version_number,
            request.expires_in_days,
        )
    except Exception as exc:
        _raise_library_http_error(exc)


@router.delete(
    "/trips/{trip_id}/shares/{share_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="撤销只读分享",
)
async def revoke_trip_share(
    trip_id: str,
    share_id: str,
    identity: OwnerIdentity = Depends(require_owner_identity),
    service: TripLibraryService = Depends(get_trip_library_service),
) -> Response:
    """
    立即停用一条分享链接。

    Args:
        trip_id: 行程资产 ID。
        share_id: 分享记录 ID。
        identity: 当前资产所有者。
        service: 行程库应用服务。

    Returns:
        无响应体的 204 响应。
    """
    try:
        await run_in_threadpool(service.revoke_share, identity, trip_id, share_id)
    except Exception as exc:
        _raise_library_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/shares/{token}",
    response_model=SharedTripResponse,
    summary="读取只读分享",
)
async def get_trip_share(
    token: str,
    service: TripLibraryService = Depends(get_trip_library_service),
) -> SharedTripResponse:
    """
    无需登录读取分享令牌绑定的固定版本。

    Args:
        token: URL 中的原始分享令牌。
        service: 行程库应用服务。

    Returns:
        不暴露所有者信息的行程快照。
    """
    try:
        return await run_in_threadpool(service.get_shared_trip, token)
    except Exception as exc:
        _raise_library_http_error(exc)


@router.post(
    "/shares/{token}/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="提交分享反馈",
)
async def create_trip_feedback(
    token: str,
    request: FeedbackCreateRequest,
    identity=Depends(get_request_identity),
    service: TripLibraryService = Depends(get_trip_library_service),
) -> FeedbackResponse:
    """
    对仍然有效的分享行程提交评分和建议。

    Args:
        token: URL 中的原始分享令牌。
        request: 星级和可选评论。
        identity: 可选用户或游客身份。
        service: 行程库应用服务。

    Returns:
        已持久化反馈的公开确认字段。
    """
    try:
        return await run_in_threadpool(
            service.add_feedback,
            token,
            identity.owner,
            request.rating,
            request.comment,
        )
    except Exception as exc:
        _raise_library_http_error(exc)


@router.get(
    "/trips/{trip_id}/calendar.ics",
    summary="导出行程日历",
)
async def export_trip_calendar(
    trip_id: str,
    identity: OwnerIdentity = Depends(require_owner_identity),
    service: TripLibraryService = Depends(get_trip_library_service),
) -> Response:
    """
    导出当前版本时间轴为 iCalendar 文件。

    Args:
        trip_id: 行程资产 ID。
        identity: 当前资产所有者。
        service: 行程库应用服务。

    Returns:
        可下载并导入系统日历的 ICS 响应。
    """
    try:
        saved = await run_in_threadpool(service.get_trip, identity, trip_id)
    except Exception as exc:
        _raise_library_http_error(exc)
    content = build_trip_calendar(saved.plan, saved.id)
    filename = f"trip-{saved.id}.ics"
    return Response(
        content=content,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
