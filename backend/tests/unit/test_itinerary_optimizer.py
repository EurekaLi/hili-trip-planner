"""确定性时间表、路线几何和跨天分组优化测试。"""

from typing import Dict, Optional, Tuple

from app.models.schemas import (
    Attraction,
    DayPlan,
    FactQuality,
    FactSource,
    Location,
    Meal,
    TripPlan,
    TripRequest,
)
from app.services.amap_service import AmapService
from app.services.itinerary_optimizer import (
    ItineraryOptimizer,
    build_visit_constraints,
)


class FakeRouteProvider:
    """按坐标距离返回固定真实路线的地图服务替身。"""

    def __init__(
        self,
        unreachable: Optional[Tuple[float, float]] = None,
    ) -> None:
        """
        初始化调用记录和可选不可达坐标对。

        Args:
            unreachable: 起终点经度组成的不可达路线标识。
        """
        self.calls: list[Tuple[float, float]] = []
        self.unreachable = unreachable

    def plan_route_by_coordinates(
        self,
        origin: Location,
        destination: Location,
        route_type: str = "walking",
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
    ) -> Dict:
        """
        对近距离坐标返回十分钟路线，对跨区域坐标返回两小时路线。

        Args:
            origin: 起点坐标。
            destination: 终点坐标。
            route_type: 用户交通方式。
            origin_city: 起点城市。
            destination_city: 终点城市。

        Returns:
            含真实形态轨迹、距离、时长和来源的路线字典。
        """
        key = (origin.longitude, destination.longitude)
        self.calls.append(key)
        if self.unreachable == key:
            return {"status": "unreachable"}

        longitude_delta = abs(destination.longitude - origin.longitude)
        duration = 600 if longitude_delta < 0.1 else 7_200
        distance = 1_500 if longitude_delta < 0.1 else 40_000
        midpoint = Location(
            longitude=(origin.longitude + destination.longitude) / 2,
            latitude=(origin.latitude + destination.latitude) / 2,
        )
        return {
            "distance": distance,
            "duration": duration,
            "route_type": route_type,
            "description": "测试路线",
            "geometry": [
                origin.model_dump(),
                midpoint.model_dump(),
                destination.model_dump(),
            ],
            "source": FactSource(
                provider="amap",
                quality=FactQuality.VERIFIED,
            ).model_dump(),
        }


def build_attraction(
    poi_id: str,
    longitude: float,
    duration: int = 180,
    name: Optional[str] = None,
    rating: float = 4.0,
) -> Attraction:
    """
    构造具有稳定坐标、时长和约束的测试景点。

    Args:
        poi_id: 景点唯一标识。
        longitude: 用于制造近距离或跨区域关系的经度。
        duration: 建议游览分钟数。
        name: 可选景点名称。
        rating: 用于确定地理链起点的评分。

    Returns:
        可直接交给行程优化器的景点。
    """
    attraction_name = name or poi_id
    return Attraction(
        name=attraction_name,
        address=f"{attraction_name}地址",
        location=Location(longitude=longitude, latitude=39.9),
        visit_duration=duration,
        description="测试景点",
        category="风景名胜",
        rating=rating,
        poi_id=poi_id,
        visit_constraints=build_visit_constraints(
            attraction_name,
            "风景名胜",
            raw_opening_hours="09:00-22:00",
            verified_source=FactSource(
                provider="amap",
                quality=FactQuality.VERIFIED,
            ),
        ),
    )


def build_meals(day_index: int) -> list[Meal]:
    """
    构造一天早中晚三个已验证餐厅槽位。

    Args:
        day_index: 用于生成唯一餐厅标识的日期序号。

    Returns:
        早餐、午餐和晚餐列表。
    """
    return [
        Meal(type=meal_type, name=f"餐厅{day_index}-{meal_type}", poi_id=f"m-{day_index}-{meal_type}")
        for meal_type in ("breakfast", "lunch", "dinner")
    ]


def build_request(days: int = 2) -> TripRequest:
    """
    构造与测试天数一致的北京公共交通请求。

    Args:
        days: 旅行天数，支持当前测试使用的一天或两天。

    Returns:
        已通过 Pydantic 日期跨度校验的请求。
    """
    end_date = "2026-09-16" if days == 2 else "2026-09-15"
    return TripRequest(
        city="北京",
        start_date="2026-09-15",
        end_date=end_date,
        travel_days=days,
        transportation="公共交通",
        accommodation="经济型酒店",
    )


def build_plan(day_attractions: list[list[Attraction]]) -> TripPlan:
    """
    根据逐日景点构造可供优化的旅行计划。

    Args:
        day_attractions: 按天排列的景点列表。

    Returns:
        包含真实餐厅槽位的测试旅行计划。
    """
    days = []
    for index, attractions in enumerate(day_attractions):
        days.append(
            DayPlan(
                date=f"2026-09-{15 + index:02d}",
                day_index=index,
                description="测试行程",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=attractions,
                meals=build_meals(index),
            )
        )
    return TripPlan(
        city="北京",
        start_date="2026-09-15",
        end_date=f"2026-09-{14 + len(days):02d}",
        days=days,
        overall_suggestions="测试建议",
    )


def test_amap_route_parser_preserves_step_geometry() -> None:
    """高德路线解析器应合并并去重 step polyline 坐标。"""
    service = AmapService.__new__(AmapService)

    route = service._parse_route_result(
        {
            "route": {
                "paths": [
                    {
                        "distance": "1200",
                        "duration": "600",
                        "steps": [
                            {"instruction": "向东", "polyline": "116.1,39.9;116.2,39.9"},
                            {"instruction": "到达", "polyline": "116.2,39.9;116.3,39.9"},
                        ],
                    }
                ]
            }
        },
        "walking",
    )

    assert route["geometry"] == [
        {"longitude": 116.1, "latitude": 39.9},
        {"longitude": 116.2, "latitude": 39.9},
        {"longitude": 116.3, "latitude": 39.9},
    ]


def test_verified_opening_hours_keep_source_and_parse_closed_weekday() -> None:
    """高德营业时间应保留真实来源并解析周一闭馆约束。"""
    source = FactSource(provider="amap", quality=FactQuality.VERIFIED)

    constraints = build_visit_constraints(
        "中国国家博物馆",
        "博物馆",
        "周二至周日 09:00-17:30，周一闭馆",
        source,
    )

    assert constraints.opening_time == "09:00"
    assert constraints.closing_time == "17:30"
    assert constraints.closed_weekdays == [0]
    assert constraints.opening_hours_source.quality == FactQuality.VERIFIED
    assert constraints.reservation_required is True


def test_overnight_opening_hours_are_normalized_without_losing_source() -> None:
    """跨午夜营业时间应显式记录次日偏移而不是触发模型校验失败。"""
    source = FactSource(provider="amap", quality=FactQuality.VERIFIED)

    constraints = build_visit_constraints(
        "夜间餐厅",
        "餐饮服务",
        "11:00-02:00",
        source,
    )

    assert constraints.opening_time == "11:00"
    assert constraints.closing_time == "02:00"
    assert constraints.closing_day_offset == 1
    assert constraints.opening_hours_source == source


def test_equal_opening_hours_are_treated_as_twenty_four_hours() -> None:
    """高德常见的00:00-00:00应按24小时营业处理。"""
    constraints = build_visit_constraints(
        "二十四小时便利店",
        "购物服务",
        "00:00-00:00",
    )

    assert constraints.closing_day_offset == 1
    assert (
        ItineraryOptimizer._constraint_closing_minutes(constraints)
        - ItineraryOptimizer._time_to_minutes(constraints.opening_time)
    ) == 24 * 60


def test_explicit_next_day_marker_is_preserved() -> None:
    """显式包含次日的高德营业时间应转换为跨日窗口。"""
    constraints = build_visit_constraints(
        "深夜书店",
        "购物服务",
        "周一至周日 18:00-次日01:00",
    )

    assert constraints.opening_time == "18:00"
    assert constraints.closing_time == "01:00"
    assert constraints.closing_day_offset == 1
    assert constraints.raw_opening_hours == "周一至周日 18:00-次日01:00"


def test_invalid_external_opening_duration_uses_estimated_fallback() -> None:
    """超过24小时的外部窗口应保留原文并回退，不能中断整批POI解析。"""
    source = FactSource(provider="amap", quality=FactQuality.VERIFIED)

    constraints = build_visit_constraints(
        "异常营业时间景点",
        "风景名胜",
        "08:00-次日18:00",
        source,
    )

    assert constraints.opening_time == "09:00"
    assert constraints.closing_time == "18:00"
    assert constraints.closing_day_offset == 0
    assert constraints.raw_opening_hours == "08:00-次日18:00"
    assert constraints.opening_hours_source.quality == FactQuality.ESTIMATED


def test_real_route_time_selects_geographic_day_groups() -> None:
    """真实通勤代价更低时应把相邻景点重新分配到同一天。"""
    attraction_a = build_attraction("A", 116.00, rating=5.0)
    attraction_b = build_attraction("B", 116.01, rating=4.0)
    attraction_c = build_attraction("C", 117.00, rating=3.0)
    attraction_d = build_attraction("D", 117.01, rating=2.0)
    plan = build_plan([[attraction_a, attraction_c], [attraction_b, attraction_d]])
    provider = FakeRouteProvider()

    result = ItineraryOptimizer(provider, max_route_calls=10).optimize(
        plan,
        build_request(),
    )

    assert [[item.poi_id for item in day.attractions] for day in result.days] == [
        ["A", "B"],
        ["C", "D"],
    ]
    assert all(route.duration == 600 for day in result.days for route in day.routes)
    assert all(len(route.geometry) == 3 for day in result.days for route in day.routes)
    assert any("真实路线耗时" in warning for warning in result.warnings)


def test_evening_attraction_starts_after_dinner_and_timeline_does_not_overlap() -> None:
    """夜游地点应排在晚餐后，且全部时间轴条目保持单调不重叠。"""
    daytime = build_attraction("day", 116.0, duration=180, rating=5.0)
    evening = build_attraction(
        "night",
        116.01,
        duration=120,
        name="什刹海夜景",
        rating=4.0,
    )
    plan = build_plan([[evening, daytime]])

    result = ItineraryOptimizer(FakeRouteProvider()).optimize(
        plan,
        build_request(days=1),
    )

    day = result.days[0]
    assert [item.poi_id for item in day.attractions] == ["day", "night"]
    night_entries = [
        item for item in day.timeline
        if item.kind == "attraction" and item.poi_id == "night"
    ]
    assert night_entries[0].start_time >= "19:00"
    assert all(
        previous.end_time <= current.start_time
        for previous, current in zip(day.timeline, day.timeline[1:])
    )


def test_unreachable_route_marks_day_infeasible_with_explicit_reason() -> None:
    """明确不可达的相邻地点必须保留路线占位并把当天标记为不可执行。"""
    origin = build_attraction("origin", 116.0, rating=5.0)
    destination = build_attraction("destination", 116.01, rating=4.0)
    plan = build_plan([[origin, destination]])
    provider = FakeRouteProvider(unreachable=(116.0, 116.01))

    result = ItineraryOptimizer(provider).optimize(
        plan,
        build_request(days=1),
    )

    day = result.days[0]
    assert day.routes[0].status == "unavailable"
    assert day.routes[0].fallback_reason == "地图服务未返回可达路线"
    assert day.schedule_status == "infeasible"
    assert any("不可达" in warning for warning in result.warnings)


def test_route_call_budget_uses_explicit_estimate_after_limit() -> None:
    """超过单任务路线预算后仍应返回带原因的估算路线而不是继续付费调用。"""
    attractions = [
        build_attraction("A", 116.00, rating=5.0),
        build_attraction("B", 116.01, rating=4.0),
        build_attraction("C", 116.02, rating=3.0),
    ]
    provider = FakeRouteProvider()

    result = ItineraryOptimizer(provider, max_route_calls=1).optimize(
        build_plan([attractions]),
        build_request(days=1),
    )

    assert len(provider.calls) == 1
    assert result.days[0].routes[1].status == "estimated"
    assert result.days[0].routes[1].fallback_reason == "路线调用预算已用尽"


def test_full_day_attraction_occupies_day_alone() -> None:
    """游览时长达到全天阈值的地点不能与普通地点并排进同一天。"""
    full_day = build_attraction("park", 116.0, duration=480, name="环球影城", rating=5.0)
    extra = build_attraction("extra", 116.01, duration=120, rating=4.0)
    plan = build_plan([[full_day, extra]])

    result = ItineraryOptimizer(FakeRouteProvider()).optimize(
        plan,
        build_request(days=1),
    )

    assert [item.poi_id for item in result.days[0].attractions] == ["park"]
    assert result.days[0].total_visit_minutes == 480
