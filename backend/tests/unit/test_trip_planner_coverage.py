"""旅行天数候选规模与每日时长补位规则测试。"""

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import (
    Attraction,
    DayPlan,
    Location,
    POIInfo,
    TripPlan,
    TripRequest,
)


def build_poi(
        poi_id: str,
        name: str,
        duration: int,
        longitude: float,
        rating: float = 4.5,
) -> POIInfo:
    """
    构造包含真实坐标和建议时长的测试 POI。

    Args:
        poi_id: 测试 POI ID。
        name: 景点名称。
        duration: 建议游览分钟数。
        longitude: 用于邻近排序的经度。
        rating: 高德评分。

    Returns:
        可用于补位逻辑的 POI 模型。
    """
    return POIInfo(
        id=poi_id,
        name=name,
        type="风景名胜",
        address=f"{name}地址",
        location=Location(longitude=longitude, latitude=39.9),
        rating=rating,
        suggested_visit_duration=duration,
    )


def build_attraction(poi: POIInfo) -> Attraction:
    """
    将测试 POI 转换为已回填的行程景点。

    Args:
        poi: 需要放入每日行程的测试 POI。

    Returns:
        已关联 POI ID 的景点模型。
    """
    return Attraction(
        name=poi.name,
        address=poi.address,
        location=poi.location,
        visit_duration=poi.suggested_visit_duration,
        description="测试景点",
        poi_id=poi.id,
    )


def build_trip_plan(attractions: list[Attraction]) -> TripPlan:
    """
    构造仅包含一天的测试旅行计划。

    Args:
        attractions: 首日已经安排的景点列表。

    Returns:
        可用于每日时长校验的旅行计划。
    """
    return TripPlan(
        city="北京",
        start_date="2026-09-15",
        end_date="2026-09-15",
        days=[
            DayPlan(
                date="2026-09-15",
                day_index=0,
                description="测试行程",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=attractions,
            ),
        ],
        overall_suggestions="测试建议",
    )


def build_request(preferences: list[str]) -> TripRequest:
    """
    构造包含指定可选偏好的测试旅行请求。

    Args:
        preferences: 用户选择的偏好标签。

    Returns:
        三天北京旅行请求。
    """
    return TripRequest(
        city="北京",
        start_date="2026-09-15",
        end_date="2026-09-17",
        travel_days=3,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=preferences,
    )


def test_candidate_target_scales_with_travel_days():
    """验证真实 POI 候选数量随旅行天数增长并受上限保护。"""
    assert MultiAgentTripPlanner._calculate_candidate_target(1) == 8
    assert MultiAgentTripPlanner._calculate_candidate_target(3) == 12
    assert MultiAgentTripPlanner._calculate_candidate_target(30) == 40


def test_preferences_are_optional_and_keep_popular_fallbacks():
    """验证无偏好仍搜索热门类别，有偏好时只改变搜索优先级。"""
    no_preference = MultiAgentTripPlanner._build_attraction_search_keywords(
        build_request([]),
    )
    history_first = MultiAgentTripPlanner._build_attraction_search_keywords(
        build_request(["历史文化"]),
    )

    assert no_preference[:2] == ["景点", "博物馆"]
    assert history_first[0] == "历史景点"
    assert "景点" in history_first


def test_full_day_attraction_can_stand_alone():
    """验证全天型景点达到时长目标后不会再自动添加景点。"""
    universal = build_poi(
        poi_id="universal",
        name="北京环球度假区",
        duration=480,
        longitude=116.67,
    )
    spare = build_poi(
        poi_id="spare",
        name="城市公园",
        duration=180,
        longitude=116.68,
    )
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    trip_plan = build_trip_plan([
        build_attraction(universal),
        build_attraction(spare),
    ])

    result = planner._ensure_daily_time_coverage(
        trip_plan=trip_plan,
        attractions=[universal, spare],
    )

    assert [item.poi_id for item in result.days[0].attractions] == ["universal"]


def test_short_attraction_is_filled_with_nearby_candidates():
    """验证短时景点会用未使用的邻近真实候选补足一天。"""
    shichahai = build_poi(
        poi_id="shichahai",
        name="什刹海",
        duration=120,
        longitude=116.38,
    )
    nearby_museum = build_poi(
        poi_id="museum",
        name="附近博物馆",
        duration=180,
        longitude=116.381,
    )
    nearby_park = build_poi(
        poi_id="park",
        name="附近公园",
        duration=180,
        longitude=116.382,
    )
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    trip_plan = build_trip_plan([build_attraction(shichahai)])

    result = planner._ensure_daily_time_coverage(
        trip_plan=trip_plan,
        attractions=[shichahai, nearby_museum, nearby_park],
    )

    total_minutes = sum(
        item.visit_duration for item in result.days[0].attractions
    )
    assert total_minutes >= 420
    assert [
        item.poi_id for item in result.days[0].attractions
    ] == ["museum", "park", "shichahai"]
