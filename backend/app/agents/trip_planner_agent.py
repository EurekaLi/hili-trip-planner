"""多智能体旅行规划系统"""

import json
from datetime import date, datetime, timedelta, timezone
from time import perf_counter
from typing import Dict, Any, List, Optional, Tuple

from ..config import get_settings
from ..models.schemas import (
    AgentDecisionTrace,
    AgentRunMetrics,
    Attraction,
    Budget,
    DayPlan,
    FactQuality,
    FactSource,
    ExternalCallMetric,
    Hotel,
    Location,
    Meal,
    POIInfo,
    RouteLeg,
    TripPlan,
    TripRequest,
    WeatherInfo,
)
from ..services.budget_service import estimate_route_cost, recalculate_budget
from ..services.external_call_audit_service import record_external_call_audit
from ..services.itinerary_optimizer import (
    ItineraryOptimizer,
    build_visit_constraints,
)
from ..services.llm_service import get_llm
from ..observability import record_external_call
from ..services.resilience_service import (
    TaskBudget,
    activate_task_budget,
    deactivate_task_budget,
    get_circuit_breaker,
    get_guarded_amap_service,
)
from .contracts import ControlAgentDecisions
from .control_orchestrator import ControlAgentOrchestrator
from .progress import TripGenerationCancelled, TripProgressCallback
from .stateless_control_agent import StatelessControlAgent

MIN_DAILY_VISIT_MINUTES = 420
FULL_DAY_VISIT_MINUTES = 420
MAX_ATTRACTION_CANDIDATES = 40
POPULAR_ATTRACTION_KEYWORDS = ("景点", "博物馆", "公园", "风景区")
EVENING_ATTRACTION_KEYWORDS = ("什刹海", "后海", "夜市", "夜游", "夜景")
PREFERENCE_SEARCH_KEYWORDS = {
    "历史文化": "历史景点",
    "自然风光": "风景区",
    "美食": "美食街",
    "购物": "商业街",
    "艺术": "美术馆",
    "休闲": "公园",
}

# ============ Agent提示词 ============

ATTRACTION_AGENT_PROMPT_v0 = """你是景点搜索专家。你的任务是根据城市和用户偏好搜索合适的景点。

**重要提示:**
你必须使用工具来搜索景点!不要自己编造景点信息!

**工具调用格式:**
使用maps_text_search工具时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_text_search:keywords=景点关键词,city=城市名]`

**示例:**
用户: "搜索北京的历史文化景点"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=历史文化,city=北京]

用户: "搜索上海的公园"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=公园,city=上海]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
3. 参数用逗号分隔
"""

WEATHER_AGENT_PROMPT_v0 = """你是天气查询专家。你的任务是查询指定城市的天气信息。

**重要提示:**
你必须使用工具来查询天气!不要自己编造天气信息!

**工具调用格式:**
使用maps_weather工具时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_weather:city=城市名]`

**示例:**
用户: "查询北京天气"
你的回复: [TOOL_CALL:amap_maps_weather:city=北京]

用户: "上海的天气怎么样"
你的回复: [TOOL_CALL:amap_maps_weather:city=上海]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
"""

HOTEL_AGENT_PROMPT_v0 = """你是酒店推荐专家。你的任务是根据城市和景点位置推荐合适的酒店。

**重要提示:**
你必须使用工具来搜索酒店!不要自己编造酒店信息!

**工具调用格式:**
使用maps_text_search工具搜索酒店时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_text_search:keywords=酒店,city=城市名]`

**示例:**
用户: "搜索北京的酒店"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=酒店,city=北京]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
3. 关键词使用"酒店"或"宾馆"
"""




PLANNER_AGENT_PROMPT = """你是行程规划专家。你的任务是根据景点信息和天气信息,生成详细的旅行计划。

请严格按照以下JSON格式返回旅行计划:
```json
{
  "city": "城市名称",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "days": [
    {
      "date": "YYYY-MM-DD",
      "day_index": 0,
      "description": "第1天行程概述",
      "transportation": "交通方式",
      "accommodation": "住宿类型",
      "hotel": {
        "name": "酒店名称",
        "address": "酒店地址",
        "location": {"longitude": 116.397128, "latitude": 39.916527},
        "price_range": "300-500元",
        "rating": "4.5",
        "distance": "距离景点2公里",
        "type": "经济型酒店",
        "estimated_cost": 400
      },
      "attractions": [
        {
          "name": "景点名称",
          "address": "详细地址",
          "location": {"longitude": 116.397128, "latitude": 39.916527},
          "visit_duration": 120,
          "description": "景点详细描述",
          "category": "景点类别",
          "ticket_price": 60
        }
      ],
      "meals": [
        {"type": "breakfast", "name": "早餐推荐", "description": "早餐描述", "estimated_cost": 30},
        {"type": "lunch", "name": "午餐推荐", "description": "午餐描述", "estimated_cost": 50},
        {"type": "dinner", "name": "晚餐推荐", "description": "晚餐描述", "estimated_cost": 80}
      ]
    }
  ],
  "weather_info": [
    {
      "date": "YYYY-MM-DD",
      "day_weather": "晴",
      "night_weather": "多云",
      "day_temp": 25,
      "night_temp": 15,
      "wind_direction": "南风",
      "wind_power": "1-3级"
    }
  ],
  "overall_suggestions": "总体建议",
  "budget": {
    "total_attractions": 180,
    "total_hotels": 1200,
    "total_meals": 480,
    "total_transportation": 200,
    "total": 2060
  }
}
```

**重要提示:**
1. weather_info数组必须包含每一天的天气信息
2. 温度必须是纯数字(不要带°C等单位)
3. 每天安排2-3个景点
4. 考虑景点之间的距离和游览时间
5. 每天必须包含早中晚三餐
6. 提供实用的旅行建议
7. **必须包含预算信息**:
   - 景点门票价格(ticket_price)
   - 餐饮预估费用(estimated_cost)
   - 酒店预估费用(estimated_cost)
   - 预算汇总(budget)包含各项总费用
"""

#============做更改
ATTRACTION_AGENT_PROMPT = """
你是旅行景点决策专家。

系统会提供已经通过高德地图验证的候选景点。你的职责是：
1. 根据用户偏好筛选景点。
2. 根据游览价值和位置进行每日分组。
3. 只能引用候选数据中的 POI ID。
4. 不得修改名称、地址和经纬度。
5. 不得创造候选列表以外的景点。
6. 普通游览日的景点建议时长合计至少420分钟。
7. 建议时长达到420分钟的全天型景点可以单独安排一天。

返回 JSON：
{
  "selected_poi_ids": ["POI ID"],
  "daily_groups": [["POI ID"]],
  "selection_reason": "选择理由"
}
"""
WEATHER_AGENT_PROMPT = """
你是旅行天气风险分析专家。

你只能分析输入的真实天气数据，不得生成天气数据。请识别降雨、
高温、低温和大风等风险，并给出行程调整建议。

返回 JSON：
{
  "date_advice": [
    {
      "date": "YYYY-MM-DD",
      "risk": "风险说明",
      "suggestion": "行程建议"
    }
  ]
}
"""
HOTEL_AGENT_PROMPT = """
你是住宿选择专家。

请根据住宿偏好、候选酒店和候选景点选择酒店。只能选择候选列表中
存在的 POI ID，不得编造酒店、评分、价格或距离。

返回 JSON：
{
  "selected_hotel_ids": ["POI ID"],
  "selection_reason": "选择理由"
}
"""

class MultiAgentTripPlanner:
    """多智能体旅行规划系统"""

    def __init__(self):
        """初始化多智能体系统"""
        print("🔄 开始初始化多智能体旅行规划系统...")

        try:
            #settings = get_settings()
            self.llm = get_llm()
            self.amap_service = get_guarded_amap_service()

            # # 创建共享的MCP工具(只创建一次)
            # print("  - 创建共享MCP工具...")
            # self.amap_tool = MCPTool(
            #     name="amap",
            #     description="高德地图服务",
            #     server_command=["uvx", "amap-mcp-server"],
            #     env={"AMAP_MAPS_API_KEY": settings.amap_api_key},
            #     auto_expand=True
            # )
            #
            # print(self.amap_tool._available_tools)
            # # print(self.amap_tool.list_tools())
            #
            # # 创建景点搜索Agent
            # print("  - 创建景点搜索Agent...")
            # self.attraction_agent = MySimpleAgent(
            #     name="景点搜索专家",
            #     llm=self.llm,
            #     system_prompt=ATTRACTION_AGENT_PROMPT
            # )
            # self.attraction_agent.add_tool(self.amap_tool)
            #
            # # 创建天气查询Agent
            # print("  - 创建天气查询Agent...")
            # self.weather_agent = MySimpleAgent(
            #     name="天气查询专家",
            #     llm=self.llm,
            #     system_prompt=WEATHER_AGENT_PROMPT
            # )
            # self.weather_agent.add_tool(self.amap_tool)
            #
            # # 创建酒店推荐Agent
            # print("  - 创建酒店推荐Agent...")
            # self.hotel_agent = MySimpleAgent(
            #     name="酒店推荐专家",
            #     llm=self.llm,
            #     system_prompt=HOTEL_AGENT_PROMPT
            # )
            # self.hotel_agent.add_tool(self.amap_tool)

            # 创建行程规划Agent(不需要工具)

            self.attraction_agent = StatelessControlAgent(
                name="景点决策专家",
                llm=self.llm,
                system_prompt=ATTRACTION_AGENT_PROMPT,
            )

            self.weather_agent = StatelessControlAgent(
                name="天气风险专家",
                llm=self.llm,
                system_prompt=WEATHER_AGENT_PROMPT,
            )

            self.hotel_agent = StatelessControlAgent(
                name="住宿选择专家",
                llm=self.llm,
                system_prompt=HOTEL_AGENT_PROMPT,
            )

            self.planner_agent = StatelessControlAgent(
                name="行程规划专家",
                llm=self.llm,
                system_prompt=PLANNER_AGENT_PROMPT,
            )
            self.control_orchestrator = ControlAgentOrchestrator(
                attraction_agent=self.attraction_agent,
                weather_agent=self.weather_agent,
                hotel_agent=self.hotel_agent,
            )

            print(f"✅ 多智能体系统初始化成功")
            print(f"   景点搜索Agent: {len(self.attraction_agent.list_tools())} 个工具")
            print(f"   天气查询Agent: {len(self.weather_agent.list_tools())} 个工具")
            print(f"   酒店推荐Agent: {len(self.hotel_agent.list_tools())} 个工具")

        except Exception as e:
            print(f"❌ 多智能体系统初始化失败: {str(e)}")
            import traceback
            traceback.print_exc()
            raise

    @staticmethod
    def _calculate_candidate_target(travel_days: int) -> int:
        """
        根据旅行天数计算需要准备的真实景点候选数量。

        Args:
            travel_days: 用户计划的旅行天数。

        Returns:
            用于搜索和规划的候选景点目标数量。
        """
        return min(max(travel_days * 4, 8), MAX_ATTRACTION_CANDIDATES)

    @staticmethod
    def _build_attraction_search_keywords(request: TripRequest) -> List[str]:
        """
        将可选偏好转换为高德搜索词，并补充热门景点兜底词。

        Args:
            request: 用户旅行需求。

        Returns:
            保持优先级且已经去重的景点搜索关键词。
        """
        keywords: List[str] = []

        for preference in request.preferences[:3]:
            keyword = PREFERENCE_SEARCH_KEYWORDS.get(
                preference,
                preference.strip(),
            )
            if keyword and keyword not in keywords:
                keywords.append(keyword)

        for keyword in POPULAR_ATTRACTION_KEYWORDS:
            if keyword not in keywords:
                keywords.append(keyword)

        return keywords

    @staticmethod
    def _serialize_pois_for_agents(pois: List[POIInfo]) -> str:
        """
        序列化控制 Agent 所需的 POI 字段并排除长图片 URL。

        Args:
            pois: 真实 POI 候选列表。

        Returns:
            可直接放入提示词的 JSON 字符串。
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
    def _estimate_visit_duration(poi: POIInfo) -> int:
        """
        根据 POI 名称和高德类型估算合理游览时间。

        Args:
            poi: 高德返回的真实 POI。

        Returns:
            建议游览分钟数；全天型景点返回至少420分钟。
        """
        identity = f"{poi.name} {poi.type}"
        full_day_keywords = (
            "环球影城",
            "迪士尼",
            "欢乐谷",
            "主题乐园",
            "大型游乐",
        )
        long_visit_keywords = (
            "故宫",
            "长城",
            "颐和园",
            "国家博物馆",
            "动物园",
            "海洋馆",
        )
        short_visit_keywords = (
            "什刹海",
            "后海",
            "南锣鼓巷",
            "王府井",
            "三里屯",
            "商业街",
            "美食街",
        )

        if any(keyword in identity for keyword in full_day_keywords):
            return 480
        if any(keyword in identity for keyword in long_visit_keywords):
            return 240
        if any(keyword in identity for keyword in short_visit_keywords):
            return 120
        if any(keyword in identity for keyword in ("博物馆", "美术馆")):
            return 180
        if any(keyword in identity for keyword in ("公园", "风景名胜")):
            return 180

        return 150

    def _run_control_agents(
            self,
            request: TripRequest,
            attractions: List[POIInfo],
            weather: List[WeatherInfo],
            hotels: List[POIInfo],
            task_budget: Optional[TaskBudget] = None,
    ) -> ControlAgentDecisions:
        """
        并行执行景点、天气和住宿控制 Agent。

        Args:
            request: 用户旅行需求。
            attractions: 真实候选景点。
            weather: 真实天气信息。
            hotels: 真实候选酒店。
            task_budget: 当前生成任务共享的外部调用预算。

        Returns:
            已完成候选白名单校验的结构化控制决策。
        """
        return self.control_orchestrator.run(
            request=request,
            attractions=attractions,
            weather=weather,
            hotels=hotels,
            task_budget=task_budget,
        )

    def _load_grounding_data(
            self,
            request: TripRequest,
    ) -> Tuple[
        List[POIInfo],
        List[WeatherInfo],
        List[POIInfo],
        List[POIInfo],
    ]:
        """
        查询生成旅行计划所需的真实地图数据。

        Args:
            request: 用户提交的旅行规划请求。

        Returns:
            由景点、天气、酒店和餐厅候选组成的元组。

        Raises:
            RuntimeError: 未查询到可用于规划的景点时抛出。
        """
        attraction_keywords = self._build_attraction_search_keywords(request)
        candidate_target = self._calculate_candidate_target(request.travel_days)
        attraction_map: Dict[str, POIInfo] = {}

        for keyword in attraction_keywords:
            remaining = candidate_target - len(attraction_map)
            if remaining <= 0:
                break

            pois = self.amap_service.search_poi(
                keywords=keyword,
                city=request.city,
                citylimit=True,
                limit=min(remaining, 20),
            )
            for poi in pois:
                attraction_map[poi.id] = poi

        attractions = list(attraction_map.values())

        if not attractions:
            raise RuntimeError(f"未查询到{request.city}的真实景点数据")

        for attraction in attractions:
            attraction.suggested_visit_duration = (
                self._estimate_visit_duration(attraction)
            )

        weather = self.amap_service.get_weather(request.city)

        hotel_keyword = request.accommodation or "酒店"
        hotels = self.amap_service.search_poi(
            keywords=hotel_keyword,
            city=request.city,
            citylimit=True,
            limit=4,
        )

        restaurant_target = min(max(request.travel_days * 3, 6), 20)
        restaurants = self.amap_service.search_poi(
            keywords="餐厅",
            city=request.city,
            citylimit=True,
            limit=restaurant_target,
        )

        return (
            attractions[:candidate_target],
            weather,
            hotels[:6],
            restaurants[:restaurant_target],
        )

    @staticmethod
    def _report_progress(
            callback: Optional[TripProgressCallback],
            stage: str,
            progress: int,
            message: str,
    ) -> None:
        """
        在规划阶段边界通知异步任务执行器。

        Args:
            callback: 可选的进度回调；同步兼容接口不传入。
            stage: 稳定的业务阶段名称。
            progress: 0 到 100 之间的阶段进度。
            message: 面向用户的阶段说明。
        """
        if callback is not None:
            callback(stage, progress, message)

    def plan_trip(
            self,
            request: TripRequest,
            progress_callback: Optional[TripProgressCallback] = None,
    ) -> TripPlan:
        """
        通过真实数据查询、并行专业决策和统一规划生成旅行计划。

        Args:
            request: 用户提交的旅行规划请求。
            progress_callback: 可选的真实阶段进度回调。

        Returns:
            已完成 POI 校验、天气覆盖和路线补全的旅行计划。

        Raises:
            RuntimeError: 数据查询、Agent 决策或行程生成失败时抛出。
        """
        task_budget = TaskBudget.from_settings()
        budget_token = activate_task_budget(task_budget)
        amap_call_metrics, trace_token = self.amap_service.begin_call_trace()
        circuit_breaker = get_circuit_breaker()
        try:
            # 第一阶段：从高德服务获取确定性的真实数据。
            self._report_progress(
                progress_callback,
                "loading_grounding_data",
                5,
                "正在查询真实景点、天气和住宿数据",
            )
            attractions, weather, hotels, restaurants = (
                self._load_grounding_data(request)
            )
            weather, missing_weather_dates = self._filter_weather_for_request(
                request=request,
                weather=weather,
            )

            # 第二阶段：三个专业 Agent 并行分析真实数据。
            self._report_progress(
                progress_callback,
                "running_control_agents",
                22,
                "景点、天气和住宿控制 Agent 正在并行决策",
            )
            decisions = self._run_control_agents(
                request=request,
                attractions=attractions,
                weather=weather,
                hotels=hotels,
                task_budget=task_budget,
            )

            # 第三阶段：Planner 根据真实数据和专业决策生成初步行程。
            self._report_progress(
                progress_callback,
                "running_planner",
                42,
                "Planner Agent 正在编排行程",
            )
            planner_query = self._build_planner_query(
                request=request,
                attractions=attractions,
                weather=weather,
                hotels=hotels,
                restaurants=restaurants,
                decisions=decisions,
            )

            planner_started_at = perf_counter()
            planner_requested_at = datetime.now(timezone.utc)
            planner_succeeded = False
            planner_response = ""
            planner_provider_started = False

            try:
                task_budget.consume_llm("planner")
                circuit_breaker.before_call("llm")
                planner_provider_started = True
                planner_response = self.planner_agent.run(
                    planner_query,
                    max_tokens=4000,
                    temperature=0.2,
                    extra_body={
                        "enable_thinking": False,
                    },
                )
                planner_succeeded = True
                circuit_breaker.record_success("llm")
            except Exception as exc:
                if planner_provider_started:
                    circuit_breaker.record_failure("llm")
                raise RuntimeError(
                    f"Planner Agent生成行程超时或失败: {exc}"
                ) from exc
            finally:
                planner_elapsed = perf_counter() - planner_started_at
                planner_usage = ControlAgentOrchestrator._consume_agent_usage(
                    self.planner_agent
                )
                planner_metrics = AgentRunMetrics(
                    controller="planner",
                    agent_name=self.planner_agent.name,
                    succeeded=planner_succeeded,
                    elapsed_seconds=round(planner_elapsed, 3),
                    input_chars=len(planner_query),
                    output_chars=len(planner_response),
                    model=getattr(
                        getattr(self.planner_agent, "llm", None),
                        "model",
                        None,
                    ),
                    input_tokens=(
                        planner_usage.get("input_tokens")
                        if planner_usage
                        else None
                    ),
                    output_tokens=(
                        planner_usage.get("output_tokens")
                        if planner_usage
                        else None
                    ),
                    total_tokens=(
                        planner_usage.get("total_tokens")
                        if planner_usage
                        else None
                    ),
                    cost_cny=(
                        planner_usage.get("cost_cny")
                        if planner_usage
                        else None
                    ),
                    usage_source=(
                        "provider" if planner_usage else "unavailable"
                    ),
                )
                task_budget.record_cost(planner_metrics.cost_cny)
                record_external_call(
                    provider="llm",
                    operation="planner",
                    succeeded=planner_metrics.succeeded,
                    elapsed_seconds=planner_metrics.elapsed_seconds,
                    cost_cny=planner_metrics.cost_cny,
                )
                record_external_call_audit(
                    provider="llm",
                    operation="planner",
                    succeeded=planner_metrics.succeeded,
                    elapsed_seconds=planner_metrics.elapsed_seconds,
                    cost_cny=planner_metrics.cost_cny,
                    error_type=(
                        None
                        if planner_metrics.succeeded
                        else "LLM_CALL_FAILED"
                    ),
                    requested_at=planner_requested_at,
                )
                print(
                    "[LLM调用统计] "
                    f"agent={self.planner_agent.name}, "
                    f"success={planner_succeeded}, "
                    f"elapsed={planner_elapsed:.3f}s, "
                    f"input_chars={len(planner_query)}"
                )

            # 第四阶段：解析并使用 Pydantic 校验 Planner 输出。
            self._report_progress(
                progress_callback,
                "validating_plan",
                65,
                "正在校验 Planner 输出结构",
            )
            trip_plan = self._parse_response(
                response=planner_response,
                request=request,
            )

            if decisions.degraded_agents:
                trip_plan.warnings.append(
                    "控制 Agent 已降级："
                    + "、".join(decisions.degraded_agents)
                )

            # 第五阶段：使用候选数据覆盖 LLM 返回的 POI 事实字段。
            self._report_progress(
                progress_callback,
                "grounding_plan",
                72,
                "正在用真实 POI 数据校准行程",
            )
            trip_plan = self._ground_trip_plan(
                trip_plan=trip_plan,
                attractions=attractions,
                hotels=hotels,
                restaurants=restaurants,
            )

            # 第六阶段：使用剩余真实候选补足每日有效游览时间。
            self._report_progress(
                progress_callback,
                "filling_schedule",
                80,
                "正在补足每日游览时间并优化景点组合",
            )
            trip_plan = self._ensure_daily_time_coverage(
                trip_plan=trip_plan,
                attractions=attractions,
            )

            # 如果所有景点均为虚构数据，则本次规划应判定为失败。
            if not any(day.attractions for day in trip_plan.days):
                raise RuntimeError("Planner没有选择任何有效的候选景点")

            # 第七阶段：天气属于事实数据，不采用 LLM 生成的内容。
            self._report_progress(
                progress_callback,
                "enriching_weather",
                86,
                "正在写入真实天气信息",
            )
            trip_plan.weather_info = weather
            if missing_weather_dates:
                trip_plan.warnings.append(
                    "高德天气预报未覆盖：" + "、".join(missing_weather_dates)
                )

            # 第八阶段：比较候选分组并生成真实路线和可执行时间轴。
            self._report_progress(
                progress_callback,
                "optimizing_itinerary",
                90,
                "正在按真实通勤和开放时间优化每日安排",
            )
            trip_plan = self._optimize_itinerary(
                trip_plan=trip_plan,
                request=request,
            )
            trip_plan.budget = self._recalculate_budget(trip_plan)
            trip_plan.agent_trace = self._build_agent_trace(
                decisions=decisions,
                planner_metrics=planner_metrics,
            )
            task_budget.ensure_within_cost()
            trip_plan.external_calls = [
                *amap_call_metrics,
                *self._build_llm_external_calls(
                    decisions=decisions,
                    planner_metrics=planner_metrics,
                ),
            ]

            return trip_plan

        except TripGenerationCancelled:
            raise
        except Exception as exc:
            wrapped = RuntimeError(f"旅行计划生成失败: {exc}")
            wrapped.budget_snapshot = task_budget.snapshot().to_dict()
            raise wrapped from exc
            # return self._create_fallback_plan(request)
        finally:
            self.amap_service.end_call_trace(trace_token)
            deactivate_task_budget(budget_token)

    def _ground_trip_plan(
            self,
            trip_plan: TripPlan,
            attractions: List[POIInfo],
            hotels: List[POIInfo],
            restaurants: List[POIInfo],
    ) -> TripPlan:
        """
        使用高德数据覆盖 LLM 返回的事实字段。

        Args:
            trip_plan: Planner Agent 生成的旅行计划。
            attractions: 高德返回的候选景点。
            hotels: 高德返回的候选酒店。
            restaurants: 高德返回的候选餐厅。

        Returns:
            完成 POI 真实性回填后的旅行计划。
        """
        attraction_by_id = {item.id: item for item in attractions}
        attraction_by_name = {item.name: item for item in attractions}
        hotel_by_id = {item.id: item for item in hotels}
        hotel_by_name = {item.name: item for item in hotels}
        used_attraction_ids = set()

        for day in trip_plan.days:
            grounded_attractions = []

            for planned in day.attractions:
                source = (
                        attraction_by_id.get(planned.poi_id or "")
                        or attraction_by_name.get(planned.name)
                )

                # 不在候选集合中的景点不能进入最终计划。
                if source is None or source.id in used_attraction_ids:
                    continue

                used_attraction_ids.add(source.id)
                planned.poi_id = source.id
                planned.name = source.name
                planned.address = source.address
                planned.location = source.location
                planned.category = source.type or planned.category
                planned.rating = source.rating
                planned.photos = list(source.photos)
                planned.image_url = source.photos[0] if source.photos else None
                planned.visit_duration = source.suggested_visit_duration
                planned.visit_constraints = (
                    source.visit_constraints
                    or build_visit_constraints(source.name, source.type)
                )
                planned.source = source.source or FactSource(
                    provider="amap",
                    quality=FactQuality.VERIFIED,
                    note="高德候选POI回填",
                )
                planned.ticket_price_source = FactSource(
                    provider="planner_agent" if planned.ticket_price > 0 else "unknown",
                    quality=(
                        FactQuality.ESTIMATED
                        if planned.ticket_price > 0
                        else FactQuality.UNKNOWN
                    ),
                    note="门票价格未连接票务数据源",
                )
                grounded_attractions.append(planned)

            day.attractions = grounded_attractions

            if day.hotel is not None:
                source_hotel = (
                        hotel_by_id.get(day.hotel.poi_id)
                        or hotel_by_name.get(day.hotel.name)
                )

                if source_hotel is None:
                    day.hotel = None
                else:
                    day.hotel.poi_id = source_hotel.id
                    day.hotel.name = source_hotel.name
                    day.hotel.address = source_hotel.address
                    day.hotel.location = source_hotel.location
                    day.hotel.type = source_hotel.type
                    day.hotel.source = source_hotel.source or FactSource(
                        provider="amap",
                        quality=FactQuality.VERIFIED,
                        note="高德候选酒店回填",
                    )
                    day.hotel.cost_source = FactSource(
                        provider=(
                            "planner_agent"
                            if day.hotel.estimated_cost > 0
                            else "unknown"
                        ),
                        quality=(
                            FactQuality.ESTIMATED
                            if day.hotel.estimated_cost > 0
                            else FactQuality.UNKNOWN
                        ),
                        note="住宿价格未连接酒店实时库存",
                    )
                    if source_hotel.rating is not None:
                        day.hotel.rating = str(source_hotel.rating)

        return self._ground_meals(trip_plan, restaurants)

    @staticmethod
    def _ground_meals(
            trip_plan: TripPlan,
            restaurants: List[POIInfo],
    ) -> TripPlan:
        """
        使用真实餐厅候选回填每日三餐，并保留明确的费用估算来源。

        Args:
            trip_plan: Planner 生成且已完成景点回填的旅行计划。
            restaurants: 高德返回的真实餐厅候选。

        Returns:
            可验证餐厅已完成回填的旅行计划；候选不足时保留明确降级提示。
        """
        restaurant_by_id = {item.id: item for item in restaurants}
        restaurant_by_name = {item.name: item for item in restaurants}
        used_ids = set()
        fallback_index = 0
        default_costs = {"breakfast": 25, "lunch": 60, "dinner": 80}

        for day in trip_plan.days:
            planned_by_type = {
                meal.type: meal
                for meal in day.meals
                if meal.type in default_costs
            }
            grounded_meals: List[Meal] = []

            for meal_type in ("breakfast", "lunch", "dinner"):
                planned = planned_by_type.get(meal_type)
                source = None
                if planned is not None:
                    source = (
                        restaurant_by_id.get(planned.poi_id or "")
                        or restaurant_by_name.get(planned.name)
                    )

                if source is None and restaurants:
                    unused = [item for item in restaurants if item.id not in used_ids]
                    if unused:
                        source = unused[0]
                    else:
                        source = restaurants[fallback_index % len(restaurants)]
                        fallback_index += 1

                if source is None:
                    if planned is not None:
                        planned.source = FactSource(
                            provider="unknown",
                            quality=FactQuality.UNKNOWN,
                            note="未查询到可验证餐厅候选",
                        )
                        planned.cost_source = FactSource(
                            provider="planner_agent",
                            quality=FactQuality.ESTIMATED,
                            note="餐费为模型估算",
                        )
                        grounded_meals.append(planned)
                    continue

                used_ids.add(source.id)
                estimated_cost = (
                    planned.estimated_cost
                    if planned is not None and planned.estimated_cost > 0
                    else default_costs[meal_type]
                )
                grounded_meals.append(
                    Meal(
                        type=meal_type,
                        name=source.name,
                        address=source.address,
                        location=source.location,
                        description=(
                            planned.description
                            if planned is not None
                            else "高德地图验证餐厅"
                        ),
                        estimated_cost=estimated_cost,
                        poi_id=source.id,
                        rating=source.rating,
                        source=source.source or FactSource(
                            provider="amap",
                            quality=FactQuality.VERIFIED,
                            note="高德候选餐厅回填",
                        ),
                        cost_source=FactSource(
                            provider=(
                                "planner_agent"
                                if planned is not None
                                and planned.estimated_cost > 0
                                else "backend_rule"
                            ),
                            quality=FactQuality.ESTIMATED,
                            note="餐费为估算值，不代表餐厅实时价格",
                        ),
                    )
                )

            day.meals = grounded_meals

        if not restaurants:
            trip_plan.warnings.append("未查询到真实餐厅候选，餐饮信息可信度较低")
        return trip_plan

    @staticmethod
    def _build_grounded_attraction(poi: POIInfo) -> Attraction:
        """
        将未被 Planner 选中的真实 POI 转换为可补位的景点模型。

        Args:
            poi: 高德验证过且包含坐标的 POI。

        Returns:
            使用真实 POI 字段构造的景点对象。
        """
        category = poi.type or "景点"
        return Attraction(
            name=poi.name,
            address=poi.address,
            location=poi.location,
            visit_duration=poi.suggested_visit_duration,
            description=f"高德地图已验证地点，类型：{category}",
            category=category,
            rating=poi.rating,
            photos=list(poi.photos),
            poi_id=poi.id,
            image_url=poi.photos[0] if poi.photos else None,
            ticket_price=0,
            source=poi.source or FactSource(
                provider="amap",
                quality=FactQuality.VERIFIED,
                note="高德候选POI补位",
            ),
            ticket_price_source=FactSource(
                provider="unknown",
                quality=FactQuality.UNKNOWN,
                note="补位景点缺少票务数据",
            ),
            visit_constraints=(
                poi.visit_constraints
                or build_visit_constraints(poi.name, poi.type)
            ),
        )

    @staticmethod
    def _coordinate_distance_score(origin: Location, candidate: POIInfo) -> float:
        """
        计算候选 POI 与当前行程末尾景点的近似距离分数。

        Args:
            origin: 当前行程最后一个景点的位置。
            candidate: 待补位的真实 POI。

        Returns:
            经纬度平方距离；数值越小表示位置越近。
        """
        longitude_delta = origin.longitude - candidate.location.longitude
        latitude_delta = origin.latitude - candidate.location.latitude
        return longitude_delta ** 2 + latitude_delta ** 2

    @staticmethod
    def _is_evening_attraction(attraction: Attraction) -> bool:
        """
        判断景点是否更适合作为当日夜间行程。

        Args:
            attraction: 已完成真实性回填的景点。

        Returns:
            名称或描述包含典型夜游关键词时返回 True。
        """
        identity = f"{attraction.name} {attraction.description}"
        return any(
            keyword in identity for keyword in EVENING_ATTRACTION_KEYWORDS
        )

    def _ensure_daily_time_coverage(
            self,
            trip_plan: TripPlan,
            attractions: List[POIInfo],
    ) -> TripPlan:
        """
        用未使用的真实候选补足普通游览日的景点时长。

        全天型景点可以单独占用一天；普通游览日按邻近程度补位，直到
        景点游览时长达到最低目标或真实候选耗尽。

        Args:
            trip_plan: 已完成真实性回填的旅行计划。
            attractions: 可用于补位的全部真实 POI 候选。

        Returns:
            完成每日时长校验和真实景点补位的旅行计划。
        """
        for day in trip_plan.days:
            full_day_attraction = next(
                (
                    attraction
                    for attraction in day.attractions
                    if attraction.visit_duration >= FULL_DAY_VISIT_MINUTES
                ),
                None,
            )
            if full_day_attraction is not None:
                day.attractions = [full_day_attraction]

        used_ids = {
            attraction.poi_id
            for day in trip_plan.days
            for attraction in day.attractions
            if attraction.poi_id
        }
        remaining = [poi for poi in attractions if poi.id not in used_ids]

        for day in trip_plan.days:
            total_minutes = sum(
                attraction.visit_duration for attraction in day.attractions
            )
            if any(
                attraction.visit_duration >= FULL_DAY_VISIT_MINUTES
                for attraction in day.attractions
            ):
                continue

            added_names: List[str] = []
            while total_minutes < MIN_DAILY_VISIT_MINUTES and remaining:
                eligible = [
                    poi for poi in remaining
                    if not day.attractions
                    or poi.suggested_visit_duration < FULL_DAY_VISIT_MINUTES
                ]
                if not eligible:
                    break

                if day.attractions:
                    origin = day.attractions[-1].location
                    candidate = min(
                        eligible,
                        key=lambda poi: self._coordinate_distance_score(
                            origin,
                            poi,
                        ),
                    )
                else:
                    candidate = max(
                        eligible,
                        key=lambda poi: poi.rating or 0,
                    )

                remaining.remove(candidate)
                evening_index = next(
                    (
                        index
                        for index, attraction in enumerate(day.attractions)
                        if self._is_evening_attraction(attraction)
                    ),
                    len(day.attractions),
                )
                day.attractions.insert(
                    evening_index,
                    self._build_grounded_attraction(candidate),
                )
                total_minutes += candidate.suggested_visit_duration
                added_names.append(candidate.name)

            if added_names:
                day.description = (
                    f"{day.description}；补充安排：{'、'.join(added_names)}"
                )

            if total_minutes < MIN_DAILY_VISIT_MINUTES:
                trip_plan.warnings.append(
                    f"第{day.day_index + 1}天真实候选景点不足，"
                    f"当前约覆盖{total_minutes}分钟"
                )

        return trip_plan

    @staticmethod
    def _filter_weather_for_request(
            request: TripRequest,
            weather: List[WeatherInfo],
    ) -> Tuple[List[WeatherInfo], List[str]]:
        """
        只保留旅行日期内的天气，并返回未被预报覆盖的日期。

        Args:
            request: 已校验日期跨度的旅行请求。
            weather: 高德返回的天气预报列表。

        Returns:
            按旅行日期排序的天气列表和缺失日期列表。
        """
        start = date.fromisoformat(request.start_date)
        expected_dates = [
            (start + timedelta(days=index)).isoformat()
            for index in range(request.travel_days)
        ]
        weather_by_date = {
            item.date: item
            for item in weather
            if item.date in expected_dates
        }
        filtered = [
            weather_by_date[item_date]
            for item_date in expected_dates
            if item_date in weather_by_date
        ]
        missing = [
            item_date
            for item_date in expected_dates
            if item_date not in weather_by_date
        ]
        return filtered, missing

    @staticmethod
    def _estimate_route_cost(route: RouteLeg) -> int:
        """
        根据已验证路线距离和交通类型估算单段市内交通费用。

        Args:
            route: 包含真实距离的相邻地点路线。

        Returns:
            以人民币元计的保守整数估算值。
        """
        return estimate_route_cost(route)

    def _recalculate_budget(self, trip_plan: TripPlan) -> Budget:
        """
        从最终行程明细确定性重算预算，覆盖 Planner 的汇总结果。

        Args:
            trip_plan: 已完成 POI、餐厅、天气和路线回填的旅行计划。

        Returns:
            各分项与总额严格一致的后端计算预算。
        """
        return recalculate_budget(trip_plan)

    @staticmethod
    def _build_agent_trace(
            decisions: ControlAgentDecisions,
            planner_metrics: AgentRunMetrics,
    ) -> List[AgentDecisionTrace]:
        """
        将控制决策和 Planner 指标转换为可持久化生成轨迹。

        Args:
            decisions: 三个控制 Agent 的结构化决策。
            planner_metrics: Planner Agent 的请求级调用指标。

        Returns:
            固定顺序的四个 Agent 运行轨迹。
        """
        traces: List[AgentDecisionTrace] = []
        for controller, decision in (
            ("attraction", decisions.attraction),
            ("weather", decisions.weather),
            ("hotel", decisions.hotel),
        ):
            traces.append(
                AgentDecisionTrace(
                    controller=controller,
                    status=decision.status,
                    decision=decision.model_dump(
                        mode="json",
                        exclude={"status", "error_message", "metrics"},
                    ),
                    metrics=decision.metrics,
                    error_message=decision.error_message,
                )
            )

        traces.append(
            AgentDecisionTrace(
                controller="planner",
                status="success" if planner_metrics.succeeded else "degraded",
                decision={"output_validated": planner_metrics.succeeded},
                metrics=planner_metrics,
            )
        )
        return traces

    @staticmethod
    def _build_llm_external_calls(
            decisions: ControlAgentDecisions,
            planner_metrics: AgentRunMetrics,
    ) -> List[ExternalCallMetric]:
        """
        将四个 Agent 的调用指标纳入统一外部费用审计。

        Args:
            decisions: 包含三个控制 Agent 指标的结构化决策。
            planner_metrics: Planner Agent 的请求级指标。

        Returns:
            可与高德调用记录一同持久化的 LLM 调用列表。
        """
        calls: List[ExternalCallMetric] = []
        metrics_by_operation = (
            ("attraction", decisions.attraction.metrics),
            ("weather", decisions.weather.metrics),
            ("hotel", decisions.hotel.metrics),
            ("planner", planner_metrics),
        )
        now = datetime.now(timezone.utc)
        for operation, metrics in metrics_by_operation:
            if metrics is None:
                continue
            calls.append(
                ExternalCallMetric(
                    provider="llm",
                    operation=operation,
                    succeeded=metrics.succeeded,
                    elapsed_seconds=metrics.elapsed_seconds,
                    requested_at=now - timedelta(
                        seconds=metrics.elapsed_seconds
                    ),
                    cost_cny=metrics.cost_cny,
                    error_type=(
                        None if metrics.succeeded else "LLM_CALL_FAILED"
                    ),
                )
            )
        return calls

    def _optimize_itinerary(
            self,
            trip_plan: TripPlan,
            request: TripRequest,
    ) -> TripPlan:
        """
        使用确定性优化器比较分组并生成路线和时间轴。

        Args:
            trip_plan: 已完成 POI 回填和每日时长补足的旅行计划。
            request: 用户旅行需求。

        Returns:
            包含优化分组、真实路线和可执行时间轴的旅行计划。
        """
        settings = get_settings()
        optimizer = ItineraryOptimizer(
            route_provider=self.amap_service,
            max_route_calls=settings.itinerary_route_call_budget,
            cross_region_minutes=settings.itinerary_cross_region_minutes,
        )
        return optimizer.optimize(trip_plan, request)


    def _build_attraction_query(self, request: TripRequest) -> str:
        """构建景点搜索查询 - 直接包含工具调用"""
        keywords = []
        if request.preferences:
            # 只取第一个偏好作为关键词
            keywords = request.preferences[0]
        else:
            keywords = "景点"

        # 直接返回工具调用格式
        query = f"请使用amap_maps_text_search工具搜索{request.city}的{keywords}相关景点。\n[TOOL_CALL:amap_maps_text_search:keywords={keywords},city={request.city}]"
        return query

    def _build_planner_query(
            self,
            request: TripRequest,
            attractions: List[POIInfo],
            weather: List[WeatherInfo],
            hotels: List[POIInfo],
            restaurants: List[POIInfo],
            decisions: ControlAgentDecisions,
    ) -> str:
        """
        构建包含真实数据和专业 Agent 决策的规划请求。

        Args:
            request: 用户旅行需求。
            attractions: 高德返回的候选景点。
            weather: 高德返回的真实天气。
            hotels: 高德返回的候选酒店。
            restaurants: 高德返回的候选餐厅。
            decisions: 三个专业控制 Agent 的结构化并行决策结果。

        Returns:
            发送给 Planner Agent 的完整提示词。
        """
        attraction_json = self._serialize_pois_for_agents(attractions)
        weather_json = json.dumps(
            [item.model_dump(mode="json") for item in weather],
            ensure_ascii=False,
        )
        hotel_json = self._serialize_pois_for_agents(hotels)
        restaurant_json = self._serialize_pois_for_agents(restaurants)
        decision_json = decisions.model_dump_json()

        query = f"""请根据以下信息生成{request.city}的{request.travel_days}天旅行计划。

    **基本信息：**
    - 城市：{request.city}
    - 日期：{request.start_date} 至 {request.end_date}
    - 天数：{request.travel_days}天
    - 交通方式：{request.transportation}
    - 住宿偏好：{request.accommodation}
    - 旅行偏好：{", ".join(request.preferences) if request.preferences else "无"}

    **高德真实候选景点：**
    {attraction_json}

    **高德真实天气：**
    {weather_json}

    **高德真实候选酒店：**
    {hotel_json}

    **高德真实候选餐厅：**
    {restaurant_json}

    **专业控制 Agent 的并行决策：**
    {decision_json}

    **行程要求：**
    1. 普通游览日安排2至4个景点，景点游览时长合计应达到420至540分钟。
    2. suggested_visit_duration达到420分钟的全天型景点可以单独安排一天。
    3. 短时景点不能单独占用全天，必须搭配同区域景点填满上午和下午。
    4. 同一景点不能跨天重复安排。
    5. 每天必须包含早餐、午餐和晚餐，餐厅只能来自候选餐厅。
    6. 每天推荐一个候选列表中的具体酒店。
    7. 根据景点位置减少往返和跨区域移动。
    8. 根据用户交通方式预留合理通勤时间。
    9. 根据天气 Agent 的建议调整室内外景点顺序。
    10. 提供具体、可执行的当日行程描述。
    11. 返回符合 TripPlan 模型的完整 JSON，不输出解释文字。

    **真实性要求：**
    1. 景点、酒店和餐厅只能来自候选数据。
    2. 必须原样返回对应的 poi_id、名称、地址和 location。
    3. 餐厅必须返回 poi_id、名称、地址和 location。
    4. 不得编造或修正经纬度。
    5. 不得编造真实天气；没有对应日期时保持 weather_info 为空。
    6. 专业 Agent 决策是建议，候选数据才是事实来源。
    7. routes、timeline 和 schedule_status 字段不需要生成，由后端确定性优化器补充。

    **预算要求：**
    1. 为每个景点提供 ticket_price。
    2. 为每餐提供 estimated_cost。
    3. 为酒店提供 estimated_cost。
    4. budget 必须包含景点、酒店、餐饮、交通和总费用。
    5. 各预算汇总值必须与每日明细基本一致。
    """

        if request.free_text_input:
            query += f"\n**用户额外要求：**\n{request.free_text_input}"

        return query
    
    def _parse_response(self, response: str, request: TripRequest) -> TripPlan:
        """
        解析Agent响应
        
        Args:
            response: Agent响应文本
            request: 原始请求
            
        Returns:
            旅行计划
        """
        try:
            # 尝试从响应中提取JSON
            # 查找JSON代码块
            if "```json" in response:
                json_start = response.find("```json") + 7
                json_end = response.find("```", json_start)
                json_str = response[json_start:json_end].strip()
            elif "```" in response:
                json_start = response.find("```") + 3
                json_end = response.find("```", json_start)
                json_str = response[json_start:json_end].strip()
            elif "{" in response and "}" in response:
                # 直接查找JSON对象
                json_start = response.find("{")
                json_end = response.rfind("}") + 1
                json_str = response[json_start:json_end]
            else:
                raise ValueError("响应中未找到JSON数据")
            
            # 解析JSON
            data = json.loads(json_str)
            
            # 转换为TripPlan对象
            trip_plan = TripPlan(**data)
            
            return trip_plan
            
        except Exception as e:
            raise ValueError(f"Planner响应解析失败: {e}") from e
            # return self._create_fallback_plan(request)
    
    def _create_fallback_plan(self, request: TripRequest) -> TripPlan:
        """创建备用计划(当Agent失败时)"""
        from datetime import datetime, timedelta
        
        # 解析日期
        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")
        
        # 创建每日行程
        days = []
        for i in range(request.travel_days):
            current_date = start_date + timedelta(days=i)
            
            day_plan = DayPlan(
                date=current_date.strftime("%Y-%m-%d"),
                day_index=i,
                description=f"第{i+1}天行程",
                transportation=request.transportation,
                accommodation=request.accommodation,
                attractions=[
                    Attraction(
                        name=f"{request.city}景点{j+1}",
                        address=f"{request.city}市",
                        location=Location(longitude=116.4 + i*0.01 + j*0.005, latitude=39.9 + i*0.01 + j*0.005),
                        visit_duration=120,
                        description=f"这是{request.city}的著名景点",
                        category="景点"
                    )
                    for j in range(2)
                ],
                meals=[
                    Meal(type="breakfast", name=f"第{i+1}天早餐", description="当地特色早餐"),
                    Meal(type="lunch", name=f"第{i+1}天午餐", description="午餐推荐"),
                    Meal(type="dinner", name=f"第{i+1}天晚餐", description="晚餐推荐")
                ]
            )
            days.append(day_plan)
        
        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            weather_info=[],
            overall_suggestions=f"这是为您规划的{request.city}{request.travel_days}日游行程,建议提前查看各景点的开放时间。"
        )


# 全局多智能体系统实例
_multi_agent_planner = None


def get_trip_planner_agent() -> MultiAgentTripPlanner:
    """获取多智能体旅行规划系统实例(单例模式)"""
    global _multi_agent_planner

    if _multi_agent_planner is None:
        _multi_agent_planner = MultiAgentTripPlanner()

    return _multi_agent_planner

