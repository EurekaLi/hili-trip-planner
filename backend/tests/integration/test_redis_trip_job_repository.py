"""Redis 旅行计划任务仓储的可选集成测试。"""

import os
from datetime import datetime, timezone
from typing import Generator, Tuple
from uuid import uuid4

import pytest
from redis import Redis

from app.jobs.models import TripJobRecord, TripJobStage, TripJobStatus
from app.jobs.repository import RedisTripJobRepository
from app.models.schemas import (
    Attraction,
    DayPlan,
    FactQuality,
    FactSource,
    Location,
    TripPlan,
    TripRequest,
    VisitConstraints,
)


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_REDIS_URL"),
    reason="未配置 TEST_REDIS_URL，跳过 Redis 集成测试",
)


@pytest.fixture
def redis_repository() -> Generator[
    Tuple[RedisTripJobRepository, str],
    None,
    None,
]:
    """
    创建使用独立键前缀的 Redis 仓储并在测试后清理数据。

    Yields:
        Redis 仓储与本次测试键前缀组成的元组。
    """
    redis_url = os.environ["TEST_REDIS_URL"]
    key_prefix = f"helloagents:test:{uuid4().hex}"
    repository = RedisTripJobRepository(redis_url, key_prefix, 60)
    try:
        yield repository, key_prefix
    finally:
        client = Redis.from_url(redis_url, decode_responses=True)
        keys = list(client.scan_iter(match=f"{key_prefix}:*"))
        if keys:
            client.delete(*keys)


def build_request(city: str = "北京") -> TripRequest:
    """
    构造 Redis 仓储集成测试使用的旅行请求。

    Args:
        city: 请求中的目的地城市。

    Returns:
        单日公共交通旅行请求。
    """
    return TripRequest(
        city=city,
        start_date="2026-09-15",
        end_date="2026-09-15",
        travel_days=1,
        transportation="公共交通",
        accommodation="经济型酒店",
    )


def test_redis_repository_preserves_idempotency_and_revisions(
    redis_repository: Tuple[RedisTripJobRepository, str],
) -> None:
    """真实 Redis 应只创建一次幂等任务并原子递增状态修订号。"""
    repository, _ = redis_repository
    initial = TripJobRecord.create(build_request())

    first, first_created = repository.create_or_get(initial, "redis-flow-1")
    second, second_created = repository.create_or_get(
        TripJobRecord.create(build_request()),
        "redis-flow-1",
    )
    first_attempt, first_claimed = repository.begin_attempt(
        first.job_id,
        first.task_id,
        allow_running_reclaim=False,
    )
    duplicate, duplicate_claimed = repository.begin_attempt(
        first.job_id,
        first.task_id,
        allow_running_reclaim=False,
    )
    recovered, recovered_claimed = repository.begin_attempt(
        first.job_id,
        first.task_id,
        allow_running_reclaim=True,
    )
    exhausted, exhausted_claimed = repository.begin_attempt(
        first.job_id,
        first.task_id,
        allow_running_reclaim=True,
    )

    assert repository.ping() is True
    assert first_created is True
    assert second_created is False
    assert second.job_id == first.job_id
    assert first_claimed is True
    assert first_attempt.revision == first.revision + 1
    assert duplicate_claimed is False
    assert duplicate.attempt_count == 1
    assert recovered_claimed is True
    assert recovered.attempt_count == 2
    assert exhausted_claimed is False
    assert exhausted.status == TripJobStatus.FAILED
    assert exhausted.error_code == "ATTEMPT_LIMIT_EXCEEDED"
    assert exhausted.request == first.request


def test_redis_repository_round_trips_nested_datetime_result(
    redis_repository: Tuple[RedisTripJobRepository, str],
) -> None:
    """真实Redis应完整保存嵌套事实时间和跨午夜营业约束。"""
    repository, _ = redis_repository
    record = TripJobRecord.create(build_request())
    repository.create_or_get(record)
    retrieved_at = datetime(2026, 9, 16, 1, 30, tzinfo=timezone.utc)
    plan = TripPlan(
        city="北京",
        start_date="2026-09-15",
        end_date="2026-09-15",
        days=[
            DayPlan(
                date="2026-09-15",
                day_index=0,
                description="Redis序列化测试",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[
                    Attraction(
                        name="夜间景点",
                        address="测试路1号",
                        location=Location(longitude=116.4, latitude=39.9),
                        visit_duration=120,
                        description="测试",
                        source=FactSource(
                            provider="amap",
                            quality=FactQuality.VERIFIED,
                            retrieved_at=retrieved_at,
                        ),
                        visit_constraints=VisitConstraints(
                            opening_time="18:00",
                            closing_time="01:00",
                            closing_day_offset=1,
                        ),
                    )
                ],
            )
        ],
        overall_suggestions="测试建议",
    )

    repository.update(
        record.job_id,
        status=TripJobStatus.SUCCEEDED,
        stage=TripJobStage.COMPLETED,
        progress=100,
        result=plan,
        finished_at=retrieved_at,
    )
    loaded = repository.get(record.job_id)

    assert loaded.result is not None
    attraction = loaded.result.days[0].attractions[0]
    assert attraction.source.retrieved_at == retrieved_at
    assert attraction.visit_constraints.closing_day_offset == 1
