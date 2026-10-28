"""账号注册、登录、会话恢复和退出接口。"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from starlette.concurrency import run_in_threadpool

from ...config import get_settings
from ...models.library_schemas import (
    AccountDeleteRequest,
    AccountDeleteResponse,
    AccountLoginRequest,
    AccountRegisterRequest,
    AccountSessionResponse,
    UserResponse,
)
from ...services.auth_service import (
    AccountConflictError,
    AuthenticationError,
    AuthService,
)
from ...services.platform_services import get_auth_service
from ..dependencies import (
    SESSION_COOKIE_NAME,
    RequestIdentity,
    get_request_identity,
    require_authenticated_user,
    require_captcha,
)
from ...persistence.models import UserRecord


router = APIRouter(prefix="/auth", tags=["账号与会话"])


def _set_session_cookie(response: Response, token: str) -> None:
    """
    把服务端会话令牌写入受保护 Cookie。

    Args:
        response: 当前 FastAPI 响应。
        token: 仅在签发时出现的原始会话令牌。
    """
    settings = get_settings()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.auth_session_ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post(
    "/register",
    response_model=AccountSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="注册账号并认领游客行程",
)
async def register_account(
    request: AccountRegisterRequest,
    response: Response,
    identity: RequestIdentity = Depends(get_request_identity),
    service: AuthService = Depends(get_auth_service),
    _captcha: None = Depends(require_captcha("register")),
) -> AccountSessionResponse:
    """
    创建账号、签发 Cookie 会话并迁移当前游客资产。

    Args:
        request: 邮箱、密码和显示名称。
        response: 用于设置 HttpOnly Cookie 的响应。
        identity: 当前请求中的游客标识。
        service: 认证应用服务。
        _captcha: 已通过或按配置关闭的人机验证结果。

    Returns:
        新用户公开信息和认领行程数量。
    """
    try:
        user, token, claimed_count = await run_in_threadpool(
            service.register,
            str(request.email),
            request.password,
            request.display_name,
            identity.guest_id,
        )
    except AccountConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _set_session_cookie(response, token)
    return AccountSessionResponse(
        authenticated=True,
        user=UserResponse.model_validate(user),
        claimed_trip_count=claimed_count,
    )


@router.post(
    "/login",
    response_model=AccountSessionResponse,
    summary="登录并认领游客行程",
)
async def login_account(
    request: AccountLoginRequest,
    response: Response,
    identity: RequestIdentity = Depends(get_request_identity),
    service: AuthService = Depends(get_auth_service),
) -> AccountSessionResponse:
    """
    校验账号密码、轮换会话并迁移当前游客资产。

    Args:
        request: 邮箱和密码。
        response: 用于设置 HttpOnly Cookie 的响应。
        identity: 当前请求中的游客标识。
        service: 认证应用服务。

    Returns:
        登录用户公开信息和认领行程数量。
    """
    try:
        user, token, claimed_count = await run_in_threadpool(
            service.login,
            str(request.email),
            request.password,
            identity.guest_id,
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _set_session_cookie(response, token)
    return AccountSessionResponse(
        authenticated=True,
        user=UserResponse.model_validate(user),
        claimed_trip_count=claimed_count,
    )


@router.get(
    "/me",
    response_model=AccountSessionResponse,
    summary="恢复当前登录会话",
)
async def get_current_account(
    identity: RequestIdentity = Depends(get_request_identity),
) -> AccountSessionResponse:
    """
    返回当前 Cookie 对应的公开用户，不创建新会话。

    Args:
        identity: 已解析的请求身份。

    Returns:
        已登录或游客会话状态。
    """
    return AccountSessionResponse(
        authenticated=identity.user is not None,
        user=(UserResponse.model_validate(identity.user) if identity.user else None),
    )


@router.post(
    "/logout",
    response_model=AccountSessionResponse,
    summary="退出当前账号",
)
async def logout_account(
    response: Response,
    identity: RequestIdentity = Depends(get_request_identity),
    service: AuthService = Depends(get_auth_service),
) -> AccountSessionResponse:
    """
    撤销服务端会话并删除浏览器 Cookie。

    Args:
        response: 用于删除会话 Cookie 的响应。
        identity: 当前会话令牌。
        service: 认证应用服务。

    Returns:
        未登录会话状态。
    """
    await run_in_threadpool(service.logout, identity.session_token)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return AccountSessionResponse(authenticated=False)


@router.delete(
    "/me",
    response_model=AccountDeleteResponse,
    summary="永久删除当前账号和关联数据",
)
async def delete_current_account(
    request: AccountDeleteRequest,
    response: Response,
    user: UserRecord = Depends(require_authenticated_user),
    service: AuthService = Depends(get_auth_service),
) -> AccountDeleteResponse:
    """
    再次校验密码后删除账号、行程、任务、调用记录和反馈。

    Args:
        request: 包含当前账号密码的确认请求。
        response: 用于清理浏览器会话 Cookie 的响应。
        user: 当前已认证用户。
        service: 认证应用服务。

    Returns:
        永久删除的数据数量。

    Raises:
        HTTPException: 密码不匹配时拒绝删除。
    """
    try:
        deleted = await run_in_threadpool(
            service.delete_account,
            user.id,
            request.password,
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return AccountDeleteResponse(deleted=True, **deleted)
