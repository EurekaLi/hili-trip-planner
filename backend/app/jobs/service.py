"""旅行计划异步任务的应用服务。"""

from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional, Tuple

from loguru import logger

from ..config import get_settings
from ..models.schemas import TripRequest
from ..services.platform_services import get_trip_library_service
from ..services.trip_library_service import OwnerIdentity, TripLibraryService
from .models import TripJobRecord, TripJobStage, TripJobStatus
from .queue import CeleryTripJobQueue, TripJobQueue
from .repository import RedisTripJobRepository, TripJobRepository


class TripJobIdempotencyConflictError(ValueError):
    """表示同一幂等键被用于不同的旅行规划请求。"""


class TripJobQueueUnavailableError(RuntimeError):
    """表示任务已经记录，但无法成功投递到执行队列。"""


class TripJobService:
    """协调任务记录、幂等提交和队列投递。"""

    def __init__(
        self,
        repository: TripJobRepository,
        queue: TripJobQueue,
        max_attempts: int = 2,
        library_service: Optional[TripLibraryService] = None,
    ) -> None:
        """
        注入任务仓储和队列实现。

        Args:
            repository: 负责任务状态持久化的仓储。
            queue: 负责异步执行投递和撤销的队列。
            max_attempts: worker 丢失场景下允许的最大执行次数。
            library_service: 可选长期任务审计和行程归档服务。
        """
        self.repository = repository
        self.queue = queue
        self.max_attempts = max_attempts
        self.library_service = library_service

    def submit(
        self,
        request: TripRequest,
        idempotency_key: Optional[str] = None,
        identity: Optional[OwnerIdentity] = None,
    ) -> Tuple[TripJobRecord, bool]:
        """
        持久化并投递一次旅行计划生成任务。

        Args:
            request: 已通过校验的旅行规划请求。
            idempotency_key: 客户端提供的可选幂等键。
            identity: 提交任务的用户或游客所有者。

        Returns:
            当前任务记录与是否新建的标记。

        Raises:
            TripJobIdempotencyConflictError: 幂等键对应的请求内容不一致。
            TripJobQueueUnavailableError: 新任务无法投递到队列。
        """
        owner = identity or OwnerIdentity()
        record, created = self.repository.create_or_get(
            TripJobRecord.create(
                request,
                max_attempts=self.max_attempts,
                owner_user_id=owner.user_id,
                guest_id=owner.guest_id,
            ),
            idempotency_key=idempotency_key,
        )
        if not created:
            if record.request.model_dump() != request.model_dump():
                raise TripJobIdempotencyConflictError(
                    "同一 Idempotency-Key 不能提交不同的旅行规划请求"
                )
            return record, False

        if self.library_service is not None and owner.is_available():
            try:
                self.library_service.record_job_submission(
                    job_id=record.job_id,
                    identity=owner,
                    request=request,
                    status=record.status.value,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            except Exception as exc:
                self.repository.update(
                    record.job_id,
                    status=TripJobStatus.FAILED,
                    stage=TripJobStage.FAILED,
                    message="任务持久化服务暂时不可用",
                    error_code="PERSISTENCE_UNAVAILABLE",
                    error_message=str(exc)[:500],
                    finished_at=datetime.now(timezone.utc),
                )
                raise TripJobQueueUnavailableError("旅行计划任务持久化失败") from exc

        try:
            if not record.task_id:
                raise RuntimeError("任务记录缺少队列任务ID")
            self.queue.enqueue(record.job_id, record.task_id)
            return record, True
        except Exception as exc:
            finished_at = datetime.now(timezone.utc)
            failed = self.repository.update(
                record.job_id,
                status=TripJobStatus.FAILED,
                stage=TripJobStage.FAILED,
                message="任务队列暂时不可用",
                error_code="QUEUE_UNAVAILABLE",
                error_message=str(exc)[:500],
                finished_at=finished_at,
            )
            if self.library_service is not None and owner.is_available():
                try:
                    self.library_service.record_job_failure(
                        job_id=record.job_id,
                        identity=owner,
                        request=record.request,
                        status=TripJobStatus.FAILED.value,
                        error_code="QUEUE_UNAVAILABLE",
                        error_message=str(exc),
                        created_at=record.created_at,
                        updated_at=failed.updated_at,
                        finished_at=finished_at,
                    )
                except Exception as audit_exc:
                    logger.warning("队列失败审计写入失败: {}", audit_exc)
            raise TripJobQueueUnavailableError("旅行计划任务投递失败") from exc

    def get(self, job_id: str) -> TripJobRecord:
        """
        查询指定旅行计划任务。

        Args:
            job_id: 公开任务ID。

        Returns:
            当前任务记录。
        """
        return self.repository.get(job_id)

    def cancel(self, job_id: str) -> TripJobRecord:
        """
        取消排队任务或为运行中任务设置协作式取消标记。

        Args:
            job_id: 公开任务ID。

        Returns:
            更新后的任务记录；终态任务保持原状。
        """
        record = self.repository.get(job_id)
        if record.is_terminal():
            return record

        if record.status == TripJobStatus.QUEUED:
            updated = self.repository.update(
                job_id,
                status=TripJobStatus.CANCELLED,
                stage=TripJobStage.CANCELLED,
                message="旅行计划任务已取消",
                finished_at=datetime.now(timezone.utc),
            )
        else:
            updated = self.repository.update(
                job_id,
                status=TripJobStatus.CANCEL_REQUESTED,
                message="正在取消旅行计划任务",
            )

        if record.task_id:
            try:
                self.queue.cancel(record.task_id)
            except Exception as exc:
                # Redis 中的取消标记是最终依据，worker 启动或进入下一阶段时仍会停止。
                logger.warning(
                    "Celery任务撤销失败，已保留Redis取消标记: task_id={}, error={}",
                    record.task_id,
                    exc,
                )
        owner = OwnerIdentity(
            user_id=record.owner_user_id,
            guest_id=record.guest_id,
        )
        if (
            updated.status == TripJobStatus.CANCELLED
            and self.library_service is not None
            and owner.is_available()
        ):
            finished_at = updated.finished_at or datetime.now(timezone.utc)
            try:
                self.library_service.record_job_failure(
                    job_id=record.job_id,
                    identity=owner,
                    request=record.request,
                    status=TripJobStatus.CANCELLED.value,
                    error_code="CANCELLED_BY_USER",
                    error_message="用户取消了旅行计划任务",
                    created_at=record.created_at,
                    updated_at=updated.updated_at,
                    finished_at=finished_at,
                )
            except Exception as exc:
                logger.warning("取消任务审计写入失败: {}", exc)
        return updated


@lru_cache(maxsize=1)
def get_trip_job_repository() -> TripJobRepository:
    """
    构建当前进程复用的 Redis 任务仓储。

    Returns:
        根据应用配置创建的 Redis 任务仓储。
    """
    settings = get_settings()
    return RedisTripJobRepository(
        redis_url=settings.redis_url,
        key_prefix=settings.trip_job_key_prefix,
        ttl_seconds=settings.trip_job_ttl_seconds,
    )


@lru_cache(maxsize=1)
def get_trip_job_service() -> TripJobService:
    """
    构建 FastAPI 进程复用的旅行计划任务服务。

    Returns:
        使用 Redis 仓储和 Celery 队列的任务服务。
    """
    return TripJobService(
        repository=get_trip_job_repository(),
        queue=CeleryTripJobQueue(),
        max_attempts=get_settings().trip_job_max_attempts,
        library_service=get_trip_library_service(),
    )
