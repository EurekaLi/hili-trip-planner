"""Celery worker 执行的旅行计划生成任务。"""

from datetime import datetime, timezone
from time import perf_counter
from typing import Dict

from .celery_app import celery_app
from .models import TripJobRecord, TripJobStage, TripJobStatus
from .service import get_trip_job_repository
from ..agents.progress import TripGenerationCancelled
from ..agents.trip_planner_agent import get_trip_planner_agent
from ..observability import TASK_DURATION, TASKS, observation_context
from ..services.platform_services import get_trip_library_service
from ..services.trip_library_service import OwnerIdentity


def _error_code_for_exception(exc: Exception) -> str:
    """
    将内部异常转换为稳定且可监控的任务错误码。

    Args:
        exc: 旅行计划生成过程中抛出的异常。

    Returns:
        不包含敏感信息的稳定错误码。
    """
    message = str(exc).lower()
    if "timed out" in message or "超时" in message:
        return "GENERATION_TIMEOUT"
    if "api key" in message or "401" in message:
        return "LLM_AUTHENTICATION_FAILED"
    if "调用预算" in message or "费用超过上限" in message:
        return "TASK_BUDGET_EXCEEDED"
    if "服务暂时熔断" in message:
        return "PROVIDER_CIRCUIT_OPEN"
    return "GENERATION_FAILED"


def _archive_terminal_failure(
    record: TripJobRecord,
    status: TripJobStatus,
    error_code: str,
    error_message: str,
    finished_at: datetime,
    budget_snapshot: Dict | None = None,
) -> None:
    """
    尽力把失败或取消终态写入长期审计，且不覆盖原始任务结果。

    Args:
        record: Redis 中的任务记录。
        status: 失败或取消终态。
        error_code: 稳定错误码。
        error_message: 面向审计的错误摘要。
        finished_at: 任务结束时间。
        budget_snapshot: 任务结束前已经消费的预算快照。
    """
    identity = OwnerIdentity(
        user_id=record.owner_user_id,
        guest_id=record.guest_id,
    )
    if not identity.is_available():
        return
    try:
        get_trip_library_service().record_job_failure(
            job_id=record.job_id,
            identity=identity,
            request=record.request,
            status=status.value,
            error_code=error_code,
            error_message=error_message,
            created_at=record.created_at,
            updated_at=finished_at,
            finished_at=finished_at,
            budget_snapshot=budget_snapshot,
        )
    except Exception:
        # 长期审计不可用时仍以 Redis 中的真实终态和原始异常为准。
        pass


def _observe_terminal_task(
    record: TripJobRecord,
    status: TripJobStatus,
    error_code: str,
    finished_at: datetime,
) -> None:
    """
    记录一个任务终态的次数和端到端耗时指标。

    Args:
        record: 任务开始时的 Redis 记录。
        status: 最终任务状态。
        error_code: 成功时为空、失败时为稳定错误码。
        finished_at: 任务进入终态的 UTC 时间。
    """
    elapsed = max(0.0, (finished_at - record.created_at).total_seconds())
    TASKS.labels(status.value, error_code or "none").inc()
    TASK_DURATION.labels(status.value).observe(elapsed)


def _execute_trip_plan(task, job_id: str) -> Dict[str, str]:
    """
    从 Redis 读取请求，执行多 Agent 规划并持续写入真实阶段进度。

    Args:
        task: Celery 绑定任务实例，用于更新队列状态。
        job_id: 已持久化的公开任务ID。

    Returns:
        包含任务ID和最终状态的轻量字典。
    """
    repository = get_trip_job_repository()
    task_id = str(task.request.id or "")
    delivery_info = task.request.delivery_info or {}
    record, claimed = repository.begin_attempt(
        job_id=job_id,
        task_id=task_id,
        allow_running_reclaim=bool(delivery_info.get("redelivered")),
    )
    if record.status in {TripJobStatus.CANCEL_REQUESTED, TripJobStatus.CANCELLED}:
        finished_at = datetime.now(timezone.utc)
        repository.update(
            job_id,
            status=TripJobStatus.CANCELLED,
            stage=TripJobStage.CANCELLED,
            message="旅行计划任务已取消",
            finished_at=finished_at,
        )
        _archive_terminal_failure(
            record,
            TripJobStatus.CANCELLED,
            "CANCELLED_BY_USER",
            "用户取消了旅行计划任务",
            finished_at,
        )
        _observe_terminal_task(
            record,
            TripJobStatus.CANCELLED,
            "CANCELLED_BY_USER",
            finished_at,
        )
        return {"job_id": job_id, "status": TripJobStatus.CANCELLED.value}
    if not claimed:
        return {
            "job_id": job_id,
            "status": record.status.value,
            "reason": (
                "stale_task_id"
                if record.task_id != task_id
                else "execution_not_claimed"
            ),
        }

    def report_progress(stage: str, progress: int, message: str) -> None:
        """
        将规划器阶段同步到 Redis 和 Celery，并检查协作式取消。

        Args:
            stage: 规划器报告的稳定阶段名称。
            progress: 0 到 100 之间的阶段进度。
            message: 面向用户的当前阶段说明。

        Raises:
            TripGenerationCancelled: 用户已请求取消时抛出。
        """
        latest = repository.get(job_id)
        if latest.status in {
            TripJobStatus.CANCEL_REQUESTED,
            TripJobStatus.CANCELLED,
        }:
            finished_at = datetime.now(timezone.utc)
            repository.update(
                job_id,
                status=TripJobStatus.CANCELLED,
                stage=TripJobStage.CANCELLED,
                message="旅行计划任务已取消",
                finished_at=finished_at,
            )
            _archive_terminal_failure(
                record,
                TripJobStatus.CANCELLED,
                "CANCELLED_BY_USER",
                "用户取消了旅行计划任务",
                finished_at,
            )
            _observe_terminal_task(
                record,
                TripJobStatus.CANCELLED,
                "CANCELLED_BY_USER",
                finished_at,
            )
            raise TripGenerationCancelled("用户取消了旅行计划任务")

        repository.update(
            job_id,
            status=TripJobStatus.RUNNING,
            stage=TripJobStage(stage),
            progress=progress,
            message=message,
        )
        task.update_state(
            state="PROGRESS",
            meta={"stage": stage, "progress": progress, "message": message},
        )

    try:
        plan = get_trip_planner_agent().plan_trip(
            record.request,
            progress_callback=report_progress,
        )
        latest = repository.get(job_id)
        if latest.status in {
            TripJobStatus.CANCEL_REQUESTED,
            TripJobStatus.CANCELLED,
        }:
            finished_at = datetime.now(timezone.utc)
            repository.update(
                job_id,
                status=TripJobStatus.CANCELLED,
                stage=TripJobStage.CANCELLED,
                message="旅行计划任务已取消",
                finished_at=finished_at,
            )
            _archive_terminal_failure(
                record,
                TripJobStatus.CANCELLED,
                "CANCELLED_BY_USER",
                "用户取消了旅行计划任务",
                finished_at,
            )
            _observe_terminal_task(
                record,
                TripJobStatus.CANCELLED,
                "CANCELLED_BY_USER",
                finished_at,
            )
            return {"job_id": job_id, "status": TripJobStatus.CANCELLED.value}

        finished_at = datetime.now(timezone.utc)
        identity = OwnerIdentity(
            user_id=record.owner_user_id,
            guest_id=record.guest_id,
        )
        saved_trip_id = None
        if identity.is_available():
            saved = get_trip_library_service().archive_generated_trip(
                job_id=job_id,
                identity=identity,
                request=record.request,
                plan=plan,
                created_at=record.created_at,
                updated_at=finished_at,
                finished_at=finished_at,
            )
            saved_trip_id = saved.id

        repository.update(
            job_id,
            status=TripJobStatus.SUCCEEDED,
            stage=TripJobStage.COMPLETED,
            progress=100,
            message="旅行计划生成成功",
            result=plan,
            saved_trip_id=saved_trip_id,
            error_code=None,
            error_message=None,
            finished_at=finished_at,
        )
        _observe_terminal_task(
            record,
            TripJobStatus.SUCCEEDED,
            "",
            finished_at,
        )
        return {
            "job_id": job_id,
            "status": TripJobStatus.SUCCEEDED.value,
            "saved_trip_id": saved_trip_id or "",
        }
    except TripGenerationCancelled:
        return {"job_id": job_id, "status": TripJobStatus.CANCELLED.value}
    except Exception as exc:
        finished_at = datetime.now(timezone.utc)
        error_code = _error_code_for_exception(exc)
        repository.update(
            job_id,
            status=TripJobStatus.FAILED,
            stage=TripJobStage.FAILED,
            message="旅行计划生成失败",
            error_code=error_code,
            error_message=str(exc)[:500],
            finished_at=finished_at,
        )
        _archive_terminal_failure(
            record,
            TripJobStatus.FAILED,
            error_code,
            str(exc),
            finished_at,
            budget_snapshot=getattr(exc, "budget_snapshot", None),
        )
        _observe_terminal_task(
            record,
            TripJobStatus.FAILED,
            error_code,
            finished_at,
        )
        raise


@celery_app.task(bind=True, name="trip.generate")
def generate_trip_plan(self, job_id: str) -> Dict[str, str]:
    """
    在带请求和任务关联字段的日志上下文中执行生成任务。

    Args:
        self: Celery 绑定任务实例。
        job_id: 已持久化的公开任务 ID。

    Returns:
        包含任务 ID、状态和可选行程 ID 的轻量结果。
    """
    started_at = perf_counter()
    record = get_trip_job_repository().get(job_id)
    with observation_context(
        request_id=record.request_id,
        job_id=job_id,
    ):
        result = _execute_trip_plan(self, job_id)
        result["worker_elapsed_seconds"] = f"{perf_counter() - started_at:.3f}"
        return result
