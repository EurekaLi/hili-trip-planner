"""多 Agent 旅行规划器真实阶段进度测试。"""

from types import SimpleNamespace
from unittest.mock import Mock

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import Attraction, DayPlan, Location, TripPlan, TripRequest


class FakePlannerAgent:
    """不访问外部 LLM 的 Planner Agent 测试替身。"""

    name = "fake-planner"

    def run(self, query: str, **kwargs) -> str:
        """
        返回供已替换解析器接收的固定字符串。

        Args:
            query: 规划器构造的提示词。
            **kwargs: 规划器传递的 LLM 调用参数。

        Returns:
            固定的测试响应字符串。
        """
        return "{}"


def build_request() -> TripRequest:
    """
    构造进度阶段测试使用的合法旅行请求。

    Returns:
        单日北京公共交通旅行请求。
    """
    return TripRequest(
        city="北京",
        start_date="2026-09-15",
        end_date="2026-09-15",
        travel_days=1,
        transportation="公共交通",
        accommodation="经济型酒店",
    )


def build_plan() -> TripPlan:
    """
    构造能够通过规划器有效景点检查的最小旅行计划。

    Returns:
        包含一个真实形态景点的单日计划。
    """
    attraction = Attraction(
        name="故宫博物院",
        address="景山前街4号",
        location=Location(longitude=116.397029, latitude=39.917839),
        visit_duration=240,
        description="测试景点",
        poi_id="B000A8UIN8",
    )
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
                attractions=[attraction],
            )
        ],
        overall_suggestions="测试建议",
    )


def return_trip_plan(trip_plan: TripPlan, **kwargs) -> TripPlan:
    """
    模拟不改变计划内容的事实回填、时间补足或路线阶段。

    Args:
        trip_plan: 上一阶段生成的旅行计划。
        **kwargs: 被替换方法接收但本测试无需使用的其他参数。

    Returns:
        未修改的输入旅行计划。
    """
    return trip_plan


def test_plan_trip_reports_all_deterministic_stage_boundaries() -> None:
    """规划器应按稳定顺序报告八个真实业务阶段。"""
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.planner_agent = FakePlannerAgent()
    planner.amap_service = Mock()
    planner.amap_service.begin_call_trace.return_value = ([], object())
    planner._load_grounding_data = Mock(return_value=([], [], [], []))
    planner._run_control_agents = Mock(
        return_value=SimpleNamespace(degraded_agents=[])
    )
    planner._build_planner_query = Mock(return_value="test planner query")
    planner._parse_response = Mock(return_value=build_plan())
    planner._ground_trip_plan = Mock(side_effect=return_trip_plan)
    planner._ensure_daily_time_coverage = Mock(side_effect=return_trip_plan)
    planner._optimize_itinerary = Mock(side_effect=return_trip_plan)
    planner._recalculate_budget = Mock(return_value=None)
    planner._build_agent_trace = Mock(return_value=[])
    planner._build_llm_external_calls = Mock(return_value=[])
    reported_stages: list[str] = []

    def capture_progress(stage: str, progress: int, message: str) -> None:
        """
        保存规划器报告的阶段名称供顺序断言使用。

        Args:
            stage: 稳定的业务阶段名称。
            progress: 阶段对应的真实进度。
            message: 面向用户的阶段说明。
        """
        reported_stages.append(stage)

    result = planner.plan_trip(build_request(), capture_progress)

    assert result.city == "北京"
    assert reported_stages == [
        "loading_grounding_data",
        "running_control_agents",
        "running_planner",
        "validating_plan",
        "grounding_plan",
        "filling_schedule",
        "enriching_weather",
        "optimizing_itinerary",
    ]
