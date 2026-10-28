"""旅行计划 Celery worker 的任务状态测试。"""

from typing import Optional

from app.agents.progress import TripProgressCallback
from app.jobs import tasks
from app.jobs.models import TripJobRecord, TripJobStage, TripJobStatus
from app.jobs.repository import InMemoryTripJobRepository
from app.models.schemas import TripPlan, TripRequest


class SuccessfulPlanner:
    """模拟上报真实阶段并返回结果的旅行规划器。"""

    def plan_trip(
        self,
        request: TripRequest,
        progress_callback: Optional[TripProgressCallback] = None,
    ) -> TripPlan:
        """
        上报两个阶段并构造最小旅行计划。

        Args:
            request: worker 从仓储读取的旅行请求。
            progress_callback: worker 注入的进度回调。

        Returns:
            与请求城市和日期一致的最小旅行计划。
        """
        if progress_callback:
            progress_callback(
                TripJobStage.RUNNING_CONTROL_AGENTS.value,
                22,
                "控制 Agent 正在决策",
            )
            progress_callback(
                TripJobStage.OPTIMIZING_ITINERARY.value,
                90,
                "正在优化路线和时间表",
            )
        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=[],
            overall_suggestions="测试建议",
        )


class TimedOutPlanner:
    """模拟上游 LLM 调用超时的旅行规划器。"""

    def plan_trip(
        self,
        request: TripRequest,
        progress_callback: Optional[TripProgressCallback] = None,
    ) -> TripPlan:
        """
        抛出带稳定语义的超时异常。

        Args:
            request: worker 从仓储读取的旅行请求。
            progress_callback: worker 注入的进度回调。

        Raises:
            TimeoutError: 始终抛出以模拟 Planner 超时。
        """
        raise TimeoutError("LLM request timed out")


class CancelAfterPlanningPlanner(SuccessfulPlanner):
    """模拟规划完成瞬间收到用户取消请求的旅行规划器。"""

    def __init__(
        self,
        repository: InMemoryTripJobRepository,
        job_id: str,
    ) -> None:
        """
        保存需要在返回结果前更新的任务上下文。

        Args:
            repository: worker 使用的测试仓储。
            job_id: 当前公开任务ID。
        """
        self.repository = repository
        self.job_id = job_id

    def plan_trip(
        self,
        request: TripRequest,
        progress_callback: Optional[TripProgressCallback] = None,
    ) -> TripPlan:
        """
        生成计划后立即写入取消请求，模拟最终提交前的并发竞争。

        Args:
            request: worker 从仓储读取的旅行请求。
            progress_callback: worker 注入的进度回调。

        Returns:
            尚未由 worker 持久化的旅行计划。
        """
        plan = super().plan_trip(request, progress_callback)
        self.repository.update(
            self.job_id,
            status=TripJobStatus.CANCEL_REQUESTED,
        )
        return plan


def build_request() -> TripRequest:
    """
    构造 worker 测试使用的合法旅行请求。

    Returns:
        单日北京旅行规划请求。
    """
    return TripRequest(
        city="北京",
        start_date="2026-09-15",
        end_date="2026-09-15",
        travel_days=1,
        transportation="公共交通",
        accommodation="经济型酒店",
    )


def create_record(repository: InMemoryTripJobRepository) -> TripJobRecord:
    """
    在指定测试仓储中创建待执行任务。

    Args:
        repository: worker 测试使用的内存仓储。

    Returns:
        已保存的排队任务记录。
    """
    record, _ = repository.create_or_get(TripJobRecord.create(build_request()))
    return record


def ignore_celery_state(*args, **kwargs) -> None:
    """
    阻止 eager 单元测试访问真实 Celery 结果后端。

    Args:
        *args: Celery 传入的位置参数，本测试无需使用。
        **kwargs: Celery 传入的状态元数据，本测试无需使用。
    """


def test_worker_persists_progress_and_result(monkeypatch) -> None:
    """worker 应持久化阶段修订并在成功后写入最终旅行计划。"""
    repository = InMemoryTripJobRepository()
    record = create_record(repository)
    def provide_repository() -> InMemoryTripJobRepository:
        """返回当前 worker 测试使用的内存仓储。"""
        return repository

    monkeypatch.setattr(tasks, "get_trip_job_repository", provide_repository)
    monkeypatch.setattr(tasks, "get_trip_planner_agent", SuccessfulPlanner)
    monkeypatch.setattr(
        tasks.generate_trip_plan,
        "update_state",
        ignore_celery_state,
    )

    result = tasks.generate_trip_plan.apply(
        args=[record.job_id],
        task_id=record.task_id,
    ).get()
    completed = repository.get(record.job_id)

    assert result["status"] == TripJobStatus.SUCCEEDED.value
    assert completed.status == TripJobStatus.SUCCEEDED
    assert completed.stage == TripJobStage.COMPLETED
    assert completed.progress == 100
    assert completed.result is not None
    assert completed.result.city == "北京"
    assert completed.revision >= 4


def test_worker_classifies_timeout_failure(monkeypatch) -> None:
    """Planner 超时后 worker 应保存稳定错误码而不是只留下异常堆栈。"""
    repository = InMemoryTripJobRepository()
    record = create_record(repository)
    def provide_repository() -> InMemoryTripJobRepository:
        """返回当前超时测试使用的内存仓储。"""
        return repository

    monkeypatch.setattr(tasks, "get_trip_job_repository", provide_repository)
    monkeypatch.setattr(tasks, "get_trip_planner_agent", TimedOutPlanner)

    result = tasks.generate_trip_plan.apply(
        args=[record.job_id],
        task_id=record.task_id,
        throw=False,
    )
    failed = repository.get(record.job_id)

    assert result.failed()
    assert failed.status == TripJobStatus.FAILED
    assert failed.stage == TripJobStage.FAILED
    assert failed.error_code == "GENERATION_TIMEOUT"


def test_worker_honors_cancel_marker_before_generation(monkeypatch) -> None:
    """worker 取到已经请求取消的任务时不应启动多 Agent 规划。"""
    repository = InMemoryTripJobRepository()
    record = create_record(repository)
    repository.update(record.job_id, status=TripJobStatus.CANCEL_REQUESTED)
    def provide_repository() -> InMemoryTripJobRepository:
        """返回当前预取消测试使用的内存仓储。"""
        return repository

    monkeypatch.setattr(tasks, "get_trip_job_repository", provide_repository)

    result = tasks.generate_trip_plan.apply(
        args=[record.job_id],
        task_id=record.task_id,
    ).get()
    cancelled = repository.get(record.job_id)

    assert result["status"] == TripJobStatus.CANCELLED.value
    assert cancelled.status == TripJobStatus.CANCELLED
    assert cancelled.stage == TripJobStage.CANCELLED


def test_worker_does_not_overwrite_late_cancel_with_success(monkeypatch) -> None:
    """结果落库前到达的取消请求不能被成功终态覆盖。"""
    repository = InMemoryTripJobRepository()
    record = create_record(repository)
    planner = CancelAfterPlanningPlanner(repository, record.job_id)

    def provide_repository() -> InMemoryTripJobRepository:
        """返回当前并发取消测试使用的内存仓储。"""
        return repository

    def provide_planner() -> CancelAfterPlanningPlanner:
        """返回会在结束前写入取消标记的测试规划器。"""
        return planner

    monkeypatch.setattr(tasks, "get_trip_job_repository", provide_repository)
    monkeypatch.setattr(tasks, "get_trip_planner_agent", provide_planner)
    monkeypatch.setattr(
        tasks.generate_trip_plan,
        "update_state",
        ignore_celery_state,
    )

    result = tasks.generate_trip_plan.apply(
        args=[record.job_id],
        task_id=record.task_id,
    ).get()
    cancelled = repository.get(record.job_id)

    assert result["status"] == TripJobStatus.CANCELLED.value
    assert cancelled.status == TripJobStatus.CANCELLED
    assert cancelled.result is None


def test_worker_ignores_stale_queue_delivery(monkeypatch) -> None:
    """旧 task ID 的延迟消息不得再次触发多 Agent 或外部付费调用。"""
    repository = InMemoryTripJobRepository()
    record = create_record(repository)

    def provide_repository() -> InMemoryTripJobRepository:
        """返回当前旧消息测试使用的内存仓储。"""
        return repository

    monkeypatch.setattr(tasks, "get_trip_job_repository", provide_repository)

    result = tasks.generate_trip_plan.apply(
        args=[record.job_id],
        task_id=f"stale-{record.job_id}",
    ).get()
    unchanged = repository.get(record.job_id)

    assert result["reason"] == "stale_task_id"
    assert unchanged.status == TripJobStatus.QUEUED
    assert unchanged.attempt_count == 0


def test_worker_reclaims_running_job_only_for_redelivery(monkeypatch) -> None:
    """Celery 重投消息应接管运行中任务并记录受限的第二次执行。"""
    repository = InMemoryTripJobRepository()
    record = create_record(repository)
    _, first_claimed = repository.begin_attempt(
        record.job_id,
        task_id=record.task_id,
        allow_running_reclaim=False,
    )

    def provide_repository() -> InMemoryTripJobRepository:
        """返回当前重投恢复测试使用的内存仓储。"""
        return repository

    monkeypatch.setattr(tasks, "get_trip_job_repository", provide_repository)
    monkeypatch.setattr(tasks, "get_trip_planner_agent", SuccessfulPlanner)
    monkeypatch.setattr(
        tasks.generate_trip_plan,
        "update_state",
        ignore_celery_state,
    )

    assert first_claimed is True
    tasks.generate_trip_plan.push_request(
        id=record.task_id,
        delivery_info={"redelivered": True},
    )
    try:
        result = tasks.generate_trip_plan.run(record.job_id)
    finally:
        tasks.generate_trip_plan.pop_request()

    completed = repository.get(record.job_id)

    assert result["status"] == TripJobStatus.SUCCEEDED.value
    assert completed.status == TripJobStatus.SUCCEEDED
    assert completed.attempt_count == 2
