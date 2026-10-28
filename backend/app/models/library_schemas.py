"""账号、行程版本、分享与反馈 API 的 Pydantic 契约。"""

from datetime import datetime
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .schemas import TripPlan


class AccountRegisterRequest(BaseModel):
    """注册并认领当前游客行程的请求。"""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=80)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        """
        去除显示名称两端空白并拒绝空内容。

        Args:
            value: 用户提交的显示名称。

        Returns:
            规范化后的显示名称。
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("display_name不能为空")
        return normalized


class AccountLoginRequest(BaseModel):
    """账号密码登录请求。"""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class AccountDeleteRequest(BaseModel):
    """要求再次校验密码的永久账号删除请求。"""

    password: str = Field(..., min_length=8, max_length=128)


class AccountDeleteResponse(BaseModel):
    """账号及关联数据永久删除结果。"""

    deleted: bool
    trips: int = Field(..., ge=0)
    jobs: int = Field(..., ge=0)
    feedback: int = Field(..., ge=0)


class UserResponse(BaseModel):
    """不包含密码和内部会话信息的公开用户。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    display_name: str
    is_admin: bool = False
    created_at: datetime


class AccountSessionResponse(BaseModel):
    """注册、登录和会话恢复使用的统一响应。"""

    authenticated: bool
    user: Optional[UserResponse] = None
    claimed_trip_count: int = Field(default=0, ge=0)


class SaveTripRequest(BaseModel):
    """把客户端现有行程保存为服务器资产的请求。"""

    plan: TripPlan
    title: Optional[str] = Field(default=None, max_length=160)


class TripTitleUpdateRequest(BaseModel):
    """修改行程标题的请求。"""

    title: str = Field(..., min_length=1, max_length=160)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        """
        去除标题两端空白并拒绝空内容。

        Args:
            value: 用户提交的行程标题。

        Returns:
            规范化后的标题。
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("title不能为空")
        return normalized


class TripSummaryResponse(BaseModel):
    """我的行程列表中的轻量摘要。"""

    id: str
    title: str
    city: str
    start_date: str
    end_date: str
    current_version: int
    created_at: datetime
    updated_at: datetime


class TripVersionResponse(BaseModel):
    """一个不可变行程版本的元数据和可选完整快照。"""

    version_number: int
    change_type: str
    change_summary: str
    created_at: datetime
    plan: Optional[TripPlan] = None


class SavedTripResponse(BaseModel):
    """行程资产、当前快照和版本历史。"""

    id: str
    title: str
    city: str
    current_version: int
    locked_poi_ids: List[str] = Field(default_factory=list)
    plan: TripPlan
    versions: List[TripVersionResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MoveAttractionOperation(BaseModel):
    """把景点移动到指定日期和位置。"""

    type: Literal["move"]
    poi_id: str = Field(..., min_length=1)
    target_day_index: int = Field(..., ge=0)
    target_position: int = Field(..., ge=0)


class DeleteAttractionOperation(BaseModel):
    """从行程中删除一个未锁定景点。"""

    type: Literal["delete"]
    poi_id: str = Field(..., min_length=1)


class UpdateAttractionOperation(BaseModel):
    """修改景点的用户可编辑字段。"""

    type: Literal["update"]
    poi_id: str = Field(..., min_length=1)
    visit_duration: Optional[int] = Field(default=None, ge=30, le=720)
    description: Optional[str] = Field(default=None, max_length=1000)


class LockAttractionOperation(BaseModel):
    """锁定或解锁一个景点。"""

    type: Literal["lock"]
    poi_id: str = Field(..., min_length=1)
    locked: bool


class ReplaceAttractionOperation(BaseModel):
    """用服务端重新验证的真实 POI 替换一个景点。"""

    type: Literal["replace"]
    poi_id: str = Field(..., min_length=1)
    replacement_poi_id: str = Field(..., min_length=1)
    replacement_keyword: str = Field(..., min_length=1, max_length=100)


TripEditOperation = Annotated[
    Union[
        MoveAttractionOperation,
        DeleteAttractionOperation,
        UpdateAttractionOperation,
        LockAttractionOperation,
        ReplaceAttractionOperation,
    ],
    Field(discriminator="type"),
]


class EditTripRequest(BaseModel):
    """基于明确版本号提交的一组原子行程修改。"""

    base_version: int = Field(..., ge=1)
    operations: List[TripEditOperation] = Field(..., min_length=1, max_length=50)
    change_summary: str = Field(default="用户编辑行程", max_length=300)


class RestoreTripVersionRequest(BaseModel):
    """把历史版本复制为新当前版本的请求。"""

    base_version: int = Field(..., ge=1)
    version_number: int = Field(..., ge=1)


class ShareCreateRequest(BaseModel):
    """创建固定版本只读分享链接的请求。"""

    version_number: Optional[int] = Field(default=None, ge=1)
    expires_in_days: Optional[int] = Field(default=30, ge=1, le=365)


class ShareCreateResponse(BaseModel):
    """只在创建时返回原始分享令牌的响应。"""

    share_id: str
    share_token: str
    share_path: str
    trip_id: str
    version_number: int
    expires_at: Optional[datetime] = None


class SharedTripResponse(BaseModel):
    """无需登录即可读取的固定版本行程。"""

    trip_id: str
    title: str
    version_number: int
    plan: TripPlan
    created_at: datetime


class FeedbackCreateRequest(BaseModel):
    """针对分享行程提交评分和可选建议。"""

    rating: int = Field(..., ge=1, le=5)
    comment: str = Field(default="", max_length=2000)


class FeedbackResponse(BaseModel):
    """反馈落库后的公开确认信息。"""

    id: str
    rating: int
    comment: str
    created_at: datetime
