"""旅行计划任务仓储接口及 Redis 实现。"""

from datetime import datetime, timezone
from hashlib import sha256
from threading import RLock
from typing import Any, Dict, Optional, Protocol, Tuple

from .models import TripJobRecord, TripJobStage, TripJobStatus


class TripJobNotFoundError(LookupError):
    """表示指定任务不存在或已经过期。"""


def _build_attempt_transition(
    current: TripJobRecord,
    task_id: str,
    allow_running_reclaim: bool,
    now: datetime,
) -> Tuple[TripJobRecord, bool, bool]:
    """
    计算一次执行领取产生的状态变化，不执行任何存储操作。

    Args:
        current: 仓储中的当前任务记录。
        task_id: 本次 Celery 投递携带的内部任务ID。
        allow_running_reclaim: 是否允许 worker 丢失后的重投递领取运行中任务。
        now: 本次状态变化使用的统一UTC时间。

    Returns:
        更新后记录、是否获得执行权、记录是否需要写回的三元组。
    """
    if current.task_id != task_id or current.is_terminal():
        return current, False, False
    if current.status == TripJobStatus.CANCEL_REQUESTED:
        return current, False, False
    if current.status == TripJobStatus.RUNNING and not allow_running_reclaim:
        return current, False, False

    payload = current.model_dump()
    if current.attempt_count >= current.max_attempts:
        payload.update(
            status=TripJobStatus.FAILED,
            stage=TripJobStage.FAILED,
            message="worker恢复次数已达到上限",
            error_code="ATTEMPT_LIMIT_EXCEEDED",
            error_message="任务因worker异常重复执行，已达到最大执行次数",
            finished_at=now,
            updated_at=now,
            revision=current.revision + 1,
        )
        return TripJobRecord.model_validate(payload), False, True

    next_attempt = current.attempt_count + 1
    payload.update(
        status=TripJobStatus.RUNNING,
        stage=TripJobStage.LOADING_GROUNDING_DATA,
        progress=2,
        message=(
            "正在准备旅行规划数据"
            if next_attempt == 1
            else f"worker异常恢复，正在执行第{next_attempt}次尝试"
        ),
        attempt_count=next_attempt,
        attempt_started_at=now,
        updated_at=now,
        revision=current.revision + 1,
    )
    return TripJobRecord.model_validate(payload), True, True


class TripJobRepository(Protocol):
    """任务状态持久化所需的最小仓储契约。"""

    def create_or_get(
        self,
        record: TripJobRecord,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[TripJobRecord, bool]:
        """创建任务，或按幂等键返回已经存在的任务。"""

    def get(self, job_id: str) -> TripJobRecord:
        """根据公开任务ID读取任务记录。"""

    def update(self, job_id: str, **changes: Any) -> TripJobRecord:
        """以原子方式合并并持久化任务字段。"""

    def begin_attempt(
        self,
        job_id: str,
        task_id: str,
        allow_running_reclaim: bool,
    ) -> Tuple[TripJobRecord, bool]:
        """原子领取一次受最大次数约束的任务执行权。"""

    def ping(self) -> bool:
        """检查任务存储是否可访问。"""


class InMemoryTripJobRepository:
    """供单元测试显式注入的线程安全任务仓储。"""

    def __init__(self) -> None:
        """初始化隔离的任务记录和幂等键索引。"""
        self._records: Dict[str, TripJobRecord] = {}
        self._idempotency_index: Dict[str, str] = {}
        self._lock = RLock()

    @staticmethod
    def _clone(record: TripJobRecord) -> TripJobRecord:
        """
        复制任务记录，避免测试调用方绕过仓储直接修改状态。

        Args:
            record: 需要复制的任务记录。

        Returns:
            与输入内容一致但对象独立的任务记录。
        """
        return TripJobRecord.model_validate(record.model_dump())

    def create_or_get(
        self,
        record: TripJobRecord,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[TripJobRecord, bool]:
        """
        在线程锁内创建任务或解析幂等键。

        Args:
            record: 待创建的初始任务记录。
            idempotency_key: 客户端提供的可选幂等键。

        Returns:
            任务记录与是否新建的标记。
        """
        with self._lock:
            if idempotency_key:
                existing_id = self._idempotency_index.get(idempotency_key)
                if existing_id:
                    return self._clone(self._records[existing_id]), False

            self._records[record.job_id] = self._clone(record)
            if idempotency_key:
                self._idempotency_index[idempotency_key] = record.job_id
            return self._clone(record), True

    def get(self, job_id: str) -> TripJobRecord:
        """
        从内存仓储读取任务。

        Args:
            job_id: 公开任务ID。

        Returns:
            当前任务记录的副本。

        Raises:
            TripJobNotFoundError: 任务不存在时抛出。
        """
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise TripJobNotFoundError(job_id)
            return self._clone(record)

    def update(self, job_id: str, **changes: Any) -> TripJobRecord:
        """
        在线程锁内合并任务变更并递增修订号。

        Args:
            job_id: 公开任务ID。
            **changes: 需要修改的任务模型字段。

        Returns:
            更新后的任务记录副本。

        Raises:
            TripJobNotFoundError: 任务不存在时抛出。
        """
        with self._lock:
            current = self._records.get(job_id)
            if current is None:
                raise TripJobNotFoundError(job_id)
            payload = current.model_dump()
            payload.update(changes)
            payload.update(
                updated_at=datetime.now(timezone.utc),
                revision=current.revision + 1,
            )
            updated = TripJobRecord.model_validate(payload)
            self._records[job_id] = updated
            return self._clone(updated)

    def begin_attempt(
        self,
        job_id: str,
        task_id: str,
        allow_running_reclaim: bool,
    ) -> Tuple[TripJobRecord, bool]:
        """
        在线程锁内领取任务执行权并递增尝试次数。

        Args:
            job_id: 公开任务ID。
            task_id: 当前 Celery 内部任务ID。
            allow_running_reclaim: 是否允许 worker 丢失重投递继续执行。

        Returns:
            最新任务记录与是否成功领取执行权的标记。

        Raises:
            TripJobNotFoundError: 任务不存在时抛出。
        """
        with self._lock:
            current = self._records.get(job_id)
            if current is None:
                raise TripJobNotFoundError(job_id)
            updated, claimed, changed = _build_attempt_transition(
                current=current,
                task_id=task_id,
                allow_running_reclaim=allow_running_reclaim,
                now=datetime.now(timezone.utc),
            )
            if changed:
                self._records[job_id] = updated
            return self._clone(updated), claimed

    def ping(self) -> bool:
        """
        返回内存仓储可用状态。

        Returns:
            测试仓储初始化后始终返回 True。
        """
        return True


class RedisTripJobRepository:
    """使用 Redis JSON 字符串和乐观锁实现的生产任务仓储。"""

    def __init__(self, redis_url: str, key_prefix: str, ttl_seconds: int) -> None:
        """
        创建 Redis 客户端并保存键空间配置。

        Args:
            redis_url: Redis 连接地址。
            key_prefix: 与其他业务隔离的任务键前缀。
            ttl_seconds: 任务和幂等索引的保留时长。

        Raises:
            RuntimeError: 未安装 redis Python 包时抛出。
        """
        try:
            from redis import Redis
        except ImportError as exc:
            raise RuntimeError(
                "缺少 redis 依赖，请先安装 backend/requirements.txt"
            ) from exc

        self._client = Redis.from_url(redis_url, decode_responses=True)
        self._key_prefix = key_prefix.rstrip(":")
        self._ttl_seconds = ttl_seconds

    def _job_key(self, job_id: str) -> str:
        """
        构造任务记录使用的 Redis 键。

        Args:
            job_id: 公开任务ID。

        Returns:
            带命名空间前缀的 Redis 键。
        """
        return f"{self._key_prefix}:job:{job_id}"

    def _idempotency_key(self, value: str) -> str:
        """
        将客户端幂等键转换为固定长度 Redis 键。

        Args:
            value: 客户端提供的原始幂等键。

        Returns:
            包含 SHA-256 摘要的 Redis 键。
        """
        digest = sha256(value.encode("utf-8")).hexdigest()
        return f"{self._key_prefix}:idempotency:{digest}"

    def create_or_get(
        self,
        record: TripJobRecord,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[TripJobRecord, bool]:
        """
        使用 SET NX 抢占幂等键并创建任务记录。

        Args:
            record: 待创建的初始任务记录。
            idempotency_key: 客户端提供的可选幂等键。

        Returns:
            任务记录与是否新建的标记。

        Raises:
            RuntimeError: 幂等索引损坏或任务ID发生极小概率冲突时抛出。
        """
        idempotency_redis_key = None
        if idempotency_key:
            idempotency_redis_key = self._idempotency_key(idempotency_key)
            claimed = self._client.set(
                idempotency_redis_key,
                record.job_id,
                nx=True,
                ex=self._ttl_seconds,
            )
            if not claimed:
                existing_id = self._client.get(idempotency_redis_key)
                if existing_id:
                    try:
                        return self.get(existing_id), False
                    except TripJobNotFoundError:
                        self._client.delete(idempotency_redis_key)
                raise RuntimeError("幂等键索引暂时不可用，请稍后重试")

        created = self._client.set(
            self._job_key(record.job_id),
            record.model_dump_json(),
            nx=True,
            ex=self._ttl_seconds,
        )
        if not created:
            if idempotency_redis_key:
                self._client.delete(idempotency_redis_key)
            raise RuntimeError("任务ID冲突，请重新提交")
        return record, True

    def get(self, job_id: str) -> TripJobRecord:
        """
        从 Redis 读取并反序列化任务记录。

        Args:
            job_id: 公开任务ID。

        Returns:
            当前任务记录。

        Raises:
            TripJobNotFoundError: Redis 中不存在该任务时抛出。
        """
        payload = self._client.get(self._job_key(job_id))
        if payload is None:
            raise TripJobNotFoundError(job_id)
        return TripJobRecord.model_validate_json(payload)

    def update(self, job_id: str, **changes: Any) -> TripJobRecord:
        """
        使用 Redis WATCH 保护并发状态合并，避免取消标记被 worker 覆盖。

        Args:
            job_id: 公开任务ID。
            **changes: 需要修改的任务模型字段。

        Returns:
            更新后的任务记录。

        Raises:
            TripJobNotFoundError: 任务不存在时抛出。
            RuntimeError: 多次并发冲突后仍未更新成功时抛出。
        """
        from redis.exceptions import WatchError

        key = self._job_key(job_id)
        for _ in range(5):
            with self._client.pipeline() as pipeline:
                try:
                    pipeline.watch(key)
                    raw_record = pipeline.get(key)
                    if raw_record is None:
                        raise TripJobNotFoundError(job_id)
                    current = TripJobRecord.model_validate_json(raw_record)
                    payload = current.model_dump()
                    payload.update(changes)
                    payload.update(
                        updated_at=datetime.now(timezone.utc),
                        revision=current.revision + 1,
                    )
                    updated = TripJobRecord.model_validate(payload)
                    pipeline.multi()
                    pipeline.set(
                        key,
                        updated.model_dump_json(),
                        ex=self._ttl_seconds,
                    )
                    pipeline.execute()
                    return updated
                except WatchError:
                    continue
        raise RuntimeError(f"任务 {job_id} 状态更新发生持续并发冲突")

    def begin_attempt(
        self,
        job_id: str,
        task_id: str,
        allow_running_reclaim: bool,
    ) -> Tuple[TripJobRecord, bool]:
        """
        使用 Redis WATCH 原子领取执行权并强制最大执行次数。

        Args:
            job_id: 公开任务ID。
            task_id: 当前 Celery 内部任务ID。
            allow_running_reclaim: 是否允许 worker 丢失重投递继续执行。

        Returns:
            最新任务记录与是否成功领取执行权的标记。

        Raises:
            TripJobNotFoundError: 任务不存在时抛出。
            RuntimeError: 多次并发冲突后仍未领取成功时抛出。
        """
        from redis.exceptions import WatchError

        key = self._job_key(job_id)
        for _ in range(5):
            with self._client.pipeline() as pipeline:
                try:
                    pipeline.watch(key)
                    raw_record = pipeline.get(key)
                    if raw_record is None:
                        raise TripJobNotFoundError(job_id)
                    current = TripJobRecord.model_validate_json(raw_record)
                    updated, claimed, changed = _build_attempt_transition(
                        current=current,
                        task_id=task_id,
                        allow_running_reclaim=allow_running_reclaim,
                        now=datetime.now(timezone.utc),
                    )
                    if not changed:
                        pipeline.unwatch()
                        return updated, claimed
                    pipeline.multi()
                    pipeline.set(
                        key,
                        updated.model_dump_json(),
                        ex=self._ttl_seconds,
                    )
                    pipeline.execute()
                    return updated, claimed
                except WatchError:
                    continue
        raise RuntimeError(f"任务 {job_id} 执行权领取发生持续并发冲突")

    def ping(self) -> bool:
        """
        检查 Redis 是否可以响应命令。

        Returns:
            Redis PING 成功时返回 True。
        """
        return bool(self._client.ping())
