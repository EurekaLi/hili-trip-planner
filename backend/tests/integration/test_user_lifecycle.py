"""用户、行程版本、编辑、分享与导出闭环的集成测试。"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import auth, library
from app.models.library_schemas import (
    DeleteAttractionOperation,
    LockAttractionOperation,
    MoveAttractionOperation,
)
from app.models.schemas import (
    Attraction,
    DayPlan,
    ExternalCallMetric,
    Location,
    Meal,
    POIInfo,
    TimelineEntry,
    TripPlan,
    TripRequest,
)
from app.persistence.database import (
    build_engine,
    build_session_factory,
    init_database,
)
from app.services.auth_service import AuthService
from app.services.calendar_service import build_trip_calendar
from app.services.trip_library_service import (
    OwnerIdentity,
    TripLibraryService,
    TripNotFoundError,
    TripVersionConflictError,
)
from app.services.trip_mutation_service import TripMutationError, TripMutationService
from app.services.platform_services import get_auth_service, get_trip_library_service


class FakeMapProvider:
    """为编辑测试提供无费用、可重复的路线和 POI 数据。"""

    def __init__(self) -> None:
        """初始化为空的当前调用 trace。"""
        self._active_metrics: Optional[List[ExternalCallMetric]] = None

    def begin_call_trace(
        self,
    ) -> Tuple[List[ExternalCallMetric], Optional[List[ExternalCallMetric]]]:
        """
        开启测试调用 trace 并返回恢复令牌。

        Returns:
            新指标列表和先前列表组成的元组。
        """
        previous = self._active_metrics
        self._active_metrics = []
        return self._active_metrics, previous

    def end_call_trace(
        self,
        token: Optional[List[ExternalCallMetric]],
    ) -> None:
        """
        恢复测试 provider 的先前调用 trace。

        Args:
            token: `begin_call_trace` 返回的先前列表。
        """
        self._active_metrics = token

    def plan_route_by_coordinates(
        self,
        origin: Location,
        destination: Location,
        route_type: str = "walking",
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
    ) -> Dict:
        """
        返回包含几何形态的固定十分钟路线。

        Args:
            origin: 起点坐标。
            destination: 终点坐标。
            route_type: 请求交通方式。
            origin_city: 可选起点城市。
            destination_city: 可选终点城市。

        Returns:
            优化器可解析的路线字典。
        """
        if self._active_metrics is not None:
            self._active_metrics.append(
                ExternalCallMetric(
                    provider="fake-map",
                    operation="route",
                    succeeded=True,
                    elapsed_seconds=0.01,
                    requested_at=datetime.now(timezone.utc),
                )
            )
        return {
            "distance": 1200,
            "duration": 600,
            "route_type": route_type,
            "description": "测试路线",
            "geometry": [origin.model_dump(), destination.model_dump()],
        }

    def search_poi(
        self,
        keywords: str,
        city: str,
        citylimit: bool = True,
        limit: int = 6,
    ) -> List[POIInfo]:
        """
        返回单个已知 POI 候选。

        Args:
            keywords: 搜索关键词。
            city: 目标城市。
            citylimit: 是否限制目标城市。
            limit: 最大候选数量。

        Returns:
            一个稳定的真实候选替身。
        """
        return [
            POIInfo(
                id="replacement",
                name=f"{keywords}候选",
                type="风景名胜",
                address=f"{city}测试地址",
                location=Location(longitude=116.45, latitude=39.95),
            )
        ][:limit]


def build_attraction(poi_id: str, longitude: float) -> Attraction:
    """
    构造具有稳定 POI ID 的测试景点。

    Args:
        poi_id: 景点唯一标识。
        longitude: 用于形成不同路线的经度。

    Returns:
        可供编辑和优化服务使用的景点。
    """
    return Attraction(
        name=f"景点{poi_id}",
        address=f"地址{poi_id}",
        location=Location(longitude=longitude, latitude=39.9),
        visit_duration=180,
        description="测试景点",
        poi_id=poi_id,
        ticket_price=20,
    )


def build_plan() -> TripPlan:
    """
    构造两天、每天两个景点的可编辑计划。

    Returns:
        包含时间轴和餐费的完整旅行计划。
    """
    days = []
    for day_index in range(2):
        first_hour = 9 + day_index
        days.append(
            DayPlan(
                date=f"2026-09-{15 + day_index:02d}",
                day_index=day_index,
                description="测试行程",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[
                    build_attraction(f"p{day_index}-a", 116.30 + day_index * 0.1),
                    build_attraction(f"p{day_index}-b", 116.32 + day_index * 0.1),
                ],
                meals=[
                    Meal(
                        type="lunch",
                        name=f"餐厅{day_index}",
                        estimated_cost=50,
                    )
                ],
                timeline=[
                    TimelineEntry(
                        kind="attraction",
                        start_time=f"{first_hour:02d}:00",
                        end_time=f"{first_hour + 2:02d}:00",
                        title=f"第{day_index + 1}天景点",
                    )
                ],
            )
        )
    return TripPlan(
        city="北京",
        start_date="2026-09-15",
        end_date="2026-09-16",
        days=days,
        overall_suggestions="测试建议",
    )


@pytest.fixture
def lifecycle_services(tmp_path: Path):
    """
    创建使用隔离 SQLite 文件的认证和行程服务。

    Args:
        tmp_path: Pytest 为当前测试提供的临时目录。

    Yields:
        认证服务、行程服务和底层会话工厂。
    """
    database_path = (tmp_path / "lifecycle.db").as_posix()
    engine = build_engine(f"sqlite:///{database_path}")
    init_database(engine)
    session_factory = build_session_factory(engine)
    auth_service = AuthService(session_factory, session_ttl_days=30)
    map_provider = FakeMapProvider()
    library_service = TripLibraryService(
        session_factory,
        TripMutationService(map_provider, map_provider),
    )
    yield auth_service, library_service, session_factory
    engine.dispose()


def test_guest_trips_are_claimed_during_registration(lifecycle_services) -> None:
    """游客注册后应在同一事务中认领已有行程并恢复会话。"""
    auth_service, library_service, _ = lifecycle_services
    guest = OwnerIdentity(guest_id="guest-1234567890abcdef")
    saved = library_service.create_trip(guest, build_plan())

    user, token, claimed = auth_service.register(
        "LEARNER@example.com",
        "strong-password",
        "学习者",
        guest.guest_id,
    )

    assert claimed == 1
    assert auth_service.resolve_user(token).id == user.id
    assert library_service.get_trip(OwnerIdentity(user_id=user.id), saved.id).id == saved.id


def test_pending_guest_job_archives_to_claiming_user(lifecycle_services) -> None:
    """注册发生在生成期间时，worker 归档应采用 SQL 中最新用户所有者。"""
    auth_service, library_service, _ = lifecycle_services
    guest = OwnerIdentity(guest_id="guest-1234567890abcdef")
    request = TripRequest(
        city="北京",
        start_date="2026-09-15",
        end_date="2026-09-16",
        travel_days=2,
        transportation="公共交通",
        accommodation="经济型酒店",
    )
    now = datetime.now(timezone.utc)
    library_service.record_job_submission(
        "job-claim-race",
        guest,
        request,
        "queued",
        now,
        now,
    )
    user, _, _ = auth_service.register(
        "race@example.com",
        "strong-password",
        "竞态用户",
        guest.guest_id,
    )

    saved = library_service.archive_generated_trip(
        "job-claim-race",
        guest,
        request,
        build_plan(),
        now,
        now,
        now,
    )

    assert library_service.get_trip(OwnerIdentity(user_id=user.id), saved.id).id == saved.id


def test_editing_creates_versions_and_rejects_stale_or_locked_changes(
    lifecycle_services,
) -> None:
    """编辑应重算派生数据、追加版本并执行锁定和乐观并发控制。"""
    _, library_service, _ = lifecycle_services
    owner = OwnerIdentity(guest_id="guest-1234567890abcdef")
    saved = library_service.create_trip(owner, build_plan())
    moved = library_service.edit_trip(
        owner,
        saved.id,
        base_version=1,
        operations=[
            MoveAttractionOperation(
                type="move",
                poi_id="p0-b",
                target_day_index=1,
                target_position=0,
            )
        ],
        change_summary="跨天移动景点",
    )

    assert moved.current_version == 2
    assert moved.plan.days[1].attractions[0].poi_id == "p0-b"
    assert moved.plan.days[1].routes
    assert moved.plan.budget is not None
    assert any(
        metric.provider == "fake-map"
        for metric in moved.plan.external_calls
    )

    with pytest.raises(TripVersionConflictError):
        library_service.edit_trip(
            owner,
            saved.id,
            base_version=1,
            operations=[DeleteAttractionOperation(type="delete", poi_id="p0-a")],
            change_summary="过期编辑",
        )

    locked = library_service.edit_trip(
        owner,
        saved.id,
        base_version=2,
        operations=[
            LockAttractionOperation(type="lock", poi_id="p0-a", locked=True)
        ],
        change_summary="锁定景点",
    )
    with pytest.raises(TripMutationError):
        library_service.edit_trip(
            owner,
            saved.id,
            base_version=locked.current_version,
            operations=[DeleteAttractionOperation(type="delete", poi_id="p0-a")],
            change_summary="删除锁定景点",
        )


def test_share_is_fixed_to_version_and_accepts_feedback(lifecycle_services) -> None:
    """分享应固定历史快照，并允许访问者提交持久化反馈。"""
    _, library_service, _ = lifecycle_services
    owner = OwnerIdentity(guest_id="guest-1234567890abcdef")
    saved = library_service.create_trip(owner, build_plan())
    share = library_service.create_share(owner, saved.id, 1, 30)
    library_service.edit_trip(
        owner,
        saved.id,
        base_version=1,
        operations=[DeleteAttractionOperation(type="delete", poi_id="p0-b")],
        change_summary="删除景点",
    )

    shared = library_service.get_shared_trip(share.share_token)
    feedback = library_service.add_feedback(
        share.share_token,
        OwnerIdentity(guest_id="viewer-1234567890abcd"),
        5,
        "计划很实用",
    )

    assert shared.version_number == 1
    assert len(shared.plan.days[0].attractions) == 2
    assert feedback.rating == 5
    library_service.revoke_share(owner, saved.id, share.share_id)
    with pytest.raises(TripNotFoundError, match="分享链接不存在或已失效"):
        library_service.get_shared_trip(share.share_token)


def test_calendar_export_contains_timeline_events() -> None:
    """日历导出应为每个时间轴活动生成稳定事件。"""
    calendar_text = build_trip_calendar(build_plan(), "trip-1")

    assert "BEGIN:VCALENDAR" in calendar_text
    assert "UID:trip-1-0-0@hiliagents.local" in calendar_text
    assert "DTSTART:20260915T090000" in calendar_text
    assert calendar_text.endswith("END:VCALENDAR\r\n")


def test_http_guest_register_restore_share_and_feedback_flow(
    lifecycle_services,
) -> None:
    """HTTP 层应贯通游客保存、注册认领、分享、反馈和日历导出。"""
    auth_service, library_service, _ = lifecycle_services
    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    app.include_router(library.router, prefix="/api")
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_trip_library_service] = lambda: library_service
    client = TestClient(app)
    guest_headers = {"X-Guest-ID": "guest-http-1234567890"}

    created = client.post(
        "/api/trips",
        headers=guest_headers,
        json={"plan": build_plan().model_dump(mode="json")},
    )
    assert created.status_code == 201
    trip_id = created.json()["id"]

    registered = client.post(
        "/api/auth/register",
        headers=guest_headers,
        json={
            "email": "flow@example.com",
            "password": "strong-password",
            "display_name": "流程用户",
        },
    )
    assert registered.status_code == 201
    assert registered.json()["claimed_trip_count"] == 1

    listed = client.get("/api/trips", headers=guest_headers)
    assert listed.status_code == 200
    assert [trip["id"] for trip in listed.json()] == [trip_id]

    share = client.post(
        f"/api/trips/{trip_id}/shares",
        headers=guest_headers,
        json={"expires_in_days": 30},
    )
    assert share.status_code == 201
    token = share.json()["share_token"]
    assert client.get(f"/api/shares/{token}").status_code == 200
    feedback = client.post(
        f"/api/shares/{token}/feedback",
        headers={"X-Guest-ID": "viewer-http-12345678"},
        json={"rating": 5, "comment": "完整可用"},
    )
    assert feedback.status_code == 201

    calendar = client.get(
        f"/api/trips/{trip_id}/calendar.ics",
        headers=guest_headers,
    )
    assert calendar.status_code == 200
    assert "BEGIN:VCALENDAR" in calendar.text
