"""旅行计划异步任务服务的单元测试。"""

import pytest

from app.jobs.models import TripJobRecord, TripJobStage, TripJobStatus
from app.jobs.repository import InMemoryTripJobRepository
from app.jobs.service import (
    TripJobIdempotencyConflictError,
    TripJobQueueUnavailableError,
    TripJobService,
)
from app.models.schemas import TripRequest


class FakeTripJobQueue:
    """记录投递与撤销操作的可控测试队列。"""

    def __init__(
        self,
        fail_enqueue: bool = False,
        fail_cancel: bool = False,
    ) -> None:
        """
        初始化测试队列。

        Args:
            fail_enqueue: 是否在投递任务时模拟 broker 故障。
            fail_cancel: 是否在撤销任务时模拟 broker 故障。
        """
        self.fail_enqueue = fail_enqueue
        self.fail_cancel = fail_cancel
        self.enqueued_job_ids: list[str] = []
        self.cancelled_task_ids: list[str] = []

    def enqueue(self, job_id: str, task_id: str) -> None:
        """
        记录使用预生成队列ID执行的任务投递。

        Args:
            job_id: 公开任务ID。
            task_id: 已写入任务记录的队列内部ID。

        Returns:
            None。

        Raises:
            RuntimeError: 测试配置要求模拟 broker 故障时抛出。
        """
        if self.fail_enqueue:
            raise RuntimeError("broker unavailable")
        self.enqueued_job_ids.append(job_id)

    def cancel(self, task_id: str) -> None:
        """
        记录被撤销的测试队列任务ID。

        Args:
            task_id: 队列内部任务ID。
        """
        if self.fail_cancel:
            raise RuntimeError("broker unavailable")
        self.cancelled_task_ids.append(task_id)


def build_request(city: str = "北京") -> TripRequest:
    """
    构造异步任务测试使用的最小合法旅行请求。

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
        preferences=[],
    )


def build_service(
    fail_enqueue: bool = False,
    fail_cancel: bool = False,
) -> tuple[TripJobService, InMemoryTripJobRepository, FakeTripJobQueue]:
    """
    构造带显式内存仓储和测试队列的任务服务。

    Args:
        fail_enqueue: 是否模拟队列投递失败。
        fail_cancel: 是否模拟队列撤销失败。

    Returns:
        任务服务、仓储和队列组成的元组。
    """
    repository = InMemoryTripJobRepository()
    queue = FakeTripJobQueue(
        fail_enqueue=fail_enqueue,
        fail_cancel=fail_cancel,
    )
    return TripJobService(repository, queue), repository, queue


def test_submit_is_idempotent_for_same_request() -> None:
    """相同幂等键和请求只能投递一次付费生成任务。"""
    service, _, queue = build_service()
    request = build_request()

    first, first_created = service.submit(request, "browser-request-1")
    second, second_created = service.submit(request, "browser-request-1")

    assert first_created is True
    assert second_created is False
    assert second.job_id == first.job_id
    assert queue.enqueued_job_ids == [first.job_id]


def test_submit_rejects_idempotency_key_reuse_for_different_request() -> None:
    """同一幂等键对应不同请求时必须拒绝，避免返回错误用户结果。"""
    service, _, _ = build_service()
    service.submit(build_request("北京"), "browser-request-2")

    with pytest.raises(TripJobIdempotencyConflictError):
        service.submit(build_request("上海"), "browser-request-2")


def test_enqueue_failure_is_persisted_as_terminal_failure() -> None:
    """broker 投递失败时任务应保留稳定错误码并进入失败终态。"""
    service, repository, _ = build_service(fail_enqueue=True)

    with pytest.raises(TripJobQueueUnavailableError):
        service.submit(build_request(), "browser-request-3")

    record = next(iter(repository._records.values()))
    assert record.status == TripJobStatus.FAILED
    assert record.stage == TripJobStage.FAILED
    assert record.error_code == "QUEUE_UNAVAILABLE"


def test_cancel_queued_job_immediately_marks_it_cancelled() -> None:
    """尚未执行的任务被撤销后应直接进入取消终态。"""
    service, _, queue = build_service()
    record, _ = service.submit(build_request())

    cancelled = service.cancel(record.job_id)

    assert cancelled.status == TripJobStatus.CANCELLED
    assert cancelled.stage == TripJobStage.CANCELLED
    assert queue.cancelled_task_ids == [f"trip-{record.job_id}"]


def test_cancel_running_job_sets_cooperative_cancel_marker() -> None:
    """运行中任务取消时应等待规划器在下一个阶段边界安全停止。"""
    service, repository, queue = build_service()
    record, _ = service.submit(build_request())
    repository.update(
        record.job_id,
        status=TripJobStatus.RUNNING,
        stage=TripJobStage.RUNNING_CONTROL_AGENTS,
        progress=22,
    )

    cancelled = service.cancel(record.job_id)

    assert cancelled.status == TripJobStatus.CANCEL_REQUESTED
    assert cancelled.stage == TripJobStage.RUNNING_CONTROL_AGENTS
    assert queue.cancelled_task_ids == [f"trip-{record.job_id}"]


def test_cancel_marker_survives_queue_revoke_failure() -> None:
    """broker 撤销失败时 Redis 取消终态仍应成功保存。"""
    service, repository, _ = build_service(fail_cancel=True)
    record, _ = service.submit(build_request())

    cancelled = service.cancel(record.job_id)

    assert cancelled.status == TripJobStatus.CANCELLED
    assert repository.get(record.job_id).status == TripJobStatus.CANCELLED


def test_repository_updates_increment_revision_without_losing_fields() -> None:
    """部分状态更新必须保留任务请求并递增 SSE 使用的修订号。"""
    _, repository, _ = build_service()
    initial, _ = repository.create_or_get(
        TripJobRecord.create(build_request())
    )

    updated = repository.update(initial.job_id, progress=42)

    assert updated.progress == 42
    assert updated.revision == initial.revision + 1
    assert updated.request == initial.request


def test_attempt_claim_allows_only_bounded_worker_recovery() -> None:
    """首次执行与一次 worker 恢复后，第三次领取必须在调用 Agent 前失败。"""
    service, repository, _ = build_service()
    record, _ = service.submit(build_request())

    first, first_claimed = repository.begin_attempt(
        record.job_id,
        record.task_id,
        allow_running_reclaim=False,
    )
    duplicate, duplicate_claimed = repository.begin_attempt(
        record.job_id,
        record.task_id,
        allow_running_reclaim=False,
    )
    recovered, recovered_claimed = repository.begin_attempt(
        record.job_id,
        record.task_id,
        allow_running_reclaim=True,
    )
    exhausted, exhausted_claimed = repository.begin_attempt(
        record.job_id,
        record.task_id,
        allow_running_reclaim=True,
    )

    assert first_claimed is True
    assert first.attempt_count == 1
    assert duplicate_claimed is False
    assert duplicate.attempt_count == 1
    assert recovered_claimed is True
    assert recovered.attempt_count == 2
    assert exhausted_claimed is False
    assert exhausted.status == TripJobStatus.FAILED
    assert exhausted.error_code == "ATTEMPT_LIMIT_EXCEEDED"
    assert exhausted.finished_at is not None


def test_service_persists_configured_attempt_budget() -> None:
    """每个任务必须保存提交时生效的 worker 最大执行次数。"""
    repository = InMemoryTripJobRepository()
    queue = FakeTripJobQueue()
    service = TripJobService(repository, queue, max_attempts=1)

    record, _ = service.submit(build_request())

    assert record.max_attempts == 1
