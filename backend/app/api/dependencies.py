"""FastAPI 身份解析和用户闭环公共依赖。"""

import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from ..persistence.models import UserRecord
from ..services.auth_service import AuthService
from ..services.captcha_service import (
    CaptchaService,
    CaptchaVerificationError,
    get_captcha_service,
)
from ..services.platform_services import get_auth_service
from ..services.trip_library_service import OwnerIdentity


SESSION_COOKIE_NAME = "trip_session"
GUEST_HEADER_NAME = "X-Guest-ID"
_GUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,80}$")


@dataclass(frozen=True)
class RequestIdentity:
    """一次 HTTP 请求解析出的会话用户和资产所有者。"""

    owner: OwnerIdentity
    user: Optional[UserRecord]
    session_token: Optional[str]
    guest_id: Optional[str]


def validate_guest_id(guest_id: Optional[str]) -> Optional[str]:
    """
    校验浏览器生成的游客标识，阻止任意长字符串进入数据库。

    Args:
        guest_id: X-Guest-ID 请求头中的可选值。

    Returns:
        合法游客标识或 None。

    Raises:
        HTTPException: 游客标识格式不合法时抛出。
    """
    if guest_id is None:
        return None
    normalized = guest_id.strip()
    if not _GUEST_ID_PATTERN.fullmatch(normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Guest-ID格式不正确",
        )
    return normalized


def get_request_identity(
    session_token: Optional[str] = Cookie(
        default=None,
        alias=SESSION_COOKIE_NAME,
    ),
    guest_id: Optional[str] = Header(default=None, alias=GUEST_HEADER_NAME),
    auth_service: AuthService = Depends(get_auth_service),
) -> RequestIdentity:
    """
    优先解析登录会话，并保留未登录游客身份作为资产归属。

    Args:
        session_token: HttpOnly Cookie 中的原始会话令牌。
        guest_id: 浏览器本地生成的游客标识。
        auth_service: 认证应用服务。

    Returns:
        可供账号和行程路由复用的请求身份。
    """
    normalized_guest_id = validate_guest_id(guest_id)
    user = auth_service.resolve_user(session_token)
    owner = OwnerIdentity(
        user_id=user.id if user else None,
        guest_id=None if user else normalized_guest_id,
    )
    return RequestIdentity(
        owner=owner,
        user=user,
        session_token=session_token,
        guest_id=normalized_guest_id,
    )


def require_owner_identity(
    identity: RequestIdentity = Depends(get_request_identity),
) -> OwnerIdentity:
    """
    要求请求具有登录用户或游客资产身份。

    Args:
        identity: 已解析的请求身份。

    Returns:
        可用于行程所有权校验的身份。

    Raises:
        HTTPException: 请求既没有会话也没有游客标识时抛出。
    """
    if not identity.owner.is_available():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少登录会话或游客身份",
        )
    return identity.owner


def require_authenticated_user(
    identity: RequestIdentity = Depends(get_request_identity),
) -> UserRecord:
    """
    要求请求来自有效的登录会话。

    Args:
        identity: 已解析的请求身份。

    Returns:
        当前登录用户记录。

    Raises:
        HTTPException: 会话缺失或已经失效时抛出。
    """
    if identity.user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录账号",
        )
    return identity.user


def require_admin_user(
    user: UserRecord = Depends(require_authenticated_user),
) -> UserRecord:
    """
    要求当前用户具有数据库中明确授予的管理员角色。

    Args:
        user: 当前已认证用户。

    Returns:
        管理员用户记录。

    Raises:
        HTTPException: 普通用户访问运营接口时抛出。
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user


def require_captcha(
    action: str,
) -> Callable[..., Awaitable[None]]:
    """
    创建绑定稳定 action 的 FastAPI Turnstile 校验依赖。

    Args:
        action: 前端小组件和后端共同约定的表单操作名。

    Returns:
        默认关闭、启用后执行服务端 Siteverify 的异步依赖。
    """
    async def verify_captcha(
        request: Request,
        token: Optional[str] = Header(default=None, alias="X-Captcha-Token"),
        service: CaptchaService = Depends(get_captcha_service),
    ) -> None:
        """
        校验当前请求的验证码 Token。

        Args:
            request: 当前 HTTP 请求，用于提取客户端 IP。
            token: 前端通过专用请求头发送的短期 Token。
            service: Turnstile 校验服务。

        Raises:
            HTTPException: 验证码无效、过期或提供方不可用时抛出。
        """
        remote_ip = request.client.host if request.client else None
        try:
            await run_in_threadpool(
                service.verify,
                token,
                remote_ip,
                action,
            )
        except CaptchaVerificationError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc

    return verify_captcha
