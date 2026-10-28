"""多控制 Agent 的并行执行、校验和降级编排。"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple

from ..models.schemas import POIInfo, TripRequest, WeatherInfo
from ..observability import get_job_id, record_external_call
from ..services.external_call_audit_service import record_external_call_audit
from ..services.resilience_service import (
    RedisCircuitBreaker,
    TaskBudget,
    get_circuit_breaker,
)
from .contracts import (
    AgentRunMetrics,
    AttractionControlDecision,
    ControlAgentDecisions,
    HotelControlDecision,
    WeatherControlDecision,
    parse_control_decision,
)
from .stateless_control_agent import StatelessControlAgent


class ControlAgentOrchestrator:
    """并行运行三个控制 Agent，并保证输出只引用真实候选。"""

    def __init__(
            self,
            attraction_agent: StatelessControlAgent,
            weather_agent: StatelessControlAgent,
            hotel_agent: StatelessControlAgent,
    ) -> None:
        """
        初始化控制 Agent 编排器。

        Args:
            attraction_agent: 景点筛选与分组 Agent。
            weather_agent: 天气风险分析 Agent。
            hotel_agent: 酒店选择 Agent。
        """
        self.attraction_agent = attraction_agent
        self.weather_agent = weather_agent
        self.hotel_agent = hotel_agent

    def run(
            self,
            request: TripRequest,
            attractions: List[POIInfo],
            weather: List[WeatherInfo],
            hotels: List[POIInfo],
            task_budget: Optional[TaskBudget] = None,
    ) -> ControlAgentDecisions:
        """
        并行执行控制 Agent 并生成完成白名单校验的决策。

        Args:
            request: 用户旅行需求。
            attractions: 真实候选景点。
            weather: 真实天气信息。
            hotels: 真实候选酒店。
            task_budget: 可选的单次生成任务共享预算。

        Returns:
            景点、天气和酒店的结构化控制决策集合。
        """
        request_json = request.model_dump_json()
        attraction_json = self._serialize_pois(attractions)
        weather_json = json.dumps(
            [item.model_dump(mode="json") for item in weather],
            ensure_ascii=False,
        )
        hotel_json = self._serialize_pois(hotels)
        tasks = {
            "attraction": (
                self.attraction_agent,
                f"用户需求：{request_json}\n候选景点：{attraction_json}",
            ),
            "weather": (
                self.weather_agent,
                f"用户需求：{request_json}\n真实天气：{weather_json}",
            ),
            "hotel": (
                self.hotel_agent,
                f"用户需求：{request_json}\n候选酒店：{hotel_json}",
            ),
        }
        raw_results: Dict[str, str] = {}
        metrics: Dict[str, AgentRunMetrics] = {}
        errors: Dict[str, str] = {}
        circuit_breaker = get_circuit_breaker() if task_budget is not None else None
        audit_job_id = get_job_id() or None

        with ThreadPoolExecutor(
                max_workers=3,
                thread_name_prefix="trip-control",
        ) as executor:
            futures = {
                executor.submit(
                    self._execute_control_agent,
                    name,
                    agent,
                    query,
                    task_budget,
                    circuit_breaker,
                    audit_job_id,
                ): name
                for name, (agent, query) in tasks.items()
            }

            for future in as_completed(futures):
                name = futures[future]
                raw, call_metrics, error_message = future.result()
                raw_results[name] = raw
                metrics[name] = call_metrics
                if error_message:
                    errors[name] = error_message

        return self._build_control_decisions(
            request=request,
            attractions=attractions,
            weather=weather,
            hotels=hotels,
            raw_results=raw_results,
            metrics=metrics,
            errors=errors,
        )

    @staticmethod
    def _serialize_pois(pois: List[POIInfo]) -> str:
        """
        序列化控制 Agent 所需的 POI 字段并排除图片 URL。

        Args:
            pois: 真实 POI 候选。

        Returns:
            可安全放入 Agent 输入的 JSON 字符串。
        """
        return json.dumps(
            [
                item.model_dump(
                    mode="json",
                    exclude={"photos", "source"},
                )
                for item in pois
            ],
            ensure_ascii=False,
        )

    @staticmethod
    def _execute_control_agent(
            name: str,
            agent: StatelessControlAgent,
            query: str,
            task_budget: Optional[TaskBudget] = None,
            circuit_breaker: Optional[RedisCircuitBreaker] = None,
            audit_job_id: Optional[str] = None,
    ) -> Tuple[str, AgentRunMetrics, Optional[str]]:
        """
        执行单个控制 Agent 并采集请求级指标。

        Args:
            name: 控制器稳定标识。
            agent: 需要执行的无状态控制 Agent。
            query: 当前请求输入。
            task_budget: 可选的任务级 LLM 调用与费用预算。
            circuit_breaker: 可选的共享 LLM 熔断器。
            audit_job_id: 从主线程显式传入的 SQL 审计任务 ID。

        Returns:
            原始响应、执行指标和可选错误摘要组成的元组。
        """
        started_at = perf_counter()
        requested_at = datetime.now(timezone.utc)
        response = ""
        error_message: Optional[str] = None
        provider_call_started = False

        try:
            if task_budget is not None:
                task_budget.consume_llm(name)
            if circuit_breaker is not None:
                circuit_breaker.before_call("llm")
            provider_call_started = True
            response = agent.run(
                query,
                max_tokens=600,
                temperature=0.2,
                extra_body={"enable_thinking": False},
            )
            if circuit_breaker is not None:
                circuit_breaker.record_success("llm")
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            if circuit_breaker is not None and provider_call_started:
                circuit_breaker.record_failure("llm")

        usage = ControlAgentOrchestrator._consume_agent_usage(agent)
        metrics = AgentRunMetrics(
            controller=name,
            agent_name=agent.name,
            succeeded=error_message is None,
            elapsed_seconds=round(perf_counter() - started_at, 3),
            input_chars=len(query),
            output_chars=len(response),
            model=getattr(getattr(agent, "llm", None), "model", None),
            input_tokens=usage.get("input_tokens") if usage else None,
            output_tokens=usage.get("output_tokens") if usage else None,
            total_tokens=usage.get("total_tokens") if usage else None,
            cost_cny=usage.get("cost_cny") if usage else None,
            usage_source="provider" if usage else "unavailable",
        )
        if task_budget is not None:
            task_budget.record_cost(metrics.cost_cny)
        record_external_call(
            provider="llm",
            operation=name,
            succeeded=metrics.succeeded,
            elapsed_seconds=metrics.elapsed_seconds,
            cost_cny=metrics.cost_cny,
        )
        record_external_call_audit(
            provider="llm",
            operation=name,
            succeeded=metrics.succeeded,
            elapsed_seconds=metrics.elapsed_seconds,
            cost_cny=metrics.cost_cny,
            error_type=(None if metrics.succeeded else "LLM_CALL_FAILED"),
            requested_at=requested_at,
            job_id=audit_job_id,
        )
        return response, metrics, error_message

    @staticmethod
    def _consume_agent_usage(agent: StatelessControlAgent) -> Optional[Dict[str, Any]]:
        """
        读取支持 usage 跟踪的 LLM 最近一次调用指标。

        Args:
            agent: 刚完成一次 `run` 调用的控制 Agent。

        Returns:
            提供方 token 与费用字典；LLM 不支持时返回 None。
        """
        llm = getattr(agent, "llm", None)
        consume = getattr(llm, "consume_last_usage", None)
        if not callable(consume):
            return None
        usage = consume()
        return usage if isinstance(usage, dict) else None

    @staticmethod
    def _known_unique_ids(
            values: List[str],
            allowed_ids: set,
    ) -> List[str]:
        """
        保留候选白名单中的 ID，并按首次出现顺序去重。

        Args:
            values: Agent 返回的原始 ID 列表。
            allowed_ids: 真实候选 ID 白名单。

        Returns:
            通过白名单校验且没有重复项的 ID 列表。
        """
        result: List[str] = []
        for value in values:
            if value in allowed_ids and value not in result:
                result.append(value)
        return result

    @staticmethod
    def _distribute_ids(values: List[str], group_count: int) -> List[List[str]]:
        """
        将候选 ID 确定性地分配到指定数量的日期组。

        Args:
            values: 需要分组的候选 ID。
            group_count: 日期组数量。

        Returns:
            保持候选顺序的轮询分组结果。
        """
        safe_group_count = max(1, group_count)
        groups = [[] for _ in range(safe_group_count)]
        for index, value in enumerate(values):
            groups[index % safe_group_count].append(value)
        return groups

    def _build_control_decisions(
            self,
            request: TripRequest,
            attractions: List[POIInfo],
            weather: List[WeatherInfo],
            hotels: List[POIInfo],
            raw_results: Dict[str, str],
            metrics: Dict[str, AgentRunMetrics],
            errors: Dict[str, str],
    ) -> ControlAgentDecisions:
        """
        解析控制 Agent 输出并用真实候选生成确定性降级决策。

        Args:
            request: 当前旅行规划请求。
            attractions: 真实景点候选。
            weather: 真实天气数据。
            hotels: 真实酒店候选。
            raw_results: 按控制器保存的原始 LLM 文本。
            metrics: 按控制器保存的请求级执行指标。
            errors: 调用阶段产生的错误摘要。

        Returns:
            景点、天气和酒店的结构化控制决策集合。
        """
        attraction_decision = self._build_attraction_decision(
            request=request,
            attractions=attractions,
            raw_response=raw_results.get("attraction", ""),
            metrics=metrics.get("attraction"),
            call_error=errors.get("attraction"),
        )
        weather_decision = self._build_weather_decision(
            weather=weather,
            raw_response=raw_results.get("weather", ""),
            metrics=metrics.get("weather"),
            call_error=errors.get("weather"),
        )
        hotel_decision = self._build_hotel_decision(
            hotels=hotels,
            raw_response=raw_results.get("hotel", ""),
            metrics=metrics.get("hotel"),
            call_error=errors.get("hotel"),
        )
        ordered_decisions = {
            "attraction": attraction_decision,
            "weather": weather_decision,
            "hotel": hotel_decision,
        }

        return ControlAgentDecisions(
            attraction=attraction_decision,
            weather=weather_decision,
            hotel=hotel_decision,
            degraded_agents=[
                name
                for name, decision in ordered_decisions.items()
                if decision.status == "degraded"
            ],
        )

    def _build_attraction_decision(
            self,
            request: TripRequest,
            attractions: List[POIInfo],
            raw_response: str,
            metrics: Optional[AgentRunMetrics],
            call_error: Optional[str],
    ) -> AttractionControlDecision:
        """
        解析并校验景点控制决策，失败时按候选顺序分组。

        Args:
            request: 当前旅行规划请求。
            attractions: 真实景点候选。
            raw_response: 景点 Agent 原始响应。
            metrics: 景点 Agent 执行指标。
            call_error: 景点 Agent 调用错误。

        Returns:
            仅包含真实候选 ID 的景点控制决策。
        """
        allowed_ids = {item.id for item in attractions}
        fallback_ids = [
            item.id
            for item in attractions[:max(1, request.travel_days * 3)]
        ]

        try:
            if call_error:
                raise ValueError(call_error)
            decision = parse_control_decision(
                raw_response,
                AttractionControlDecision,
            )
            selected_ids = self._known_unique_ids(
                decision.selected_poi_ids,
                allowed_ids,
            )
            if not selected_ids:
                raise ValueError("景点 Agent 未选择有效候选 POI")

            grouped_ids: List[List[str]] = []
            used_group_ids = set()
            for group in decision.daily_groups[:request.travel_days]:
                clean_group = self._known_unique_ids(group, set(selected_ids))
                clean_group = [
                    item for item in clean_group if item not in used_group_ids
                ]
                used_group_ids.update(clean_group)
                grouped_ids.append(clean_group)

            missing_ids = [
                item for item in selected_ids if item not in used_group_ids
            ]
            if missing_ids or not grouped_ids:
                grouped_ids = self._distribute_ids(
                    selected_ids,
                    request.travel_days,
                )

            return decision.model_copy(update={
                "selected_poi_ids": selected_ids,
                "daily_groups": grouped_ids,
                "metrics": metrics,
            })
        except Exception as exc:
            return AttractionControlDecision(
                status="degraded",
                error_message=f"{type(exc).__name__}: {exc}",
                metrics=metrics,
                selected_poi_ids=fallback_ids,
                daily_groups=self._distribute_ids(
                    fallback_ids,
                    request.travel_days,
                ),
                selection_reason="控制 Agent 不可用，按真实候选顺序降级分组",
            )

    def _build_weather_decision(
            self,
            weather: List[WeatherInfo],
            raw_response: str,
            metrics: Optional[AgentRunMetrics],
            call_error: Optional[str],
    ) -> WeatherControlDecision:
        """
        解析天气控制决策并移除不存在于真实天气中的日期。

        Args:
            weather: 真实天气数据。
            raw_response: 天气 Agent 原始响应。
            metrics: 天气 Agent 执行指标。
            call_error: 天气 Agent 调用错误。

        Returns:
            日期已通过真实天气白名单校验的天气决策。
        """
        allowed_dates = {item.date for item in weather}
        try:
            if call_error:
                raise ValueError(call_error)
            decision = parse_control_decision(
                raw_response,
                WeatherControlDecision,
            )
            valid_advice = [
                advice
                for advice in decision.date_advice
                if advice.date in allowed_dates
            ]
            return decision.model_copy(update={
                "date_advice": valid_advice,
                "metrics": metrics,
            })
        except Exception as exc:
            return WeatherControlDecision(
                status="degraded",
                error_message=f"{type(exc).__name__}: {exc}",
                metrics=metrics,
                date_advice=[],
            )

    def _build_hotel_decision(
            self,
            hotels: List[POIInfo],
            raw_response: str,
            metrics: Optional[AgentRunMetrics],
            call_error: Optional[str],
    ) -> HotelControlDecision:
        """
        解析并校验酒店控制决策，失败时选择首个真实候选。

        Args:
            hotels: 真实酒店候选。
            raw_response: 酒店 Agent 原始响应。
            metrics: 酒店 Agent 执行指标。
            call_error: 酒店 Agent 调用错误。

        Returns:
            仅包含真实候选 ID 的酒店控制决策。
        """
        allowed_ids = {item.id for item in hotels}
        try:
            if call_error:
                raise ValueError(call_error)
            decision = parse_control_decision(
                raw_response,
                HotelControlDecision,
            )
            selected_ids = self._known_unique_ids(
                decision.selected_hotel_ids,
                allowed_ids,
            )
            if hotels and not selected_ids:
                raise ValueError("酒店 Agent 未选择有效候选 POI")
            return decision.model_copy(update={
                "selected_hotel_ids": selected_ids,
                "metrics": metrics,
            })
        except Exception as exc:
            return HotelControlDecision(
                status="degraded",
                error_message=f"{type(exc).__name__}: {exc}",
                metrics=metrics,
                selected_hotel_ids=[hotels[0].id] if hotels else [],
                selection_reason="控制 Agent 不可用，使用首个真实酒店候选",
            )
