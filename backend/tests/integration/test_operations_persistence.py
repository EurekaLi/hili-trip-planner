"""运营聚合、外部调用审计和隐私删除的持久化集成测试。"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.observability import observation_context
from app.persistence.database import Base, build_engine, build_session_factory
from app.persistence.models import (
    ExternalCallRecord,
    TripFeedbackRecord,
    TripGenerationJobRecord,
    TripRecord,
    TripVersionRecord,
)
from app.services import external_call_audit_service as audit_module
from app.services.auth_service import AuthenticationError, AuthService
from app.services.external_call_audit_service import record_external_call_audit
from app.services.operations_service import OperationsService


@pytest.fixture()
def session_factory(tmp_path):
    """创建启用真实外键约束的隔离 SQLite 会话工厂。"""
    engine = build_engine(f"sqlite:///{tmp_path / 'operations.db'}")
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


def test_operations_overview_and_job_trace(session_factory) -> None:
    """运营服务应聚合成功率、P95、费用并按请求 ID 追踪任务。"""
    now = datetime.now(timezone.utc)
    success = TripGenerationJobRecord(
        job_id="job-success",
        request_id="request-success",
        trip_id="trip-success",
        status="succeeded",
        request_json={"city": "北京", "travel_days": 1},
        budget_json={"llm_calls": 4, "external_calls": 8},
        created_at=now - timedelta(seconds=12),
        updated_at=now,
        finished_at=now,
    )
    failed = TripGenerationJobRecord(
        job_id="job-failed",
        request_id="request-failed",
        status="failed",
        request_json={"city": "上海", "travel_days": 2},
        budget_json={"llm_calls": 3, "external_calls": 5},
        error_code="GENERATION_TIMEOUT",
        error_message="上游调用超时",
        created_at=now - timedelta(seconds=30),
        updated_at=now,
        finished_at=now,
    )
    trip = TripRecord(
        id="trip-success",
        source_job_id="job-success",
        guest_id="operations-guest",
        title="北京一日行程",
        city="北京",
        current_version=1,
        locked_poi_ids=[],
    )
    with session_factory() as session:
        session.add_all([success, failed])
        session.flush()
        session.add(trip)
        session.flush()
        session.add_all(
            [
                TripVersionRecord(
                    trip_id=trip.id,
                    version_number=1,
                    plan_json={"agent_trace": [{"controller": "planner"}]},
                    change_type="generated",
                    change_summary="测试",
                ),
                ExternalCallRecord(
                    job_id=success.job_id,
                    trip_id=trip.id,
                    provider="llm",
                    operation="planner",
                    succeeded=True,
                    elapsed_seconds=2.5,
                    units=1,
                    cost_cny=0.12,
                    requested_at=now,
                ),
                ExternalCallRecord(
                    job_id=failed.job_id,
                    provider="amap",
                    operation="plan_route",
                    succeeded=False,
                    elapsed_seconds=20,
                    units=1,
                    error_type="TimeoutError",
                    requested_at=now,
                ),
                TripFeedbackRecord(
                    trip_id=trip.id,
                    guest_id="feedback-guest",
                    rating=5,
                    comment="很好",
                ),
            ]
        )
        session.commit()

    service = OperationsService(session_factory)
    overview = service.overview(24)
    trace = service.job_trace("request-success")

    assert overview.jobs_total == 2
    assert overview.success_rate == 0.5
    assert overview.known_cost_cny == pytest.approx(0.12)
    assert overview.failure_counts[0].name == "GENERATION_TIMEOUT"
    assert overview.feedback_count == 1
    assert trace is not None
    assert trace.job_id == "job-success"
    assert trace.external_calls[0].operation == "planner"
    assert trace.agent_trace[0]["controller"] == "planner"


def test_external_call_audit_links_current_job(
    session_factory,
    monkeypatch,
) -> None:
    """外部调用审计应读取当前 job 上下文并保留稳定错误分类。"""
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            TripGenerationJobRecord(
                job_id="job-audit",
                request_id="request-audit",
                status="running",
                request_json={"city": "北京"},
                budget_json={},
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    monkeypatch.setattr(audit_module, "SessionLocal", session_factory)

    with observation_context(job_id="job-audit"):
        record_external_call_audit(
            provider="amap",
            operation="maps_weather",
            succeeded=False,
            elapsed_seconds=1.25,
            error_type="TimeoutError",
        )

    with session_factory() as session:
        record = session.scalar(select(ExternalCallRecord))

    assert record is not None
    assert record.job_id == "job-audit"
    assert record.operation == "maps_weather"
    assert record.error_type == "TimeoutError"
    assert record.elapsed_seconds == pytest.approx(1.25)


def test_account_deletion_requires_password_and_removes_owned_data(
    session_factory,
) -> None:
    """账号删除应再次认证，并清理账号拥有的任务和行程。"""
    service = AuthService(session_factory=session_factory, session_ttl_days=30)
    user, _, _ = service.register(
        "operations@example.com",
        "strong-password",
        "Operations User",
    )
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            TripGenerationJobRecord(
                job_id="owned-job",
                owner_user_id=user.id,
                status="failed",
                request_json={"city": "北京"},
                budget_json={},
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            TripRecord(
                id="owned-trip",
                owner_user_id=user.id,
                title="待删除行程",
                city="北京",
                current_version=1,
                locked_poi_ids=[],
            )
        )
        session.commit()

    with pytest.raises(AuthenticationError):
        service.delete_account(user.id, "wrong-password")
    deleted = service.delete_account(user.id, "strong-password")

    assert deleted["trips"] == 1
    assert deleted["jobs"] == 1
    assert service.resolve_user(None) is None
