"""使用固定城市数据集评测行程可执行性。"""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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
from app.services.itinerary_optimizer import (
    FULL_DAY_VISIT_MINUTES,
    MIN_DAILY_VISIT_MINUTES,
    ItineraryOptimizer,
    build_visit_constraints,
)


@dataclass(frozen=True)
class EvaluationMetrics:
    """固定数据集一次运行的可执行性指标。"""

    executable_day_ratio: float
    route_coverage: float
    daily_visit_coverage: float
    timeline_overlap_count: int
    maximum_transfer_minutes: int


class RecordedRouteProvider:
    """读取固定数据集中的已记录路线，避免评测访问真实高德服务。"""

    def __init__(self, dataset: Dict[str, Any]) -> None:
        """
        建立坐标到 POI ID 和路线记录的索引。

        Args:
            dataset: 已加载的固定城市 JSON 数据集。
        """
        self._poi_by_location: Dict[Tuple[float, float], str] = {}
        for day in dataset["days"]:
            for attraction in day["attractions"]:
                location = attraction["location"]
                self._poi_by_location[
                    (float(location["longitude"]), float(location["latitude"]))
                ] = attraction["poi_id"]
        self._routes = {
            (item["origin_poi_id"], item["destination_poi_id"]): item
            for item in dataset["routes"]
        }

    def plan_route_by_coordinates(
        self,
        origin: Location,
        destination: Location,
        route_type: str = "walking",
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        返回与坐标对匹配的已记录路线。

        Args:
            origin: 起点坐标。
            destination: 终点坐标。
            route_type: 用户交通方式。
            origin_city: 起点城市。
            destination_city: 终点城市。

        Returns:
            含距离、时长、轨迹和来源的路线；记录缺失时返回不可达状态。
        """
        origin_id = self._poi_by_location.get((origin.longitude, origin.latitude))
        destination_id = self._poi_by_location.get(
            (destination.longitude, destination.latitude)
        )
        route = self._routes.get((origin_id, destination_id))
        if route is None:
            return {"status": "unreachable"}
        midpoint = {
            "longitude": (origin.longitude + destination.longitude) / 2,
            "latitude": (origin.latitude + destination.latitude) / 2,
        }
        return {
            "distance": route["distance"],
            "duration": route["duration"],
            "route_type": route_type,
            "description": route["description"],
            "geometry": [origin.model_dump(), midpoint, destination.model_dump()],
            "source": FactSource(
                provider="amap_recorded_fixture",
                quality=FactQuality.VERIFIED,
                note="固定数据集中的已记录路线",
            ).model_dump(),
        }


def load_dataset(path: Path) -> Dict[str, Any]:
    """
    读取一个 UTF-8 固定城市评测数据集。

    Args:
        path: 数据集 JSON 文件路径。

    Returns:
        解析后的数据集字典。
    """
    return json.loads(path.read_text(encoding="utf-8"))


def build_plan(dataset: Dict[str, Any]) -> Tuple[TripRequest, TripPlan]:
    """
    将固定城市数据集转换为优化器输入模型。

    Args:
        dataset: 已加载的数据集字典。

    Returns:
        日期合法的请求和待优化旅行计划。
    """
    request = TripRequest.model_validate(dataset["request"])
    days = []
    for day_index, day_data in enumerate(dataset["days"]):
        attractions = []
        for item in day_data["attractions"]:
            source = FactSource(
                provider="amap_recorded_fixture",
                quality=FactQuality.VERIFIED,
                note="固定城市POI数据",
            )
            attractions.append(
                Attraction(
                    name=item["name"],
                    address=item["address"],
                    location=Location.model_validate(item["location"]),
                    visit_duration=item["visit_duration"],
                    description=item["description"],
                    category=item["category"],
                    rating=item["rating"],
                    poi_id=item["poi_id"],
                    source=source,
                    visit_constraints=build_visit_constraints(
                        item["name"],
                        item["category"],
                        raw_opening_hours=item["opening_hours"],
                        verified_source=source,
                    ),
                )
            )
        meals = [
            Meal(
                type=meal_type,
                name=f"第{day_index + 1}天{meal_type}",
                poi_id=f"meal-{day_index}-{meal_type}",
                source=FactSource(
                    provider="amap_recorded_fixture",
                    quality=FactQuality.VERIFIED,
                ),
            )
            for meal_type in ("breakfast", "lunch", "dinner")
        ]
        days.append(
            DayPlan(
                date=day_data["date"],
                day_index=day_index,
                description=day_data["description"],
                transportation=request.transportation,
                accommodation=request.accommodation,
                attractions=attractions,
                meals=meals,
            )
        )

    return request, TripPlan(
        city=request.city,
        start_date=request.start_date,
        end_date=request.end_date,
        days=days,
        overall_suggestions="固定数据集评测",
    )


def evaluate_plan(plan: TripPlan) -> EvaluationMetrics:
    """
    计算路线覆盖、日程覆盖、时间重叠和最大通勤指标。

    Args:
        plan: 已完成确定性优化的旅行计划。

    Returns:
        可用于阈值验收的行程可执行性指标。
    """
    day_count = max(len(plan.days), 1)
    expected_routes = sum(max(len(day.attractions) - 1, 0) for day in plan.days)
    available_routes = sum(
        route.status == "available"
        for day in plan.days
        for route in day.routes
    )
    covered_days = sum(
        day.total_visit_minutes >= MIN_DAILY_VISIT_MINUTES
        or any(
            attraction.visit_duration >= FULL_DAY_VISIT_MINUTES
            for attraction in day.attractions
        )
        for day in plan.days
    )
    overlaps = 0
    for day in plan.days:
        overlaps += sum(
            previous.end_time > current.start_time
            for previous, current in zip(day.timeline, day.timeline[1:])
        )
    maximum_transfer = max(
        (
            round(route.duration / 60)
            for day in plan.days
            for route in day.routes
        ),
        default=0,
    )
    return EvaluationMetrics(
        executable_day_ratio=round(
            sum(day.schedule_status != "infeasible" for day in plan.days)
            / day_count,
            4,
        ),
        route_coverage=round(
            available_routes / expected_routes if expected_routes else 1.0,
            4,
        ),
        daily_visit_coverage=round(covered_days / day_count, 4),
        timeline_overlap_count=overlaps,
        maximum_transfer_minutes=maximum_transfer,
    )


def run_dataset(path: Path) -> Dict[str, Any]:
    """
    运行一个固定城市数据集并判断是否达到声明阈值。

    Args:
        path: 数据集 JSON 文件路径。

    Returns:
        数据集名称、通过状态、指标、阈值和优化结果。
    """
    dataset = load_dataset(path)
    request, plan = build_plan(dataset)
    optimizer = ItineraryOptimizer(
        RecordedRouteProvider(dataset),
        max_route_calls=dataset.get("route_call_budget", 18),
    )
    optimized = optimizer.optimize(plan, request)
    metrics = evaluate_plan(optimized)
    thresholds = dataset["thresholds"]
    passed = (
        metrics.executable_day_ratio >= thresholds["executable_day_ratio"]
        and metrics.route_coverage >= thresholds["route_coverage"]
        and metrics.daily_visit_coverage >= thresholds["daily_visit_coverage"]
        and metrics.timeline_overlap_count <= thresholds["timeline_overlap_count"]
        and metrics.maximum_transfer_minutes
        <= thresholds["maximum_transfer_minutes"]
    )
    return {
        "dataset": dataset["name"],
        "passed": passed,
        "metrics": asdict(metrics),
        "thresholds": thresholds,
        "selected_groups": [
            [attraction.poi_id for attraction in day.attractions]
            for day in optimized.days
        ],
    }


def main() -> int:
    """
    运行默认北京数据集并打印机器可读评测报告。

    Returns:
        达到全部阈值时返回0，否则返回1。
    """
    dataset_path = Path(__file__).parent / "datasets" / "beijing_3_day_v1.json"
    report = run_dataset(dataset_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
