"""无需登录即可读取的隐私和第三方数据来源说明。"""

from fastapi import APIRouter

from ...config import get_settings
from ...models.operations_schemas import (
    PrivacyInfoResponse,
    PublicSecurityConfigResponse,
)


router = APIRouter(prefix="/compliance", tags=["隐私与数据来源"])


@router.get(
    "/privacy",
    response_model=PrivacyInfoResponse,
    summary="查看数据处理和第三方提供方说明",
)
async def get_privacy_information() -> PrivacyInfoResponse:
    """
    返回与当前实现一致的收集范围、保留周期和用户控制项。

    Returns:
        前端隐私页可直接展示的结构化说明。
    """
    settings = get_settings()
    return PrivacyInfoResponse(
        contact_email=settings.privacy_contact_email or None,
        retention_days=settings.data_retention_days,
        collected_data=[
            "账号邮箱、显示名称和不可逆密码摘要",
            "旅行需求、行程版本、任务状态和用户反馈",
            "请求关联 ID、调用耗时、错误分类和已知费用",
        ],
        external_providers=[
            {
                "name": "高德地图",
                "purpose": "POI、天气、地理编码、路线和部分地点图片",
            },
            {
                "name": "Wikimedia Commons",
                "purpose": "地点图片及其作者、许可和来源链接",
            },
            {
                "name": "Unsplash",
                "purpose": "Commons 无结果时的地点图片降级来源",
            },
            {
                "name": "配置的 LLM 提供方",
                "purpose": "对真实候选进行控制决策和行程编排",
            },
        ],
        user_controls=[
            "撤销只读分享链接",
            "删除单个行程及其版本",
            "再次验证密码后永久删除账号和关联数据",
        ],
    )


@router.get(
    "/security-config",
    response_model=PublicSecurityConfigResponse,
    summary="读取前端可公开的安全配置",
)
async def get_public_security_config() -> PublicSecurityConfigResponse:
    """
    仅返回 Turnstile 是否启用和公开站点 Key，不暴露 Secret。

    Returns:
        前端按需加载验证码小组件所需配置。
    """
    settings = get_settings()
    return PublicSecurityConfigResponse(
        turnstile_enabled=settings.turnstile_enabled,
        turnstile_site_key=(
            settings.turnstile_site_key
            if settings.turnstile_enabled
            else None
        ),
    )
