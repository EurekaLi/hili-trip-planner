"""从最终行程事实确定性计算预算。"""

from datetime import datetime, timezone
from math import ceil
from typing import List

from ..models.schemas import (
    Budget,
    FactQuality,
    FactSource,
    RouteLeg,
    TripPlan,
)


def estimate_route_cost(route: RouteLeg) -> int:
    """
    根据路线距离和交通类型估算单段市内交通费用。

    Args:
        route: 包含距离和交通类型的路线段。

    Returns:
        以人民币元计的保守整数估算值。
    """
    if route.route_type == "walking":
        return 0
    if route.route_type == "transit":
        return 6
    distance_km = max(route.distance, 0) / 1000
    return max(13, ceil(13 + max(distance_km - 3, 0) * 2.3))


def recalculate_budget(trip_plan: TripPlan) -> Budget:
    """
    从最终景点、住宿、餐饮和路线明细重算预算。

    Args:
        trip_plan: 已完成事实回填和路线刷新的旅行计划。

    Returns:
        各分项与总额严格一致的预算模型。
    """
    attraction_total = 0
    hotel_total = 0
    meal_total = 0
    transportation_total = 0
    unknown_items: List[str] = []

    for day in trip_plan.days:
        for attraction in day.attractions:
            attraction_total += max(attraction.ticket_price, 0)
            if (
                attraction.ticket_price_source is None
                or attraction.ticket_price_source.quality == FactQuality.UNKNOWN
            ):
                unknown_items.append(f"{attraction.name}门票")

        if day.hotel is not None:
            hotel_total += max(day.hotel.estimated_cost, 0)
            if (
                day.hotel.cost_source is None
                or day.hotel.cost_source.quality == FactQuality.UNKNOWN
            ):
                unknown_items.append(f"{day.hotel.name}住宿")

        for meal in day.meals:
            meal_total += max(meal.estimated_cost, 0)
            if meal.cost_source is None or meal.cost_source.quality == FactQuality.UNKNOWN:
                unknown_items.append(f"{day.date}{meal.type}餐费")

        for route in day.routes:
            transportation_total += estimate_route_cost(route)

        expected_routes = max(len(day.attractions) - 1, 0)
        if len(day.routes) < expected_routes:
            unknown_items.append(f"{day.date}缺失路线交通费")

    total = attraction_total + hotel_total + meal_total + transportation_total
    return Budget(
        total_attractions=attraction_total,
        total_hotels=hotel_total,
        total_meals=meal_total,
        total_transportation=transportation_total,
        total=total,
        source=FactSource(
            provider="backend_calculator",
            quality=FactQuality.ESTIMATED,
            retrieved_at=datetime.now(timezone.utc),
            note="根据最终明细确定性求和；单项价格可能为估算或未知",
        ),
        unknown_cost_items=list(dict.fromkeys(unknown_items)),
    )
