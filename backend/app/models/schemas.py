"""旅行规划 API、事实来源和生成轨迹的数据模型。"""

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


# ============ 请求模型 ============

class TripRequest(BaseModel):
    """经过日期跨度校验的旅行规划请求。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "city": "北京",
                "start_date": "2026-09-15",
                "end_date": "2026-09-17",
                "travel_days": 3,
                "transportation": "公共交通",
                "accommodation": "经济型酒店",
                "preferences": ["历史文化", "美食"],
                "free_text_input": "希望多安排一些博物馆"
            }
        },
    )

    city: str = Field(..., min_length=1, max_length=50, description="目的地城市")
    start_date: str = Field(..., description="开始日期 YYYY-MM-DD")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD")
    travel_days: int = Field(..., description="旅行天数", ge=1, le=30)
    transportation: str = Field(..., min_length=1, description="交通方式")
    accommodation: str = Field(..., min_length=1, description="住宿偏好")
    preferences: List[str] = Field(
        default_factory=list,
        max_length=10,
        description="旅行偏好标签",
    )
    free_text_input: Optional[str] = Field(
        default="",
        max_length=1000,
        description="额外要求",
    )

    @model_validator(mode="after")
    def validate_date_span(self) -> "TripRequest":
        """
        校验日期格式、先后关系和声明天数是否一致。

        Returns:
            日期跨度合法的当前请求。

        Raises:
            ValueError: 日期格式错误、结束日期早于开始日期或天数不一致。
        """
        try:
            start = date.fromisoformat(self.start_date)
            end = date.fromisoformat(self.end_date)
        except ValueError as exc:
            raise ValueError("start_date和end_date必须使用YYYY-MM-DD格式") from exc

        if end < start:
            raise ValueError("end_date不能早于start_date")

        actual_days = (end - start).days + 1
        if actual_days != self.travel_days:
            raise ValueError(
                f"travel_days应为{actual_days}，必须与日期跨度一致"
            )
        return self


class POISearchRequest(BaseModel):
    """POI搜索请求"""
    keywords: str = Field(..., description="搜索关键词")
    city: str = Field(..., description="城市")
    citylimit: bool = Field(default=True, description="是否限制在城市范围内")


class RouteRequest(BaseModel):
    """路线规划请求"""
    origin_address: str = Field(..., description="起点地址")
    destination_address: str = Field(..., description="终点地址")
    origin_city: Optional[str] = Field(default=None, description="起点城市")
    destination_city: Optional[str] = Field(default=None, description="终点城市")
    route_type: str = Field(default="walking", description="路线类型: walking/driving/transit")


# ============ 响应模型 ============


class FactQuality(str, Enum):
    """业务事实值的可信类型。"""

    VERIFIED = "verified"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class FactSource(BaseModel):
    """一个业务字段或实体的数据来源说明。"""

    provider: str = Field(..., description="数据提供方稳定标识")
    quality: FactQuality = Field(..., description="事实可信类型")
    retrieved_at: Optional[datetime] = Field(
        default=None,
        description="外部数据抓取时间",
    )
    note: Optional[str] = Field(default=None, description="来源或估算口径说明")


class VisitConstraints(BaseModel):
    """地点开放时间、固定闭馆日和预约要求。"""

    opening_time: Optional[str] = Field(
        default=None,
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
        description="常规开放时间 HH:MM",
    )
    closing_time: Optional[str] = Field(
        default=None,
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
        description="常规闭馆时间 HH:MM",
    )
    closing_day_offset: Literal[0, 1] = Field(
        default=0,
        description="闭馆时间相对开放日期的天数偏移，1表示次日闭馆",
    )
    raw_opening_hours: Optional[str] = Field(
        default=None,
        description="外部服务返回的原始营业时间",
    )
    closed_weekdays: List[int] = Field(
        default_factory=list,
        description="固定闭馆星期，周一为0、周日为6",
    )
    reservation_required: Optional[bool] = Field(
        default=None,
        description="是否通常需要提前预约；未知时为null",
    )
    opening_hours_source: Optional[FactSource] = Field(
        default=None,
        description="开放时间和闭馆日来源",
    )
    reservation_source: Optional[FactSource] = Field(
        default=None,
        description="预约要求来源",
    )

    @field_validator("closed_weekdays")
    @classmethod
    def validate_closed_weekdays(cls, value: List[int]) -> List[int]:
        """
        校验并去重固定闭馆星期。

        Args:
            value: 周一为0、周日为6的星期序号列表。

        Returns:
            已排序且去重的星期序号列表。

        Raises:
            ValueError: 任一序号不在0到6范围内时抛出。
        """
        if any(day < 0 or day > 6 for day in value):
            raise ValueError("closed_weekdays必须在0到6之间")
        return sorted(set(value))

    @model_validator(mode="after")
    def validate_opening_window(self) -> "VisitConstraints":
        """
        校验常规营业时间必须成对出现且持续时间不超过24小时。

        Returns:
            营业时间窗口合法的当前约束。

        Raises:
            ValueError: 时间字段不成对或不构成有效营业区间时抛出。
        """
        if bool(self.opening_time) != bool(self.closing_time):
            raise ValueError("opening_time和closing_time必须同时提供")
        if not self.opening_time or not self.closing_time:
            if self.closing_day_offset:
                raise ValueError("没有营业时间时closing_day_offset必须为0")
            return self

        opening_minutes = self._clock_minutes(self.opening_time)
        closing_minutes = (
            self._clock_minutes(self.closing_time)
            + self.closing_day_offset * 24 * 60
        )
        duration = closing_minutes - opening_minutes
        if duration <= 0 or duration > 24 * 60:
            raise ValueError("营业时间必须正向推进且不能超过24小时")
        return self

    @staticmethod
    def _clock_minutes(value: str) -> int:
        """
        将已通过格式校验的时钟文本转换为日内分钟数。

        Args:
            value: 24小时制 HH:MM 时间。

        Returns:
            从当天00:00起算的分钟数。
        """
        hours, minutes = value.split(":", 1)
        return int(hours) * 60 + int(minutes)


class AgentRunMetrics(BaseModel):
    """一次 LLM Agent 调用的可观测指标。"""

    controller: str = Field(..., description="控制器稳定标识")
    agent_name: str = Field(..., description="Agent 显示名称")
    succeeded: bool = Field(..., description="LLM 调用是否成功返回")
    elapsed_seconds: float = Field(..., ge=0, description="调用耗时")
    input_chars: int = Field(..., ge=0, description="输入字符数")
    output_chars: int = Field(..., ge=0, description="输出字符数")
    model: Optional[str] = Field(default=None, description="模型标识")
    input_tokens: Optional[int] = Field(default=None, ge=0, description="输入 token 数")
    output_tokens: Optional[int] = Field(default=None, ge=0, description="输出 token 数")
    total_tokens: Optional[int] = Field(default=None, ge=0, description="总 token 数")
    cost_cny: Optional[float] = Field(default=None, ge=0, description="本次调用费用")
    usage_source: Literal["provider", "estimated", "unavailable"] = "unavailable"


class AgentDecisionTrace(BaseModel):
    """持久化到旅行计划中的单个 Agent 决策与调用状态。"""

    controller: str = Field(..., description="控制器稳定标识")
    status: Literal["success", "degraded"]
    decision: Dict[str, Any] = Field(default_factory=dict)
    metrics: Optional[AgentRunMetrics] = None
    error_message: Optional[str] = None


class ExternalCallMetric(BaseModel):
    """一次外部数据或工具调用的计量记录。"""

    provider: str = Field(..., description="外部服务提供方")
    operation: str = Field(..., description="操作或工具名称")
    succeeded: bool = Field(..., description="调用是否成功完成")
    elapsed_seconds: float = Field(..., ge=0, description="调用耗时")
    requested_at: datetime = Field(..., description="调用开始时间")
    units: int = Field(default=1, ge=1, description="计费请求单位数")
    cost_cny: Optional[float] = Field(default=None, ge=0, description="已知调用费用")
    error_type: Optional[str] = Field(default=None, description="稳定错误类型")

class Location(BaseModel):
    """地理位置"""
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")


class Attraction(BaseModel):
    """景点信息"""
    name: str = Field(..., description="景点名称")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    visit_duration: int = Field(..., description="建议游览时间(分钟)")
    description: str = Field(..., description="景点描述")
    category: Optional[str] = Field(default="景点", description="景点类别")
    rating: Optional[float] = Field(default=None, description="评分")
    photos: Optional[List[str]] = Field(default_factory=list, description="景点图片URL列表")
    poi_id: Optional[str] = Field(default="", description="POI ID")
    image_url: Optional[str] = Field(default=None, description="图片URL")
    ticket_price: int = Field(default=0, description="门票价格(元)")
    source: Optional[FactSource] = Field(default=None, description="POI事实来源")
    ticket_price_source: Optional[FactSource] = Field(
        default=None,
        description="门票价格来源",
    )
    visit_constraints: Optional[VisitConstraints] = Field(
        default=None,
        description="开放时间、闭馆日和预约要求",
    )


class Meal(BaseModel):
    """餐饮信息"""
    type: str = Field(..., description="餐饮类型: breakfast/lunch/dinner/snack")
    name: str = Field(..., description="餐饮名称")
    address: Optional[str] = Field(default=None, description="地址")
    location: Optional[Location] = Field(default=None, description="经纬度坐标")
    description: Optional[str] = Field(default=None, description="描述")
    estimated_cost: int = Field(default=0, description="预估费用(元)")
    poi_id: Optional[str] = Field(default=None, description="高德POI ID")
    rating: Optional[float] = Field(default=None, description="高德评分")
    source: Optional[FactSource] = Field(default=None, description="餐厅事实来源")
    cost_source: Optional[FactSource] = Field(default=None, description="餐费来源")


class Hotel(BaseModel):
    """酒店信息"""
    name: str = Field(..., description="酒店名称")
    address: str = Field(default="", description="酒店地址")
    location: Optional[Location] = Field(default=None, description="酒店位置")
    price_range: str = Field(default="", description="价格范围")
    rating: str = Field(default="", description="评分")
    distance: str = Field(default="", description="距离景点距离")
    type: str = Field(default="", description="酒店类型")
    estimated_cost: int = Field(default=0, description="预估费用(元/晚)")
    poi_id: str = Field(default="", description="高德POI ID")
    source: Optional[FactSource] = Field(default=None, description="酒店事实来源")
    cost_source: Optional[FactSource] = Field(default=None, description="住宿价格来源")

class RouteLeg(BaseModel):
    """同一天两个相邻景点之间的真实路线。"""

    origin_poi_id: str = Field(..., description="起点POI ID")
    destination_poi_id: str = Field(..., description="终点POI ID")
    origin_name: str = Field(..., description="起点名称")
    destination_name: str = Field(..., description="终点名称")
    distance: float = Field(..., description="路线距离，单位为米")
    duration: int = Field(..., description="预计时间，单位为秒")
    route_type: str = Field(..., description="路线类型")
    description: str = Field(default="", description="路线说明")
    geometry: List[Location] = Field(
        default_factory=list,
        description="按行进顺序排列的真实或降级路线坐标",
    )
    status: Literal["available", "estimated", "unavailable"] = Field(
        default="available",
        description="真实可用、估算降级或不可达",
    )
    fallback_reason: Optional[str] = Field(
        default=None,
        description="路线降级或不可达原因",
    )
    cross_region: bool = Field(
        default=False,
        description="是否触发长距离跨区域惩罚",
    )
    source: Optional[FactSource] = Field(default=None, description="路线事实来源")


class TimelineEntry(BaseModel):
    """单日可执行时间轴中的一个连续活动。"""

    kind: Literal["attraction", "meal", "transit", "wait"]
    start_time: str = Field(
        ...,
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
        description="开始时间 HH:MM",
    )
    end_time: str = Field(
        ...,
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
        description="结束时间 HH:MM",
    )
    title: str = Field(..., min_length=1, description="活动名称")
    status: Literal["scheduled", "degraded", "conflict"] = "scheduled"
    poi_id: Optional[str] = Field(default=None, description="关联地点POI ID")
    route_index: Optional[int] = Field(default=None, ge=0, description="关联路线序号")
    note: Optional[str] = Field(default=None, description="约束或降级说明")

    @model_validator(mode="after")
    def validate_time_order(self) -> "TimelineEntry":
        """
        校验时间轴条目必须在同一天内正向推进。

        Returns:
            时间顺序合法的当前条目。

        Raises:
            ValueError: 结束时间不晚于开始时间时抛出。
        """
        if self.end_time <= self.start_time:
            raise ValueError("时间轴条目的end_time必须晚于start_time")
        return self

class DayPlan(BaseModel):
    """单日行程"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_index: int = Field(..., description="第几天(从0开始)")
    description: str = Field(..., description="当日行程描述")
    transportation: str = Field(..., description="交通方式")
    accommodation: str = Field(..., description="住宿")
    hotel: Optional[Hotel] = Field(default=None, description="推荐酒店")
    attractions: List[Attraction] = Field(default_factory=list, description="景点列表")
    meals: List[Meal] = Field(default_factory=list, description="餐饮列表")
    routes: List[RouteLeg] = Field(
        default_factory=list,
        description="当天相邻景点间的真实路线",
    )
    timeline: List[TimelineEntry] = Field(
        default_factory=list,
        description="包含游览、用餐和通勤的可执行时间轴",
    )
    schedule_status: Literal["feasible", "degraded", "infeasible"] = Field(
        default="degraded",
        description="当天时间表可执行状态",
    )
    total_visit_minutes: int = Field(default=0, ge=0, description="游览总分钟数")
    total_transit_minutes: int = Field(default=0, ge=0, description="通勤总分钟数")


class WeatherInfo(BaseModel):
    """天气信息"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_weather: str = Field(default="", description="白天天气")
    night_weather: str = Field(default="", description="夜间天气")
    day_temp: Union[int, str] = Field(default=0, description="白天温度")
    night_temp: Union[int, str] = Field(default=0, description="夜间温度")
    wind_direction: str = Field(default="", description="风向")
    wind_power: str = Field(default="", description="风力")
    source: Optional[FactSource] = Field(default=None, description="天气事实来源")

    @field_validator('day_temp', 'night_temp', mode='before')
    @classmethod
    def parse_temperature(cls, v):
        """解析温度,移除°C等单位"""
        if isinstance(v, str):
            # 移除°C, ℃等单位符号
            v = v.replace('°C', '').replace('℃', '').replace('°', '').strip()
            try:
                return int(v)
            except ValueError:
                return 0
        return v


class Budget(BaseModel):
    """预算信息"""
    total_attractions: int = Field(default=0, description="景点门票总费用")
    total_hotels: int = Field(default=0, description="酒店总费用")
    total_meals: int = Field(default=0, description="餐饮总费用")
    total_transportation: int = Field(default=0, description="交通总费用")
    total: int = Field(default=0, description="总费用")
    source: Optional[FactSource] = Field(default=None, description="预算计算来源")
    unknown_cost_items: List[str] = Field(
        default_factory=list,
        description="缺少可靠价格的项目",
    )


class TripPlan(BaseModel):
    """旅行计划"""
    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    days: List[DayPlan] = Field(..., description="每日行程")
    weather_info: List[WeatherInfo] = Field(default_factory=list, description="天气信息")
    overall_suggestions: str = Field(..., description="总体建议")
    budget: Optional[Budget] = Field(default=None, description="预算信息")

    warnings: List[str] = Field(
        default_factory=list,
        description="外部服务降级或部分数据缺失提示",
    )
    agent_trace: List[AgentDecisionTrace] = Field(
        default_factory=list,
        description="控制 Agent 与 Planner 的结构化决策轨迹",
    )
    external_calls: List[ExternalCallMetric] = Field(
        default_factory=list,
        description="MCP和第三方数据服务调用计量记录",
    )


class TripPlanResponse(BaseModel):
    """旅行计划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[TripPlan] = Field(default=None, description="旅行计划数据")


class POIInfo(BaseModel):
    """POI信息"""
    id: str = Field(..., description="POI ID")
    name: str = Field(..., description="名称")
    type: str = Field(..., description="类型")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    tel: Optional[str] = Field(default=None, description="电话")
    rating: Optional[float] = Field(default=None, description="高德评分")
    photos: List[str] = Field(default_factory=list, description="高德POI图片URL")
    suggested_visit_duration: int = Field(
        default=180,
        ge=30,
        le=600,
        description="建议游览时间，单位为分钟",
    )
    source: Optional[FactSource] = Field(default=None, description="POI事实来源")
    visit_constraints: Optional[VisitConstraints] = Field(
        default=None,
        description="地点开放时间、闭馆日和预约要求",
    )


class POISearchResponse(BaseModel):
    """POI搜索响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[POIInfo] = Field(default_factory=list, description="POI列表")


class RouteInfo(BaseModel):
    """路线信息"""
    distance: float = Field(..., description="距离(米)")
    duration: int = Field(..., description="时间(秒)")
    route_type: str = Field(..., description="路线类型")
    description: str = Field(..., description="路线描述")
    geometry: List[Location] = Field(default_factory=list, description="路线轨迹坐标")
    source: Optional[FactSource] = Field(default=None, description="路线事实来源")


class RouteResponse(BaseModel):
    """路线规划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[RouteInfo] = Field(default=None, description="路线信息")


class WeatherResponse(BaseModel):
    """天气查询响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[WeatherInfo] = Field(default_factory=list, description="天气信息")


# ============ 错误响应 ============

class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = Field(default=False, description="是否成功")
    message: str = Field(..., description="错误消息")
    error_code: Optional[str] = Field(default=None, description="错误代码")

