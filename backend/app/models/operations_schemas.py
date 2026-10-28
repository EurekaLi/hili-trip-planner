"""运营看板、任务追踪和公开合规信息的 API 契约。"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class StatusCount(BaseModel):
    """一个稳定状态或错误分类的出现次数。"""

    name: str
    count: int = Field(..., ge=0)


class ProviderOperationMetrics(BaseModel):
    """按提供方和操作聚合的调用质量与费用。"""

    provider: str
    operation: str
    calls: int = Field(..., ge=0)
    success_rate: float = Field(..., ge=0, le=1)
    average_latency_seconds: float = Field(..., ge=0)
    p95_latency_seconds: float = Field(..., ge=0)
    known_cost_cny: float = Field(..., ge=0)


class RecentFailure(BaseModel):
    """运营列表使用的脱敏失败任务摘要。"""

    job_id: str
    request_id: Optional[str] = None
    city: str
    error_code: str
    error_message: str
    created_at: datetime
    finished_at: Optional[datetime] = None


class OperationsOverviewResponse(BaseModel):
    """指定时间窗口的任务、调用、缓存和反馈总览。"""

    generated_at: datetime
    window_hours: int = Field(..., ge=1)
    jobs_total: int = Field(..., ge=0)
    success_rate: float = Field(..., ge=0, le=1)
    p50_job_seconds: float = Field(..., ge=0)
    p95_job_seconds: float = Field(..., ge=0)
    known_cost_cny: float = Field(..., ge=0)
    status_counts: List[StatusCount]
    failure_counts: List[StatusCount]
    provider_metrics: List[ProviderOperationMetrics]
    recent_failures: List[RecentFailure]
    feedback_count: int = Field(..., ge=0)
    average_rating: Optional[float] = Field(default=None, ge=1, le=5)
    image_cache_hits: int = Field(..., ge=0)
    image_cache_misses: int = Field(..., ge=0)
    image_cache_hit_rate: Optional[float] = Field(default=None, ge=0, le=1)


class ExternalCallTraceResponse(BaseModel):
    """单任务追踪中的一次第三方调用。"""

    model_config = ConfigDict(from_attributes=True)

    provider: str
    operation: str
    succeeded: bool
    elapsed_seconds: float = Field(..., ge=0)
    units: int = Field(..., ge=1)
    cost_cny: Optional[float] = Field(default=None, ge=0)
    error_type: Optional[str] = None
    requested_at: datetime


class JobTraceResponse(BaseModel):
    """通过 job_id 或 request_id 定位一次生成请求的完整审计。"""

    job_id: str
    request_id: Optional[str] = None
    trip_id: Optional[str] = None
    status: str
    city: str
    request: Dict[str, Any]
    budget: Dict[str, Any]
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    finished_at: Optional[datetime] = None
    external_calls: List[ExternalCallTraceResponse]
    agent_trace: List[Dict[str, Any]]


class CircuitStatusResponse(BaseModel):
    """运营页面展示的提供方熔断状态。"""

    provider: str
    failures: int = Field(..., ge=0)
    is_open: bool
    retry_after_seconds: int = Field(..., ge=0)


class PrivacyInfoResponse(BaseModel):
    """前端隐私页展示的稳定数据处理说明。"""

    contact_email: Optional[str] = None
    retention_days: int = Field(..., ge=1)
    collected_data: List[str]
    external_providers: List[Dict[str, str]]
    user_controls: List[str]


class PublicSecurityConfigResponse(BaseModel):
    """前端渲染验证码所需且允许公开的安全配置。"""

    turnstile_enabled: bool
    turnstile_site_key: Optional[str] = None
