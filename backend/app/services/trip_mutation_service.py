"""应用结构化编辑操作并重新计算行程派生数据。"""

from datetime import datetime, timezone
from typing import Any, Iterable, List, Protocol, Sequence, Tuple

from ..config import get_settings
from ..models.library_schemas import (
    DeleteAttractionOperation,
    LockAttractionOperation,
    MoveAttractionOperation,
    ReplaceAttractionOperation,
    TripEditOperation,
    UpdateAttractionOperation,
)
from ..models.schemas import (
    Attraction,
    FactQuality,
    FactSource,
    POIInfo,
    TripPlan,
    TripRequest,
)
from .budget_service import recalculate_budget
from .itinerary_optimizer import ItineraryOptimizer, RouteProvider, build_visit_constraints


class POISearchProvider(Protocol):
    """景点替换所需的最小真实 POI 查询接口。"""

    def search_poi(
        self,
        keywords: str,
        city: str,
        citylimit: bool = True,
        limit: int = 6,
    ) -> List[POIInfo]:
        """
        查询指定城市中的真实 POI。

        Args:
            keywords: 搜索关键词。
            city: 目标城市。
            citylimit: 是否限制在目标城市。
            limit: 最大候选数。

        Returns:
            真实 POI 候选列表。
        """


class TripMutationError(ValueError):
    """编辑操作引用无效或锁定景点。"""


class TripMutationService:
    """执行行程编辑并刷新路线、时间轴和预算。"""

    def __init__(
        self,
        route_provider: RouteProvider,
        poi_provider: POISearchProvider,
    ) -> None:
        """
        初始化地图服务依赖。

        Args:
            route_provider: 路线和 geometry 查询服务。
            poi_provider: 替换景点时使用的真实 POI 服务。
        """
        self._route_provider = route_provider
        self._poi_provider = poi_provider

    def apply(
        self,
        plan: TripPlan,
        locked_poi_ids: Sequence[str],
        operations: Iterable[TripEditOperation],
    ) -> Tuple[TripPlan, List[str]]:
        """
        原子应用编辑操作并刷新全部派生字段。

        Args:
            plan: 当前不可变版本的完整计划。
            locked_poi_ids: 当前已锁定的 POI 标识。
            operations: 按顺序执行的结构化编辑操作。

        Returns:
            新旅行计划和去重后的锁定 POI 标识。

        Raises:
            TripMutationError: 操作引用不存在、锁定或不合法的景点时抛出。
        """
        updated = plan.model_copy(deep=True)
        locked = set(locked_poi_ids)
        trace_contexts = self._begin_call_traces()
        try:
            for operation in operations:
                if isinstance(operation, LockAttractionOperation):
                    self._apply_lock(updated, locked, operation)
                elif isinstance(operation, MoveAttractionOperation):
                    self._ensure_unlocked(operation.poi_id, locked)
                    self._apply_move(updated, operation)
                elif isinstance(operation, DeleteAttractionOperation):
                    self._ensure_unlocked(operation.poi_id, locked)
                    self._apply_delete(updated, operation)
                elif isinstance(operation, UpdateAttractionOperation):
                    self._ensure_unlocked(operation.poi_id, locked)
                    self._apply_update(updated, operation)
                elif isinstance(operation, ReplaceAttractionOperation):
                    self._ensure_unlocked(operation.poi_id, locked)
                    self._apply_replace(updated, operation)

            request = self._build_request(updated)
            settings = get_settings()
            optimizer = ItineraryOptimizer(
                route_provider=self._route_provider,
                max_route_calls=settings.itinerary_route_call_budget,
                cross_region_minutes=settings.itinerary_cross_region_minutes,
            )
            refreshed = optimizer.refresh(updated, request)
            edit_metrics = [
                metric
                for _, _, metrics in trace_contexts
                for metric in metrics
            ]
            refreshed.external_calls = [*updated.external_calls, *edit_metrics]
            refreshed.budget = recalculate_budget(refreshed)
            return refreshed, sorted(locked)
        finally:
            self._end_call_traces(trace_contexts)

    def _begin_call_traces(self) -> List[Tuple[Any, Any, list]]:
        """
        为具有调用追踪能力的唯一外部 provider 开启本轮编辑 trace。

        Returns:
            provider、恢复令牌和可变指标列表组成的上下文列表。
        """
        contexts: List[Tuple[Any, Any, list]] = []
        unique_providers = {
            id(provider): provider
            for provider in (self._route_provider, self._poi_provider)
        }
        for provider in unique_providers.values():
            begin_trace = getattr(provider, "begin_call_trace", None)
            end_trace = getattr(provider, "end_call_trace", None)
            if callable(begin_trace) and callable(end_trace):
                metrics, token = begin_trace()
                contexts.append((provider, token, metrics))
        return contexts

    @staticmethod
    def _end_call_traces(contexts: List[Tuple[Any, Any, list]]) -> None:
        """
        按逆序恢复所有 provider 的调用追踪上下文。

        Args:
            contexts: `_begin_call_traces` 返回的上下文列表。
        """
        for provider, token, _ in reversed(contexts):
            provider.end_call_trace(token)

    def _apply_lock(
        self,
        plan: TripPlan,
        locked: set[str],
        operation: LockAttractionOperation,
    ) -> None:
        """
        锁定或解锁一个确实存在的景点。

        Args:
            plan: 正在编辑的计划。
            locked: 可原地更新的锁定标识集合。
            operation: 锁定操作。
        """
        attraction, _, _ = self._find_attraction(plan, operation.poi_id)
        key = self._attraction_key(attraction)
        if operation.locked:
            locked.add(key)
        else:
            locked.discard(key)

    def _apply_move(
        self,
        plan: TripPlan,
        operation: MoveAttractionOperation,
    ) -> None:
        """
        把景点移动到合法目标日期和位置。

        Args:
            plan: 正在编辑的计划。
            operation: 跨天或日内移动操作。

        Raises:
            TripMutationError: 目标日期超出行程范围时抛出。
        """
        if operation.target_day_index >= len(plan.days):
            raise TripMutationError("目标日期超出行程范围")
        attraction, source_day, source_index = self._find_attraction(
            plan,
            operation.poi_id,
        )
        source_day.attractions.pop(source_index)
        target_day = plan.days[operation.target_day_index]
        target_position = min(operation.target_position, len(target_day.attractions))
        target_day.attractions.insert(target_position, attraction)

    def _apply_delete(
        self,
        plan: TripPlan,
        operation: DeleteAttractionOperation,
    ) -> None:
        """
        删除一个景点。

        Args:
            plan: 正在编辑的计划。
            operation: 删除操作。
        """
        _, day, index = self._find_attraction(plan, operation.poi_id)
        day.attractions.pop(index)

    def _apply_update(
        self,
        plan: TripPlan,
        operation: UpdateAttractionOperation,
    ) -> None:
        """
        更新一个景点允许用户修改的字段。

        Args:
            plan: 正在编辑的计划。
            operation: 字段修改操作。
        """
        attraction, _, _ = self._find_attraction(plan, operation.poi_id)
        if operation.visit_duration is not None:
            attraction.visit_duration = operation.visit_duration
        if operation.description is not None:
            attraction.description = operation.description.strip()

    def _apply_replace(
        self,
        plan: TripPlan,
        operation: ReplaceAttractionOperation,
    ) -> None:
        """
        用服务端重新查询并匹配的真实 POI 替换景点。

        Args:
            plan: 正在编辑的计划。
            operation: 包含候选 POI ID 和关键词的替换操作。

        Raises:
            TripMutationError: 地图服务未返回指定真实 POI 时抛出。
        """
        _, day, index = self._find_attraction(plan, operation.poi_id)
        candidates = self._poi_provider.search_poi(
            operation.replacement_keyword,
            plan.city,
            citylimit=True,
            limit=10,
        )
        replacement = next(
            (
                candidate
                for candidate in candidates
                if candidate.id == operation.replacement_poi_id
            ),
            None,
        )
        if replacement is None:
            raise TripMutationError("替换景点不在地图服务返回的真实候选中")
        day.attractions[index] = self._poi_to_attraction(replacement)

    @staticmethod
    def _find_attraction(plan: TripPlan, poi_id: str):
        """
        在整个计划中定位唯一景点。

        Args:
            plan: 正在编辑的计划。
            poi_id: POI ID；旧数据缺少 ID 时允许使用名称。

        Returns:
            景点、所属日期和日内下标。

        Raises:
            TripMutationError: 未找到或出现重复标识时抛出。
        """
        matches = []
        for day in plan.days:
            for index, attraction in enumerate(day.attractions):
                if poi_id in {attraction.poi_id, attraction.name}:
                    matches.append((attraction, day, index))
        if not matches:
            raise TripMutationError(f"未找到景点：{poi_id}")
        if len(matches) > 1:
            raise TripMutationError(f"景点标识不唯一：{poi_id}")
        return matches[0]

    @staticmethod
    def _ensure_unlocked(poi_id: str, locked: set[str]) -> None:
        """
        阻止修改用户已锁定的景点。

        Args:
            poi_id: 待修改景点标识。
            locked: 当前锁定标识集合。

        Raises:
            TripMutationError: 景点已锁定时抛出。
        """
        if poi_id in locked:
            raise TripMutationError("景点已锁定，请先解锁")

    @staticmethod
    def _attraction_key(attraction: Attraction) -> str:
        """
        获取可在旧数据上工作的稳定景点标识。

        Args:
            attraction: 目标景点。

        Returns:
            优先使用 POI ID，缺失时使用名称。
        """
        return attraction.poi_id or attraction.name

    @staticmethod
    def _poi_to_attraction(poi: POIInfo) -> Attraction:
        """
        把真实 POI 候选转换为行程景点。

        Args:
            poi: 地图服务返回的 POI。

        Returns:
            带事实来源和未知票价标记的景点。
        """
        return Attraction(
            name=poi.name,
            address=poi.address,
            location=poi.location,
            visit_duration=poi.suggested_visit_duration,
            description=f"用户选择替换为{poi.name}",
            category=poi.type,
            rating=poi.rating,
            photos=list(poi.photos),
            image_url=poi.photos[0] if poi.photos else None,
            poi_id=poi.id,
            source=poi.source,
            ticket_price=0,
            ticket_price_source=FactSource(
                provider="unknown",
                quality=FactQuality.UNKNOWN,
                retrieved_at=datetime.now(timezone.utc),
                note="替换景点缺少票务数据",
            ),
            visit_constraints=(
                poi.visit_constraints
                or build_visit_constraints(poi.name, poi.type)
            ),
        )

    @staticmethod
    def _build_request(plan: TripPlan) -> TripRequest:
        """
        从持久化计划重建确定性优化器需要的请求。

        Args:
            plan: 用户修改后的旅行计划。

        Returns:
            日期、交通和住宿信息一致的旅行请求。
        """
        first_day = plan.days[0] if plan.days else None
        return TripRequest(
            city=plan.city,
            start_date=plan.start_date,
            end_date=plan.end_date,
            travel_days=len(plan.days),
            transportation=(first_day.transportation if first_day else "公共交通"),
            accommodation=(first_day.accommodation if first_day else "经济型酒店"),
            preferences=[],
        )
