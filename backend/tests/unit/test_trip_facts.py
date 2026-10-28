"""旅行事实来源、日期覆盖和确定性预算测试。"""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.agents.contracts import (
    AttractionControlDecision,
    ControlAgentDecisions,
    HotelControlDecision,
    WeatherControlDecision,
)
from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import (
    AgentRunMetrics,
    Attraction,
    DayPlan,
    FactQuality,
    FactSource,
    Hotel,
    Location,
    Meal,
    POIInfo,
    RouteLeg,
    TripPlan,
    TripRequest,
    VisitConstraints,
    WeatherInfo,
)


def build_request() -> TripRequest:
    """
    构造两天旅行事实测试使用的合法请求。

    Returns:
        日期跨度与旅行天数一致的北京请求。
    """
    return TripRequest(
        city="北京",
        start_date="2026-09-15",
        end_date="2026-09-16",
        travel_days=2,
        transportation="公共交通",
        accommodation="经济型酒店",
    )


def build_source(
    provider: str,
    quality: FactQuality,
) -> FactSource:
    """
    构造测试实体使用的事实来源。

    Args:
        provider: 测试来源提供方。
        quality: 测试事实可信类型。

    Returns:
        不包含抓取时间的来源对象。
    """
    return FactSource(provider=provider, quality=quality)


def build_restaurant(index: int) -> POIInfo:
    """
    构造带真实来源的餐厅候选。

    Args:
        index: 用于生成唯一ID和坐标的序号。

    Returns:
        可用于每日餐饮回填的高德POI。
    """
    return POIInfo(
        id=f"restaurant-{index}",
        name=f"真实餐厅{index}",
        type="餐饮服务",
        address=f"测试路{index}号",
        location=Location(longitude=116.4 + index / 100, latitude=39.9),
        rating=4.5,
        source=build_source("amap", FactQuality.VERIFIED),
    )


def test_trip_request_rejects_inconsistent_date_span() -> None:
    """请求声明天数与起止日期不一致时应在进入任务队列前失败。"""
    with pytest.raises(ValidationError, match="travel_days应为3"):
        TripRequest(
            city="北京",
            start_date="2026-09-15",
            end_date="2026-09-17",
            travel_days=2,
            transportation="公共交通",
            accommodation="经济型酒店",
        )


def test_weather_is_filtered_to_trip_dates_and_reports_gaps() -> None:
    """天气结果只能覆盖旅行日期，并明确返回缺失日期。"""
    weather = [
        WeatherInfo(date="2026-09-14", day_weather="晴"),
        WeatherInfo(date="2026-09-15", day_weather="多云"),
    ]

    filtered, missing = MultiAgentTripPlanner._filter_weather_for_request(
        request=build_request(),
        weather=weather,
    )

    assert [item.date for item in filtered] == ["2026-09-15"]
    assert missing == ["2026-09-16"]


def test_meals_are_replaced_with_verified_restaurant_candidates() -> None:
    """Planner 编造的餐厅身份应被真实候选替换，同时保留费用估算标签。"""
    plan = TripPlan(
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
                meals=[
                    Meal(type="breakfast", name="虚构早餐", estimated_cost=20),
                    Meal(type="lunch", name="虚构午餐", estimated_cost=50),
                    Meal(type="dinner", name="虚构晚餐", estimated_cost=80),
                ],
            )
        ],
        overall_suggestions="测试建议",
    )
    restaurants = [build_restaurant(index) for index in range(1, 4)]

    result = MultiAgentTripPlanner._ground_meals(plan, restaurants)

    meals = result.days[0].meals
    assert [meal.poi_id for meal in meals] == [
        "restaurant-1",
        "restaurant-2",
        "restaurant-3",
    ]
    assert all(meal.source.quality == FactQuality.VERIFIED for meal in meals)
    assert all(meal.cost_source.quality == FactQuality.ESTIMATED for meal in meals)


def test_budget_is_recalculated_from_final_details() -> None:
    """后端预算应覆盖模型汇总，并列出缺少价格来源的明细。"""
    verified = build_source("amap", FactQuality.VERIFIED)
    estimated = build_source("planner_agent", FactQuality.ESTIMATED)
    unknown = build_source("unknown", FactQuality.UNKNOWN)
    plan = TripPlan(
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
                hotel=Hotel(
                    name="真实酒店",
                    estimated_cost=200,
                    source=verified,
                    cost_source=estimated,
                ),
                attractions=[
                    Attraction(
                        name="收费景点",
                        address="地址1",
                        location=Location(longitude=116.4, latitude=39.9),
                        visit_duration=180,
                        description="测试",
                        ticket_price=60,
                        source=verified,
                        ticket_price_source=estimated,
                    ),
                    Attraction(
                        name="未知票价景点",
                        address="地址2",
                        location=Location(longitude=116.5, latitude=39.9),
                        visit_duration=180,
                        description="测试",
                        ticket_price=0,
                        source=verified,
                        ticket_price_source=unknown,
                    ),
                ],
                meals=[
                    Meal(type="breakfast", name="早餐", estimated_cost=25, cost_source=estimated),
                    Meal(type="lunch", name="午餐", estimated_cost=60, cost_source=estimated),
                    Meal(type="dinner", name="晚餐", estimated_cost=80, cost_source=estimated),
                ],
                routes=[
                    RouteLeg(
                        origin_poi_id="a",
                        destination_poi_id="b",
                        origin_name="收费景点",
                        destination_name="未知票价景点",
                        distance=1000,
                        duration=600,
                        route_type="transit",
                        description="乘坐地铁",
                        source=verified,
                    )
                ],
            )
        ],
        overall_suggestions="测试建议",
    )
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)

    budget = planner._recalculate_budget(plan)

    assert budget.total_attractions == 60
    assert budget.total_hotels == 200
    assert budget.total_meals == 165
    assert budget.total_transportation == 6
    assert budget.total == 431
    assert budget.unknown_cost_items == ["未知票价景点门票"]


def test_agent_decisions_are_converted_to_persistable_trace() -> None:
    """三个控制决策和 Planner 指标应形成固定顺序的结构化轨迹。"""
    metrics = AgentRunMetrics(
        controller="planner",
        agent_name="测试Planner",
        succeeded=True,
        elapsed_seconds=1.2,
        input_chars=100,
        output_chars=200,
        model="test-model",
    )
    decisions = ControlAgentDecisions(
        attraction=AttractionControlDecision(selected_poi_ids=["a"]),
        weather=WeatherControlDecision(),
        hotel=HotelControlDecision(selected_hotel_ids=["h"]),
    )

    traces = MultiAgentTripPlanner._build_agent_trace(decisions, metrics)

    assert [trace.controller for trace in traces] == [
        "attraction",
        "weather",
        "hotel",
        "planner",
    ]
    assert traces[0].decision["selected_poi_ids"] == ["a"]
    assert traces[-1].metrics.model == "test-model"


def test_fact_provenance_serializes_for_api_response() -> None:
    """事实来源和外部调用时间应能序列化为 JSON API 数据。"""
    retrieved_at = datetime(2026, 9, 15, 8, 30, tzinfo=timezone.utc)
    plan = TripPlan(
        city="北京",
        start_date="2026-09-15",
        end_date="2026-09-15",
        days=[
            DayPlan(
                date="2026-09-15",
                day_index=0,
                description="测试行程",
                transportation="步行",
                accommodation="经济型酒店",
                attractions=[
                    Attraction(
                        name="真实景点",
                        address="测试路1号",
                        location=Location(longitude=116.4, latitude=39.9),
                        visit_duration=180,
                        description="测试",
                        source=FactSource(
                            provider="amap",
                            quality=FactQuality.VERIFIED,
                            retrieved_at=retrieved_at,
                        ),
                        visit_constraints=VisitConstraints(
                            opening_time="11:00",
                            closing_time="02:00",
                            closing_day_offset=1,
                            opening_hours_source=FactSource(
                                provider="amap",
                                quality=FactQuality.VERIFIED,
                                retrieved_at=retrieved_at,
                            ),
                        ),
                    )
                ],
            )
        ],
        overall_suggestions="测试建议",
    )

    encoded = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    payload = TripPlan.model_validate_json(encoded).model_dump(mode="json")

    source = payload["days"][0]["attractions"][0]["source"]
    assert source["quality"] == "verified"
    assert source["retrieved_at"] == "2026-09-15T08:30:00Z"
    constraints = payload["days"][0]["attractions"][0]["visit_constraints"]
    assert constraints["closing_day_offset"] == 1
    assert (
        constraints["opening_hours_source"]["retrieved_at"]
        == "2026-09-15T08:30:00Z"
    )


def test_same_day_opening_window_rejects_reverse_clock_order() -> None:
    """未声明次日偏移的倒序营业窗口必须被契约拒绝。"""
    with pytest.raises(ValidationError, match="营业时间必须正向推进"):
        VisitConstraints(
            opening_time="11:00",
            closing_time="02:00",
        )


def test_planner_poi_prompt_serializes_nested_fact_timestamps() -> None:
    """Planner候选POI中的嵌套来源时间必须转换为JSON字符串。"""
    retrieved_at = datetime(2026, 9, 16, 1, 30, tzinfo=timezone.utc)
    poi = POIInfo(
        id="poi-a",
        name="夜间景点",
        type="风景名胜",
        address="测试路1号",
        location=Location(longitude=116.4, latitude=39.9),
        visit_constraints=VisitConstraints(
            opening_time="11:00",
            closing_time="02:00",
            closing_day_offset=1,
            opening_hours_source=FactSource(
                provider="amap",
                quality=FactQuality.VERIFIED,
                retrieved_at=retrieved_at,
            ),
        ),
    )

    payload = json.loads(
        MultiAgentTripPlanner._serialize_pois_for_agents([poi])
    )

    assert (
        payload[0]["visit_constraints"]["opening_hours_source"]["retrieved_at"]
        == "2026-09-16T01:30:00Z"
    )


def test_planner_query_serializes_weather_fact_timestamp() -> None:
    """Planner提示词中的天气来源时间必须使用JSON模式编码。"""
    weather = WeatherInfo(
        date="2026-09-15",
        day_weather="晴",
        night_weather="晴",
        day_temp=25,
        night_temp=15,
        source=FactSource(
            provider="amap",
            quality=FactQuality.VERIFIED,
            retrieved_at=datetime(2026, 9, 16, 1, 30, tzinfo=timezone.utc),
        ),
    )
    decisions = ControlAgentDecisions(
        attraction=AttractionControlDecision(),
        weather=WeatherControlDecision(),
        hotel=HotelControlDecision(),
    )

    query = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)._build_planner_query(
        request=build_request(),
        attractions=[],
        weather=[weather],
        hotels=[],
        restaurants=[],
        decisions=decisions,
    )

    assert "2026-09-16T01:30:00Z" in query
