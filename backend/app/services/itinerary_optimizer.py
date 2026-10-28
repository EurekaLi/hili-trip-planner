"""基于确定性约束和真实路线数据优化旅行行程。"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from math import asin, cos, radians, sin, sqrt
import re
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

from ..models.schemas import (
    Attraction,
    DayPlan,
    FactQuality,
    FactSource,
    Location,
    Meal,
    RouteLeg,
    TimelineEntry,
    TripPlan,
    TripRequest,
    VisitConstraints,
)


DAY_START_MINUTE = 9 * 60
DAY_END_MINUTE = 22 * 60
FULL_DAY_VISIT_MINUTES = 420
MIN_DAILY_VISIT_MINUTES = 420
TARGET_DAILY_VISIT_MINUTES = 480
MAX_DAILY_VISIT_MINUTES = 600
EVENING_START_MINUTE = 19 * 60
MEAL_WINDOWS = {
    "lunch": (12 * 60, 60, 13 * 60 + 30),
    "dinner": (18 * 60, 60, 20 * 60),
}
EVENING_KEYWORDS = ("什刹海", "后海", "夜市", "夜游", "夜景", "三里屯")
RESERVATION_KEYWORDS = (
    "故宫",
    "国家博物馆",
    "博物馆",
    "科技馆",
    "美术馆",
    "环球影城",
    "迪士尼",
)
MUSEUM_KEYWORDS = ("博物馆", "美术馆", "科技馆", "纪念馆")
FULL_DAY_KEYWORDS = ("环球影城", "迪士尼", "欢乐谷", "主题乐园")
WEEKDAY_TOKENS = {
    "周一": 0,
    "星期一": 0,
    "周二": 1,
    "星期二": 1,
    "周三": 2,
    "星期三": 2,
    "周四": 3,
    "星期四": 3,
    "周五": 4,
    "星期五": 4,
    "周六": 5,
    "星期六": 5,
    "周日": 6,
    "星期日": 6,
    "星期天": 6,
}


class RouteProvider(Protocol):
    """行程优化器依赖的最小路线查询接口。"""

    def plan_route_by_coordinates(
        self,
        origin: Location,
        destination: Location,
        route_type: str = "walking",
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
    ) -> Dict:
        """
        查询两个坐标之间的路线。

        Args:
            origin: 起点坐标。
            destination: 终点坐标。
            route_type: 步行、驾车或公共交通。
            origin_city: 起点城市。
            destination_city: 终点城市。

        Returns:
            路线服务返回的结构化字典。
        """


@dataclass
class _TimelineState:
    """时间轴构建过程中的可变状态。"""

    cursor: int = DAY_START_MINUTE
    entries: List[TimelineEntry] = field(default_factory=list)
    consumed_meals: set[str] = field(default_factory=set)
    status: str = "feasible"
    warnings: List[str] = field(default_factory=list)


@dataclass
class _OptimizationCandidate:
    """一个已完成路线和时间轴评估的候选行程。"""

    name: str
    plan: TripPlan
    score: float


def build_visit_constraints(
    name: str,
    category: str,
    raw_opening_hours: Optional[str] = None,
    verified_source: Optional[FactSource] = None,
) -> VisitConstraints:
    """
    将高德原始营业时间和保守业务规则转换为结构化游览约束。

    外部数据不存在时只生成带 `estimated` 或 `unknown` 来源的约束，避免把
    博物馆闭馆日或预约经验规则伪装成实时官方信息。

    Args:
        name: 地点名称。
        category: 高德地点类型。
        raw_opening_hours: 高德返回的原始营业时间文本。
        verified_source: 原始营业时间对应的事实来源。

    Returns:
        可供时间表校验的开放时间、闭馆日和预约约束。
    """
    identity = f"{name} {category}"
    raw_text = (raw_opening_hours or "").strip()
    opening_time: Optional[str] = None
    closing_time: Optional[str] = None
    closing_day_offset = 0
    closed_weekdays: List[int] = []

    time_matches = re.findall(
        r"([01]?\d|2[0-3]):([0-5]\d)\s*[-至~—]\s*"
        r"(次日)?\s*([01]?\d|2[0-3]):([0-5]\d)",
        raw_text,
    )
    if time_matches:
        (
            start_hour,
            start_minute,
            next_day_marker,
            end_hour,
            end_minute,
        ) = time_matches[0]
        opening_time = f"{int(start_hour):02d}:{start_minute}"
        closing_time = f"{int(end_hour):02d}:{end_minute}"
        if next_day_marker or closing_time <= opening_time:
            closing_day_offset = 1
        opening_minutes = int(start_hour) * 60 + int(start_minute)
        closing_minutes = (
            int(end_hour) * 60
            + int(end_minute)
            + closing_day_offset * 24 * 60
        )
        if not 0 < closing_minutes - opening_minutes <= 24 * 60:
            opening_time = None
            closing_time = None
            closing_day_offset = 0

    for token, weekday in WEEKDAY_TOKENS.items():
        if re.search(rf"{re.escape(token)}[^，,；;]*?(闭馆|休息|不开放)", raw_text):
            closed_weekdays.append(weekday)

    if opening_time and closing_time:
        opening_source = verified_source or FactSource(
            provider="amap",
            quality=FactQuality.VERIFIED,
            retrieved_at=datetime.now(timezone.utc),
            note="高德POI营业时间",
        )
    else:
        if any(keyword in identity for keyword in EVENING_KEYWORDS):
            opening_time, closing_time = "18:00", "23:00"
        elif any(keyword in identity for keyword in FULL_DAY_KEYWORDS):
            opening_time, closing_time = "09:00", "20:00"
        elif any(keyword in identity for keyword in MUSEUM_KEYWORDS):
            opening_time, closing_time = "09:00", "17:00"
            closed_weekdays = closed_weekdays or [0]
        else:
            opening_time, closing_time = "09:00", "18:00"
        opening_source = FactSource(
            provider="backend_rule",
            quality=FactQuality.ESTIMATED,
            retrieved_at=datetime.now(timezone.utc),
            note="未获得可靠营业时间，使用地点类型的保守时间窗口",
        )

    reservation_required = any(
        keyword in identity for keyword in RESERVATION_KEYWORDS
    )
    reservation_source = FactSource(
        provider="backend_rule" if reservation_required else "unknown",
        quality=(
            FactQuality.ESTIMATED
            if reservation_required
            else FactQuality.UNKNOWN
        ),
        retrieved_at=datetime.now(timezone.utc),
        note=(
            "热门场馆通常需要预约，请以官方渠道为准"
            if reservation_required
            else "未连接官方预约数据源"
        ),
    )

    return VisitConstraints(
        opening_time=opening_time,
        closing_time=closing_time,
        closing_day_offset=closing_day_offset,
        raw_opening_hours=raw_text or None,
        closed_weekdays=closed_weekdays,
        reservation_required=reservation_required or None,
        opening_hours_source=opening_source,
        reservation_source=reservation_source,
    )


class ItineraryOptimizer:
    """比较候选分组并生成包含真实通勤的可执行时间轴。"""

    def __init__(
        self,
        route_provider: RouteProvider,
        max_route_calls: int = 18,
        cross_region_minutes: int = 75,
    ) -> None:
        """
        初始化请求内路线缓存和优化预算。

        Args:
            route_provider: 提供坐标路线查询能力的地图服务。
            max_route_calls: 单次优化最多新增的付费路线调用数。
            cross_region_minutes: 触发跨区域惩罚的单段通勤分钟数。
        """
        self.route_provider = route_provider
        self.max_route_calls = max(1, max_route_calls)
        self.cross_region_seconds = max(1, cross_region_minutes) * 60
        self._route_calls = 0
        self._route_cache: Dict[Tuple[str, str, str], RouteLeg] = {}

    def optimize(self, trip_plan: TripPlan, request: TripRequest) -> TripPlan:
        """
        使用原始方案和地理重组方案生成并选择最低代价的可执行行程。

        候选代价由真实路线耗时、跨区域惩罚、路线降级和时间冲突组成。
        优化只改变每日景点分组、顺序、路线和时间轴，不修改事实身份字段。

        Args:
            trip_plan: 已完成真实 POI 回填和游览时长补足的旅行计划。
            request: 用户提交并通过日期校验的旅行请求。

        Returns:
            带路线 geometry、时间轴和可执行状态的最低代价行程。
        """
        self._route_calls = 0
        self._route_cache = {}

        original_groups = [
            [item.model_copy(deep=True) for item in day.attractions]
            for day in trip_plan.days
        ]
        geographic_groups = self._build_geographic_groups(trip_plan.days)

        candidates = [
            self._evaluate_candidate(
                name="planner_grouping",
                trip_plan=trip_plan,
                request=request,
                groups=original_groups,
            )
        ]
        if self._group_signature(geographic_groups) != self._group_signature(
            original_groups
        ):
            candidates.append(
                self._evaluate_candidate(
                    name="route_optimized_grouping",
                    trip_plan=trip_plan,
                    request=request,
                    groups=geographic_groups,
                )
            )

        selected = min(candidates, key=lambda candidate: candidate.score)
        if selected.name == "route_optimized_grouping":
            selected.plan.warnings.append(
                "已根据真实路线耗时选择更紧凑的每日景点分组"
            )
        selected.plan.warnings = list(dict.fromkeys(selected.plan.warnings))
        return selected.plan

    def refresh(self, trip_plan: TripPlan, request: TripRequest) -> TripPlan:
        """
        保留用户指定的每日分组和顺序并刷新路线与时间轴。

        Args:
            trip_plan: 用户修改后的旅行计划。
            request: 根据行程元数据重建的旅行请求。

        Returns:
            分组顺序不变且路线、时间轴已重新计算的计划。
        """
        self._route_calls = 0
        self._route_cache = {}
        groups = [
            [item.model_copy(deep=True) for item in day.attractions]
            for day in trip_plan.days
        ]
        candidate = self._evaluate_candidate(
            name="user_grouping",
            trip_plan=trip_plan,
            request=request,
            groups=groups,
            normalize_order=False,
        )
        candidate.plan.warnings = list(dict.fromkeys(candidate.plan.warnings))
        return candidate.plan

    def _evaluate_candidate(
        self,
        name: str,
        trip_plan: TripPlan,
        request: TripRequest,
        groups: Sequence[Sequence[Attraction]],
        normalize_order: bool = True,
    ) -> _OptimizationCandidate:
        """
        为一个景点分组查询路线、构建时间轴并计算候选代价。

        Args:
            name: 候选方案稳定名称。
            trip_plan: 作为事实和非景点字段来源的基础计划。
            request: 用户旅行请求。
            groups: 与每日行程一一对应的景点分组。
            normalize_order: 是否允许优化器调整日内顺序。

        Returns:
            包含完整计划和可比较代价的候选方案。
        """
        candidate_plan = trip_plan.model_copy(deep=True)
        route_type = self._resolve_route_type(request.transportation)
        score = 0.0

        for day, group in zip(candidate_plan.days, groups):
            day.attractions = (
                self._normalize_temporal_order(list(group), day.date)
                if normalize_order
                else [item.model_copy(deep=True) for item in group]
            )
            day.routes = self._build_routes(
                day.attractions,
                route_type=route_type,
                city=request.city,
            )
            timeline, status, warnings = self._build_timeline(day)
            day.timeline = timeline
            day.schedule_status = status
            day.total_visit_minutes = sum(
                attraction.visit_duration for attraction in day.attractions
            )
            day.total_transit_minutes = sum(
                max(1, round(route.duration / 60)) for route in day.routes
            )
            candidate_plan.warnings.extend(warnings)
            score += self._score_day(day)

        return _OptimizationCandidate(name=name, plan=candidate_plan, score=score)

    def _build_geographic_groups(
        self,
        days: Sequence[DayPlan],
    ) -> List[List[Attraction]]:
        """
        在保留全天景点独占日和每日数量的前提下生成地理紧凑分组。

        Args:
            days: 当前 Planner 分组后的每日计划。

        Returns:
            与输入天数相同的地理候选分组。
        """
        groups: List[List[Attraction]] = [[] for _ in days]
        unlocked_indices: List[int] = []
        pool: List[Attraction] = []

        for index, day in enumerate(days):
            full_day = next(
                (
                    attraction
                    for attraction in day.attractions
                    if attraction.visit_duration >= FULL_DAY_VISIT_MINUTES
                ),
                None,
            )
            if full_day is not None:
                groups[index] = [full_day.model_copy(deep=True)]
                continue
            unlocked_indices.append(index)
            pool.extend(
                attraction.model_copy(deep=True)
                for attraction in day.attractions
            )

        chain = self._build_nearest_neighbor_chain(pool)
        partitions = self._partition_chain(chain, len(unlocked_indices))
        for day_index, partition in zip(unlocked_indices, partitions):
            groups[day_index] = partition
        return groups

    def _partition_chain(
        self,
        chain: Sequence[Attraction],
        group_count: int,
    ) -> List[List[Attraction]]:
        """
        用动态规划把地理连续序列切为接近目标游览时长的日分组。

        Args:
            chain: 按地理邻近关系排列的景点序列。
            group_count: 可用于安排普通景点的天数。

        Returns:
            保持原顺序且覆盖全部景点的连续分组。
        """
        if group_count <= 0:
            return []
        if not chain:
            return [[] for _ in range(group_count)]
        if len(chain) < group_count:
            return [
                [chain[index]] if index < len(chain) else []
                for index in range(group_count)
            ]

        prefix = [0]
        for attraction in chain:
            prefix.append(prefix[-1] + attraction.visit_duration)

        infinity = float("inf")
        costs = [
            [infinity for _ in range(group_count + 1)]
            for _ in range(len(chain) + 1)
        ]
        previous = [
            [-1 for _ in range(group_count + 1)]
            for _ in range(len(chain) + 1)
        ]
        costs[0][0] = 0

        for end in range(1, len(chain) + 1):
            for groups_used in range(1, min(group_count, end) + 1):
                earliest_start = groups_used - 1
                for start in range(earliest_start, end):
                    if costs[start][groups_used - 1] == infinity:
                        continue
                    minutes = prefix[end] - prefix[start]
                    candidate_cost = (
                        costs[start][groups_used - 1]
                        + self._visit_capacity_penalty(minutes)
                    )
                    if candidate_cost < costs[end][groups_used]:
                        costs[end][groups_used] = candidate_cost
                        previous[end][groups_used] = start

        partitions: List[List[Attraction]] = []
        end = len(chain)
        groups_used = group_count
        while groups_used > 0:
            start = previous[end][groups_used]
            if start < 0:
                return [list(chain)] + [[] for _ in range(group_count - 1)]
            partitions.append(list(chain[start:end]))
            end = start
            groups_used -= 1
        partitions.reverse()
        return partitions

    def _build_nearest_neighbor_chain(
        self,
        attractions: Sequence[Attraction],
    ) -> List[Attraction]:
        """
        使用经纬度最近邻生成地理连续序列，并把夜游地点放在分组末端。

        Args:
            attractions: 等待重新分组的景点集合。

        Returns:
            每个景点恰好出现一次的地理连续序列。
        """
        remaining = [item.model_copy(deep=True) for item in attractions]
        if not remaining:
            return []

        daytime = [item for item in remaining if not self._is_evening(item)]
        evening = [item for item in remaining if self._is_evening(item)]
        available = daytime or evening
        current = max(available, key=lambda item: item.rating or 0)
        chain = [current]
        remaining.remove(current)

        while remaining:
            non_evening = [item for item in remaining if not self._is_evening(item)]
            candidates = non_evening or remaining
            current = min(
                candidates,
                key=lambda item: self._haversine_distance(
                    chain[-1].location,
                    item.location,
                ),
            )
            chain.append(current)
            remaining.remove(current)
        return chain

    def _normalize_temporal_order(
        self,
        attractions: List[Attraction],
        day_date: str,
    ) -> List[Attraction]:
        """
        让全天景点独占一天，并把早闭馆地点和夜游地点放在合适位置。

        Args:
            attractions: 一个候选日内的景点顺序。
            day_date: 当前行程日期。

        Returns:
            满足基础时间偏好的景点顺序。
        """
        full_day = next(
            (
                attraction
                for attraction in attractions
                if attraction.visit_duration >= FULL_DAY_VISIT_MINUTES
            ),
            None,
        )
        if full_day is not None:
            return [full_day]

        weekday = date.fromisoformat(day_date).weekday()
        indexed = list(enumerate(attractions))

        def sort_key(item: Tuple[int, Attraction]) -> Tuple[int, int, int, int]:
            """
            构造闭馆、结束时间和夜游优先级组成的稳定排序键。

            Args:
                item: 原始下标与景点组成的元组。

            Returns:
                可直接用于升序排序的约束优先级元组。
            """
            index, attraction = item
            constraints = self._constraints_for(attraction)
            is_closed = weekday in constraints.closed_weekdays
            closing = self._constraint_closing_minutes(constraints)
            return (
                1 if is_closed else 0,
                1 if self._is_evening(attraction) else 0,
                closing,
                index,
            )

        return [item for _, item in sorted(indexed, key=sort_key)]

    def _build_routes(
        self,
        attractions: Sequence[Attraction],
        route_type: str,
        city: str,
    ) -> List[RouteLeg]:
        """
        查询并缓存一个日内所有相邻景点路线。

        Args:
            attractions: 已确定顺序的日内景点。
            route_type: 用户交通方式对应的高德路线类型。
            city: 路线起终点所在城市。

        Returns:
            与相邻景点对一一对应的真实或显式降级路线。
        """
        return [
            self._resolve_route(origin, destination, route_type, city)
            for origin, destination in zip(attractions, attractions[1:])
        ]

    def _resolve_route(
        self,
        origin: Attraction,
        destination: Attraction,
        route_type: str,
        city: str,
    ) -> RouteLeg:
        """
        从请求内缓存读取路线，或在预算内调用地图服务并建立降级路线。

        Args:
            origin: 起点景点。
            destination: 终点景点。
            route_type: 高德路线类型。
            city: 起终点城市。

        Returns:
            包含状态、轨迹和来源的路线段。
        """
        cache_key = (
            origin.poi_id or origin.name,
            destination.poi_id or destination.name,
            route_type,
        )
        cached = self._route_cache.get(cache_key)
        if cached is not None:
            return cached.model_copy(deep=True)

        if self._route_calls >= self.max_route_calls:
            route = self._build_fallback_route(
                origin,
                destination,
                route_type,
                "路线调用预算已用尽",
            )
            self._route_cache[cache_key] = route
            return route.model_copy(deep=True)

        self._route_calls += 1
        try:
            payload = self.route_provider.plan_route_by_coordinates(
                origin=origin.location,
                destination=destination.location,
                route_type=route_type,
                origin_city=city,
                destination_city=city,
            )
        except Exception as exc:
            route = self._build_fallback_route(
                origin,
                destination,
                route_type,
                f"地图路线调用失败：{type(exc).__name__}",
            )
            self._route_cache[cache_key] = route
            return route.model_copy(deep=True)

        if not payload or payload.get("status") == "unreachable":
            route = self._build_fallback_route(
                origin,
                destination,
                route_type,
                "地图服务未返回可达路线",
                force_unavailable=payload.get("status") == "unreachable" if payload else False,
            )
            self._route_cache[cache_key] = route
            return route.model_copy(deep=True)

        geometry = self._normalize_geometry(payload.get("geometry"))
        duration = int(payload.get("duration") or 0)
        distance = float(payload.get("distance") or 0)
        if duration <= 0 or distance < 0:
            route = self._build_fallback_route(
                origin,
                destination,
                route_type,
                "地图服务返回的路线距离或时长无效",
            )
            self._route_cache[cache_key] = route
            return route.model_copy(deep=True)

        has_real_geometry = len(geometry) >= 2
        if not has_real_geometry:
            geometry = [origin.location, destination.location]
        source_payload = payload.get("source")
        source = (
            FactSource.model_validate(source_payload)
            if source_payload
            else FactSource(
                provider="amap",
                quality=FactQuality.VERIFIED,
                retrieved_at=datetime.now(timezone.utc),
                note="高德坐标路线规划",
            )
        )
        route = RouteLeg(
            origin_poi_id=origin.poi_id or "",
            destination_poi_id=destination.poi_id or "",
            origin_name=origin.name,
            destination_name=destination.name,
            distance=distance,
            duration=duration,
            route_type=route_type,
            description=str(payload.get("description") or "路线规划成功"),
            geometry=geometry,
            status="available" if has_real_geometry else "estimated",
            fallback_reason=(None if has_real_geometry else "地图路线缺少轨迹坐标"),
            cross_region=(
                duration >= self.cross_region_seconds or distance >= 25_000
            ),
            source=source,
        )
        self._route_cache[cache_key] = route
        return route.model_copy(deep=True)

    def _build_fallback_route(
        self,
        origin: Attraction,
        destination: Attraction,
        route_type: str,
        reason: str,
        force_unavailable: bool = False,
    ) -> RouteLeg:
        """
        使用球面距离创建可识别的估算路线或不可达占位路线。

        Args:
            origin: 起点景点。
            destination: 终点景点。
            route_type: 用户交通方式。
            reason: 触发降级的稳定原因。
            force_unavailable: 是否将该路线明确标记为不可达。

        Returns:
            带估算来源、端点轨迹和降级原因的路线段。
        """
        distance = self._haversine_distance(origin.location, destination.location)
        speeds_kmh = {"walking": 4.5, "transit": 18.0, "driving": 28.0}
        speed = speeds_kmh.get(route_type, 12.0)
        duration = max(60, round(distance / 1000 / speed * 3600))
        unavailable = force_unavailable or distance >= 80_000
        status = "unavailable" if unavailable else "estimated"
        return RouteLeg(
            origin_poi_id=origin.poi_id or "",
            destination_poi_id=destination.poi_id or "",
            origin_name=origin.name,
            destination_name=destination.name,
            distance=round(distance, 1),
            duration=duration,
            route_type=route_type,
            description=(
                "该段路线不可达，请更换景点或交通方式"
                if unavailable
                else "暂按两点距离估算通勤时间"
            ),
            geometry=[origin.location, destination.location],
            status=status,
            fallback_reason=reason,
            cross_region=(
                duration >= self.cross_region_seconds or distance >= 25_000
            ),
            source=FactSource(
                provider="backend_haversine",
                quality=(
                    FactQuality.UNKNOWN if unavailable else FactQuality.ESTIMATED
                ),
                retrieved_at=datetime.now(timezone.utc),
                note=reason,
            ),
        )

    def _build_timeline(
        self,
        day: DayPlan,
    ) -> Tuple[List[TimelineEntry], str, List[str]]:
        """
        将景点、三餐和通勤组合为无重叠的单日时间轴。

        Args:
            day: 已完成景点排序和相邻路线查询的单日计划。

        Returns:
            时间轴条目、可执行状态和面向用户的约束告警。
        """
        state = _TimelineState(cursor=DAY_START_MINUTE)
        meals = {meal.type: meal for meal in day.meals}
        breakfast = meals.get("breakfast")
        state.entries.append(
            TimelineEntry(
                kind="meal",
                start_time="08:00",
                end_time="08:30",
                title=breakfast.name if breakfast else "早餐",
                status="scheduled" if breakfast else "degraded",
                poi_id=breakfast.poi_id if breakfast else None,
                note=None if breakfast else "未获得真实早餐候选",
            )
        )
        if breakfast is None:
            self._degrade_state(state)

        weekday = date.fromisoformat(day.date).weekday()
        for index, attraction in enumerate(day.attractions):
            constraints = self._constraints_for(attraction)
            is_evening = self._is_evening(attraction)
            if is_evening and state.cursor < EVENING_START_MINUTE:
                self._consume_due_meals(
                    state,
                    meals,
                    up_to=EVENING_START_MINUTE,
                    force=True,
                )
                state.cursor = max(state.cursor, EVENING_START_MINUTE)

            opening = self._time_to_minutes(constraints.opening_time or "09:00")
            closing = self._constraint_closing_minutes(constraints)
            if weekday in constraints.closed_weekdays:
                self._conflict_state(
                    state,
                    f"{day.date} {attraction.name}可能固定闭馆",
                )
            if state.cursor < opening:
                self._append_timeline_entry(
                    state,
                    kind="wait",
                    duration=opening - state.cursor,
                    title=f"等待{attraction.name}开放",
                    status="scheduled",
                    note=f"预计{constraints.opening_time}开放",
                )

            entry_status = "scheduled"
            notes: List[str] = []
            if constraints.reservation_required:
                entry_status = "degraded"
                notes.append("通常需要提前预约，请以官方渠道为准")
                self._degrade_state(state)
                state.warnings.append(f"{attraction.name}可能需要提前预约")

            start_minute = state.cursor
            self._schedule_activity(
                state,
                meals,
                kind="attraction",
                title=attraction.name,
                duration=max(30, attraction.visit_duration),
                status=entry_status,
                poi_id=attraction.poi_id or None,
                note="；".join(notes) or None,
                allow_split=True,
            )
            if start_minute >= closing or state.cursor > closing:
                self._conflict_state(
                    state,
                    (
                        f"{attraction.name}的安排可能超出"
                        f"{self._format_constraint_closing_time(constraints)}闭馆时间"
                    ),
                )

            if index < len(day.routes):
                route = day.routes[index]
                route_status = (
                    "scheduled" if route.status == "available" else "degraded"
                )
                if route.status == "unavailable":
                    route_status = "conflict"
                    self._conflict_state(
                        state,
                        f"{route.origin_name}到{route.destination_name}不可达",
                    )
                elif route.status == "estimated":
                    self._degrade_state(state)
                    state.warnings.append(
                        f"{route.origin_name}到{route.destination_name}使用估算通勤"
                    )
                if route.cross_region:
                    self._degrade_state(state)
                    state.warnings.append(
                        f"{route.origin_name}到{route.destination_name}通勤较远"
                    )
                self._schedule_activity(
                    state,
                    meals,
                    kind="transit",
                    title=f"前往{route.destination_name}",
                    duration=max(1, round(route.duration / 60)),
                    status=route_status,
                    route_index=index,
                    note=route.fallback_reason or route.description,
                    allow_split=False,
                )

        self._consume_due_meals(state, meals, up_to=18 * 60, force=True)
        if state.cursor > DAY_END_MINUTE:
            self._conflict_state(
                state,
                f"{day.date}行程预计在{self._minutes_to_time(state.cursor)}后结束",
            )
        return state.entries, state.status, list(dict.fromkeys(state.warnings))

    def _schedule_activity(
        self,
        state: _TimelineState,
        meals: Dict[str, Meal],
        kind: str,
        title: str,
        duration: int,
        status: str,
        poi_id: Optional[str] = None,
        route_index: Optional[int] = None,
        note: Optional[str] = None,
        allow_split: bool = False,
    ) -> None:
        """
        在固定午晚餐时段之间安排活动，必要时拆分长时间游览。

        Args:
            state: 当前时间轴构建状态。
            meals: 按类型索引的真实餐厅安排。
            kind: 活动类型。
            title: 活动显示名称。
            duration: 活动分钟数。
            status: 活动可执行状态。
            poi_id: 可选关联 POI ID。
            route_index: 可选关联路线序号。
            note: 可选约束或导航说明。
            allow_split: 是否允许在固定用餐时段拆分活动。
        """
        remaining = max(1, duration)
        segment_index = 0
        while remaining > 0 and state.cursor < 24 * 60 - 1:
            meal_type, meal_start = self._next_meal_after(state)
            if meal_type is not None and state.cursor >= meal_start:
                self._insert_meal(state, meals, meal_type)
                continue

            available = (
                meal_start - state.cursor
                if meal_type is not None
                else remaining
            )
            if meal_type is not None and remaining > available:
                if allow_split and available >= 30:
                    segment_duration = available
                else:
                    state.cursor = meal_start
                    self._insert_meal(state, meals, meal_type)
                    continue
            else:
                segment_duration = remaining

            segment_title = title if segment_index == 0 else f"继续{title}"
            self._append_timeline_entry(
                state,
                kind=kind,
                duration=segment_duration,
                title=segment_title,
                status=status,
                poi_id=poi_id,
                route_index=route_index,
                note=note,
            )
            remaining -= segment_duration
            segment_index += 1

    def _consume_due_meals(
        self,
        state: _TimelineState,
        meals: Dict[str, Meal],
        up_to: int,
        force: bool,
    ) -> None:
        """
        按时间顺序补入目标时刻之前尚未安排的午餐和晚餐。

        Args:
            state: 当前时间轴构建状态。
            meals: 按类型索引的真实餐厅安排。
            up_to: 需要消费的最晚目标分钟。
            force: 是否允许把游览光标推进到目标用餐时间。
        """
        for meal_type, (target, _, _) in MEAL_WINDOWS.items():
            if meal_type in state.consumed_meals or target > up_to:
                continue
            if force and state.cursor < target:
                state.cursor = target
            if state.cursor >= target:
                self._insert_meal(state, meals, meal_type)

    def _insert_meal(
        self,
        state: _TimelineState,
        meals: Dict[str, Meal],
        meal_type: str,
    ) -> None:
        """
        在当前光标处插入一次用餐并标记是否晚于合理窗口。

        Args:
            state: 当前时间轴构建状态。
            meals: 按类型索引的真实餐厅安排。
            meal_type: lunch 或 dinner。
        """
        if meal_type in state.consumed_meals:
            return
        target, duration, latest = MEAL_WINDOWS[meal_type]
        state.cursor = max(state.cursor, target)
        meal = meals.get(meal_type)
        is_late = state.cursor > latest
        status = "scheduled" if meal is not None and not is_late else "degraded"
        notes: List[str] = []
        if meal is None:
            notes.append("未获得真实餐厅候选")
        if is_late:
            notes.append("晚于建议用餐时段")
        self._append_timeline_entry(
            state,
            kind="meal",
            duration=duration,
            title=meal.name if meal else ("午餐" if meal_type == "lunch" else "晚餐"),
            status=status,
            poi_id=meal.poi_id if meal else None,
            note="；".join(notes) or None,
        )
        state.consumed_meals.add(meal_type)
        if status == "degraded":
            self._degrade_state(state)

    @staticmethod
    def _append_timeline_entry(
        state: _TimelineState,
        kind: str,
        duration: int,
        title: str,
        status: str,
        poi_id: Optional[str] = None,
        route_index: Optional[int] = None,
        note: Optional[str] = None,
    ) -> None:
        """
        从当前时间光标追加条目并推进光标。

        Args:
            state: 当前时间轴构建状态。
            kind: 活动类型。
            duration: 持续分钟数。
            title: 活动显示名称。
            status: 活动可执行状态。
            poi_id: 可选关联 POI ID。
            route_index: 可选关联路线序号。
            note: 可选说明。
        """
        start = min(state.cursor, 24 * 60 - 2)
        end = min(start + max(1, duration), 24 * 60 - 1)
        state.entries.append(
            TimelineEntry(
                kind=kind,
                start_time=ItineraryOptimizer._minutes_to_time(start),
                end_time=ItineraryOptimizer._minutes_to_time(end),
                title=title,
                status=status,
                poi_id=poi_id,
                route_index=route_index,
                note=note,
            )
        )
        state.cursor = end

    @staticmethod
    def _next_meal_after(state: _TimelineState) -> Tuple[Optional[str], int]:
        """
        找到当前时间轴尚未消费的下一固定用餐时间。

        Args:
            state: 当前时间轴构建状态。

        Returns:
            用餐类型和目标分钟；没有剩余用餐时返回 `(None, 0)`。
        """
        for meal_type, (target, _, _) in MEAL_WINDOWS.items():
            if meal_type not in state.consumed_meals:
                return meal_type, target
        return None, 0

    def _score_day(self, day: DayPlan) -> float:
        """
        计算路线耗时、跨区域、降级和时间冲突组成的日计划代价。

        Args:
            day: 已完成路线和时间轴构建的单日计划。

        Returns:
            数值越低越优的候选代价。
        """
        score = float(sum(route.duration for route in day.routes))
        for route in day.routes:
            if route.cross_region:
                score += max(route.duration - self.cross_region_seconds, 0) * 2
            if route.status == "estimated":
                score += 2_400
            elif route.status == "unavailable":
                score += 1_000_000
        if day.schedule_status == "degraded":
            score += 1_200
        elif day.schedule_status == "infeasible":
            score += 1_000_000
        score += self._visit_capacity_penalty(day.total_visit_minutes)
        return score

    @staticmethod
    def _visit_capacity_penalty(minutes: int) -> float:
        """
        对过短或过载游览日施加高于正常波动的容量惩罚。

        Args:
            minutes: 当天景点建议游览总分钟数。

        Returns:
            420至600分钟区间内为轻微目标偏差，区间外为平方惩罚。
        """
        if minutes < MIN_DAILY_VISIT_MINUTES:
            return float((MIN_DAILY_VISIT_MINUTES - minutes) ** 2 * 10)
        if minutes > MAX_DAILY_VISIT_MINUTES:
            return float((minutes - MAX_DAILY_VISIT_MINUTES) ** 2 * 10)
        return float(abs(minutes - TARGET_DAILY_VISIT_MINUTES))

    @staticmethod
    def _constraints_for(attraction: Attraction) -> VisitConstraints:
        """
        返回景点现有约束，缺失时根据名称和类型建立带来源的保守约束。

        Args:
            attraction: 需要安排时间的景点。

        Returns:
            始终包含常规开放时间的游览约束。
        """
        if attraction.visit_constraints is not None:
            return attraction.visit_constraints
        constraints = build_visit_constraints(
            name=attraction.name,
            category=attraction.category or "景点",
        )
        attraction.visit_constraints = constraints
        return constraints

    @staticmethod
    def _normalize_geometry(payload: object) -> List[Location]:
        """
        将路线服务返回的坐标字典或模型列表转换为 Location 列表。

        Args:
            payload: 路线 geometry 原始值。

        Returns:
            已过滤无效坐标的轨迹列表。
        """
        if not isinstance(payload, list):
            return []
        result: List[Location] = []
        for item in payload:
            try:
                location = (
                    item
                    if isinstance(item, Location)
                    else Location.model_validate(item)
                )
            except Exception:
                continue
            if not result or location != result[-1]:
                result.append(location)
        return result

    @staticmethod
    def _group_signature(groups: Sequence[Sequence[Attraction]]) -> Tuple:
        """
        将景点分组转换为可比较且与对象实例无关的签名。

        Args:
            groups: 候选每日景点分组。

        Returns:
            按日和顺序排列的 POI ID 或名称元组。
        """
        return tuple(
            tuple(item.poi_id or item.name for item in group)
            for group in groups
        )

    @staticmethod
    def _resolve_route_type(transportation: str) -> str:
        """
        将用户交通文案转换为地图路线类型。

        Args:
            transportation: 用户选择的交通方式。

        Returns:
            walking、driving 或 transit。
        """
        if any(keyword in transportation for keyword in ("自驾", "驾车", "汽车")):
            return "driving"
        if any(keyword in transportation for keyword in ("公交", "地铁", "公共交通")):
            return "transit"
        return "walking"

    @staticmethod
    def _is_evening(attraction: Attraction) -> bool:
        """
        判断地点是否具有明显夜游属性。

        Args:
            attraction: 待判断景点。

        Returns:
            名称、描述或类型含夜游关键词时返回 True。
        """
        identity = f"{attraction.name} {attraction.description} {attraction.category}"
        return any(keyword in identity for keyword in EVENING_KEYWORDS)

    @staticmethod
    def _haversine_distance(origin: Location, destination: Location) -> float:
        """
        计算两个 WGS84 近似坐标之间的球面距离。

        Args:
            origin: 起点坐标。
            destination: 终点坐标。

        Returns:
            两点间距离，单位为米。
        """
        longitude_delta = radians(destination.longitude - origin.longitude)
        latitude_delta = radians(destination.latitude - origin.latitude)
        origin_latitude = radians(origin.latitude)
        destination_latitude = radians(destination.latitude)
        value = (
            sin(latitude_delta / 2) ** 2
            + cos(origin_latitude)
            * cos(destination_latitude)
            * sin(longitude_delta / 2) ** 2
        )
        return 2 * 6_371_000 * asin(sqrt(value))

    @staticmethod
    def _time_to_minutes(value: str) -> int:
        """
        将 HH:MM 时间转换为当天分钟数。

        Args:
            value: 24小时制时间。

        Returns:
            从00:00起算的分钟数。
        """
        hours, minutes = value.split(":", 1)
        return int(hours) * 60 + int(minutes)

    @staticmethod
    def _constraint_closing_minutes(constraints: VisitConstraints) -> int:
        """
        将游览约束的闭馆时刻转换为可跨午夜比较的分钟数。

        Args:
            constraints: 包含闭馆时间和日期偏移的游览约束。

        Returns:
            从开放日期00:00起算的闭馆分钟数。
        """
        closing_time = constraints.closing_time or "22:00"
        return (
            ItineraryOptimizer._time_to_minutes(closing_time)
            + constraints.closing_day_offset * 24 * 60
        )

    @staticmethod
    def _format_constraint_closing_time(constraints: VisitConstraints) -> str:
        """
        格式化包含次日语义的闭馆时间。

        Args:
            constraints: 包含闭馆时间和日期偏移的游览约束。

        Returns:
            当日 HH:MM 或次日 HH:MM 文本。
        """
        prefix = "次日" if constraints.closing_day_offset else ""
        return f"{prefix}{constraints.closing_time or '22:00'}"

    @staticmethod
    def _minutes_to_time(value: int) -> str:
        """
        将当天分钟数转换为 Pydantic 契约接受的 HH:MM。

        Args:
            value: 当天分钟数。

        Returns:
            限制在00:00至23:59的时间文本。
        """
        safe_value = max(0, min(value, 24 * 60 - 1))
        hours, minutes = divmod(safe_value, 60)
        return f"{hours:02d}:{minutes:02d}"

    @staticmethod
    def _degrade_state(state: _TimelineState) -> None:
        """
        在不覆盖不可执行状态的前提下将时间轴标记为降级。

        Args:
            state: 当前时间轴构建状态。
        """
        if state.status == "feasible":
            state.status = "degraded"

    @staticmethod
    def _conflict_state(state: _TimelineState, warning: str) -> None:
        """
        将时间轴标记为不可执行并记录唯一约束告警。

        Args:
            state: 当前时间轴构建状态。
            warning: 面向用户的冲突说明。
        """
        state.status = "infeasible"
        state.warnings.append(warning)
