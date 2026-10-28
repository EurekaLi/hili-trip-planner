"""结构化控制 Agent 契约与降级行为测试。"""

import json
from datetime import datetime, timezone
from typing import Optional

from app.agents.contracts import (
    ControlAgentDecisions,
    WeatherControlDecision,
    parse_control_decision,
)
from app.agents.control_orchestrator import ControlAgentOrchestrator
from app.models.schemas import (
    FactQuality,
    FactSource,
    Location,
    POIInfo,
    TripRequest,
    VisitConstraints,
    WeatherInfo,
)


class FakeControlAgent:
    """返回固定文本或异常的控制 Agent 测试替身。"""

    def __init__(
            self,
            name: str,
            response: str = "",
            error: Optional[Exception] = None,
    ) -> None:
        """
        初始化测试 Agent 的名称、响应和可选异常。

        Args:
            name: Agent 显示名称。
            response: 调用成功时返回的固定文本。
            error: 调用时需要抛出的异常。
        """
        self.name = name
        self.response = response
        self.error = error

    def run(self, query: str, **kwargs) -> str:
        """
        返回固定响应或抛出预设异常。

        Args:
            query: 控制器生成的当前请求输入。
            **kwargs: LLM 调用参数。

        Returns:
            初始化时提供的固定响应。

        Raises:
            Exception: 初始化时配置了异常时抛出。
        """
        if self.error is not None:
            raise self.error
        return self.response


def build_request(travel_days: int = 2) -> TripRequest:
    """
    构造控制 Agent 测试使用的旅行请求。

    Args:
        travel_days: 请求中的旅行天数。

    Returns:
        日期范围与天数一致的北京旅行请求。
    """
    return TripRequest(
        city="北京",
        start_date="2026-09-15",
        end_date="2026-09-16",
        travel_days=travel_days,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
    )


def build_poi(poi_id: str, name: str) -> POIInfo:
    """
    构造控制 Agent 白名单校验使用的 POI。

    Args:
        poi_id: 测试 POI ID。
        name: 测试 POI 名称。

    Returns:
        包含有效坐标的 POI 模型。
    """
    return POIInfo(
        id=poi_id,
        name=name,
        type="风景名胜",
        address=f"{name}地址",
        location=Location(longitude=116.4, latitude=39.9),
    )


def build_weather(date: str) -> WeatherInfo:
    """
    构造天气日期白名单使用的真实天气记录。

    Args:
        date: 天气预报日期。

    Returns:
        指定日期的天气模型。
    """
    return WeatherInfo(
        date=date,
        day_weather="晴",
        night_weather="晴",
        day_temp=25,
        night_temp=15,
        source=FactSource(
            provider="amap",
            quality=FactQuality.VERIFIED,
            retrieved_at=datetime(2026, 9, 16, tzinfo=timezone.utc),
        ),
    )


def test_parse_control_decision_accepts_markdown_json_block():
    """验证契约解析器可以处理带说明文字的 JSON 代码块。"""
    response = """天气分析如下：
```json
{"date_advice": [{"date": "2026-09-15", "risk": "", "suggestion": ""}]}
```
"""

    decision = parse_control_decision(response, WeatherControlDecision)

    assert decision.date_advice[0].date == "2026-09-15"


def test_control_agents_return_validated_decisions_and_metrics():
    """验证未知 ID 被移除，调用异常产生可观测的确定性降级。"""
    orchestrator = ControlAgentOrchestrator(
        attraction_agent=FakeControlAgent(
            name="景点决策专家",
            response=(
                '{"selected_poi_ids":["a","unknown","a","b"],'
                '"daily_groups":[["a","a"],["unknown","b"]],'
                '"selection_reason":"按区域安排"}'
            ),
        ),
        weather_agent=FakeControlAgent(
            name="天气风险专家",
            response=(
                '{"date_advice":['
                '{"date":"2026-09-15","risk":"高温","suggestion":"防晒"},'
                '{"date":"2099-01-01","risk":"未知","suggestion":"忽略"}'
                ']}'
            ),
        ),
        hotel_agent=FakeControlAgent(
            name="住宿选择专家",
            error=TimeoutError("酒店控制调用超时"),
        ),
    )
    attractions = [build_poi("a", "故宫"), build_poi("b", "什刹海")]
    hotels = [build_poi("h1", "测试酒店")]

    decisions = orchestrator.run(
        request=build_request(),
        attractions=attractions,
        weather=[build_weather("2026-09-15")],
        hotels=hotels,
    )

    assert isinstance(decisions, ControlAgentDecisions)
    assert decisions.attraction.selected_poi_ids == ["a", "b"]
    assert decisions.attraction.daily_groups == [["a"], ["b"]]
    assert [
        advice.date for advice in decisions.weather.date_advice
    ] == ["2026-09-15"]
    assert decisions.hotel.status == "degraded"
    assert decisions.hotel.selected_hotel_ids == ["h1"]
    assert decisions.hotel.metrics is not None
    assert decisions.hotel.metrics.controller == "hotel"
    assert decisions.hotel.metrics.succeeded is False
    assert decisions.degraded_agents == ["hotel"]


def test_invalid_attraction_json_uses_deterministic_real_candidates():
    """验证景点 Agent 返回坏 JSON 时只使用真实候选并稳定分组。"""
    orchestrator = ControlAgentOrchestrator(
        attraction_agent=FakeControlAgent("景点决策专家"),
        weather_agent=FakeControlAgent("天气风险专家"),
        hotel_agent=FakeControlAgent("住宿选择专家"),
    )
    attractions = [
        build_poi("a", "景点A"),
        build_poi("b", "景点B"),
        build_poi("c", "景点C"),
    ]

    decision = orchestrator._build_attraction_decision(
        request=build_request(),
        attractions=attractions,
        raw_response="这不是JSON",
        metrics=None,
        call_error=None,
    )

    assert decision.status == "degraded"
    assert decision.selected_poi_ids == ["a", "b", "c"]
    assert decision.daily_groups == [["a", "c"], ["b"]]


def test_prompt_payload_serializes_nested_fact_timestamps_as_json() -> None:
    """控制 Agent 输入中的嵌套事实时间必须编码为ISO 8601字符串。"""
    retrieved_at = datetime(2026, 9, 16, 1, 30, tzinfo=timezone.utc)
    poi = build_poi("a", "夜间景点")
    poi.visit_constraints = VisitConstraints(
        opening_time="11:00",
        closing_time="02:00",
        closing_day_offset=1,
        opening_hours_source=FactSource(
            provider="amap",
            quality=FactQuality.VERIFIED,
            retrieved_at=retrieved_at,
        ),
    )

    serialized = ControlAgentOrchestrator._serialize_pois([poi])
    payload = json.loads(serialized)

    assert payload[0]["visit_constraints"]["closing_day_offset"] == 1
    assert (
        payload[0]["visit_constraints"]["opening_hours_source"]["retrieved_at"]
        == "2026-09-16T01:30:00Z"
    )
