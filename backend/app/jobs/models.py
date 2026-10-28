"""旅行计划异步任务的数据模型。"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from ..models.schemas import TripPlan, TripRequest
from ..observability import get_request_id


class TripJobStatus(str, Enum):
    """旅行计划任务的生命周期状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TripJobStage(str, Enum):
    """旅行计划生成过程中的可观测业务阶段。"""

    QUEUED = "queued"
    LOADING_GROUNDING_DATA = "loading_grounding_data"
    RUNNING_CONTROL_AGENTS = "running_control_agents"
    RUNNING_PLANNER = "running_planner"
    VALIDATING_PLAN = "validating_plan"
    GROUNDING_PLAN = "grounding_plan"
    FILLING_SCHEDULE = "filling_schedule"
    ENRICHING_WEATHER = "enriching_weather"
    ENRICHING_ROUTES = "enriching_routes"
    OPTIMIZING_ITINERARY = "optimizing_itinerary"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_JOB_STATUSES = frozenset(
    {
        TripJobStatus.CANCELLED,
        TripJobStatus.SUCCEEDED,
        TripJobStatus.FAILED,
    }
)


class TripJobRecord(BaseModel):
    """持久化在任务仓储中的完整旅行计划任务记录。"""

    job_id: str = Field(..., description="公开任务ID")
    request_id: Optional[str] = Field(default=None, description="入口请求关联ID")
    task_id: Optional[str] = Field(default=None, description="队列内部任务ID")
    status: TripJobStatus = Field(..., description="任务状态")
    stage: TripJobStage = Field(..., description="当前业务阶段")
    progress: int = Field(..., ge=0, le=100, description="真实阶段进度")
    message: str = Field(default="", description="面向用户的阶段说明")
    owner_user_id: Optional[str] = Field(default=None, description="登录用户所有者ID")
    guest_id: Optional[str] = Field(default=None, description="游客所有者ID")
    request: TripRequest = Field(..., description="原始旅行规划请求")
    result: Optional[TripPlan] = Field(default=None, description="生成结果")
    saved_trip_id: Optional[str] = Field(default=None, description="归档后的行程资产ID")
    error_code: Optional[str] = Field(default=None, description="稳定错误码")
    error_message: Optional[str] = Field(default=None, description="错误摘要")
    attempt_count: int = Field(default=0, ge=0, description="已领取的执行次数")
    max_attempts: int = Field(default=2, ge=1, le=5, description="最大执行次数")
    attempt_started_at: Optional[datetime] = Field(
        default=None,
        description="当前执行尝试的开始时间",
    )
    finished_at: Optional[datetime] = Field(default=None, description="任务终态时间")
    created_at: datetime = Field(..., description="任务创建时间")
    updated_at: datetime = Field(..., description="任务更新时间")
    revision: int = Field(default=0, ge=0, description="状态修订号")

    @classmethod
    def create(
        cls,
        request: TripRequest,
        max_attempts: int = 2,
        owner_user_id: Optional[str] = None,
        guest_id: Optional[str] = None,
    ) -> "TripJobRecord":
        """
        为一次旅行规划请求创建初始排队记录。

        Args:
            request: 已通过接口校验的旅行规划请求。
            max_attempts: 包含首次执行在内的最大执行次数。
            owner_user_id: 可选登录用户所有者 ID。
            guest_id: 未登录时的游客所有者 ID。

        Returns:
            具有随机任务ID和统一UTC时间的初始记录。
        """
        now = datetime.now(timezone.utc)
        job_id = uuid4().hex
        return cls(
            job_id=job_id,
            request_id=get_request_id() or None,
            task_id=f"trip-{job_id}",
            status=TripJobStatus.QUEUED,
            stage=TripJobStage.QUEUED,
            progress=0,
            message="旅行计划任务已进入队列",
            owner_user_id=owner_user_id,
            guest_id=None if owner_user_id else guest_id,
            request=request,
            max_attempts=max_attempts,
            created_at=now,
            updated_at=now,
        )

    def is_terminal(self) -> bool:
        """
        判断任务是否已经进入不可继续执行的终态。

        Returns:
            任务成功、失败或取消时返回 True。
        """
        return self.status in TERMINAL_JOB_STATUSES


class TripJobSubmissionResponse(BaseModel):
    """提交异步旅行计划任务后的轻量响应。"""

    job_id: str = Field(..., description="任务ID")
    status: TripJobStatus = Field(..., description="任务状态")
    stage: TripJobStage = Field(..., description="当前业务阶段")
    progress: int = Field(..., ge=0, le=100, description="当前进度")
    message: str = Field(..., description="提交结果说明")
    created: bool = Field(..., description="是否新建任务")
    attempt_count: int = Field(..., ge=0, description="已领取的执行次数")
    max_attempts: int = Field(..., ge=1, description="最大执行次数")
    saved_trip_id: Optional[str] = Field(default=None, description="已有归档行程ID")

    @classmethod
    def from_record(
        cls,
        record: TripJobRecord,
        created: bool,
    ) -> "TripJobSubmissionResponse":
        """
        从内部任务记录构造对外提交响应。

        Args:
            record: 已持久化的任务记录。
            created: 本次请求是否实际创建了新任务。

        Returns:
            不包含内部请求参数和队列ID的提交响应。
        """
        return cls(
            job_id=record.job_id,
            status=record.status,
            stage=record.stage,
            progress=record.progress,
            message=record.message,
            created=created,
            attempt_count=record.attempt_count,
            max_attempts=record.max_attempts,
            saved_trip_id=record.saved_trip_id,
        )


class TripJobStatusResponse(BaseModel):
    """查询或订阅旅行计划任务时返回的公开状态。"""

    job_id: str = Field(..., description="任务ID")
    status: TripJobStatus = Field(..., description="任务状态")
    stage: TripJobStage = Field(..., description="当前业务阶段")
    progress: int = Field(..., ge=0, le=100, description="当前进度")
    message: str = Field(..., description="当前阶段说明")
    result: Optional[TripPlan] = Field(default=None, description="成功后的旅行计划")
    saved_trip_id: Optional[str] = Field(default=None, description="成功归档后的行程ID")
    error_code: Optional[str] = Field(default=None, description="失败错误码")
    error_message: Optional[str] = Field(default=None, description="失败原因摘要")
    attempt_count: int = Field(..., ge=0, description="已领取的执行次数")
    max_attempts: int = Field(..., ge=1, description="最大执行次数")
    attempt_started_at: Optional[datetime] = Field(
        default=None,
        description="当前执行尝试的开始时间",
    )
    finished_at: Optional[datetime] = Field(default=None, description="任务终态时间")
    created_at: datetime = Field(..., description="任务创建时间")
    updated_at: datetime = Field(..., description="任务更新时间")
    revision: int = Field(..., ge=0, description="状态修订号")

    @classmethod
    def from_record(cls, record: TripJobRecord) -> "TripJobStatusResponse":
        """
        从内部记录构造不泄露原始请求和队列ID的公开状态。

        Args:
            record: 当前任务记录。

        Returns:
            可供轮询接口和SSE事件复用的状态响应。
        """
        return cls(
            job_id=record.job_id,
            status=record.status,
            stage=record.stage,
            progress=record.progress,
            message=record.message,
            result=record.result,
            saved_trip_id=record.saved_trip_id,
            error_code=record.error_code,
            error_message=record.error_message,
            attempt_count=record.attempt_count,
            max_attempts=record.max_attempts,
            attempt_started_at=record.attempt_started_at,
            finished_at=record.finished_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
            revision=record.revision,
        )
