"""基于长期审计数据的运营聚合和单任务追踪服务。"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import DefaultDict, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from ..models.operations_schemas import (
    ExternalCallTraceResponse,
    JobTraceResponse,
    OperationsOverviewResponse,
    ProviderOperationMetrics,
    RecentFailure,
    StatusCount,
)
from ..observability import IMAGE_CACHE_LOOKUPS
from ..persistence.models import (
    ExternalCallRecord,
    TripFeedbackRecord,
    TripGenerationJobRecord,
    TripVersionRecord,
)


def _percentile(values: Sequence[float], fraction: float) -> float:
    """
    计算小样本也稳定的 nearest-rank 百分位数。

    Args:
        values: 非负数值序列。
        fraction: 0 到 1 之间的分位位置。

    Returns:
        空序列返回 0，否则返回对应 nearest-rank 数值。
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999) - 1))
    return round(float(ordered[index]), 3)


def _aware(value: datetime) -> datetime:
    """
    将 SQLite 可能返回的无时区 UTC 时间规范为时区感知值。

    Args:
        value: 数据库时间。

    Returns:
        带 UTC 时区或保留原时区的时间。
    """
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _image_cache_counts() -> Tuple[int, int]:
    """
    从当前 API 进程 Prometheus Counter 读取图片缓存命中和未命中数。

    Returns:
        命中数和未命中数组成的元组。
    """
    totals: Counter[str] = Counter()
    for metric in IMAGE_CACHE_LOOKUPS.collect():
        for sample in metric.samples:
            if sample.name.endswith("_total"):
                totals[sample.labels.get("outcome", "unknown")] += int(sample.value)
    hits = totals["hit"] + totals["hit_after_wait"]
    return hits, totals["miss"]


class OperationsService:
    """查询任务、外部调用和反馈数据并生成低成本运营视图。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """
        初始化运营查询服务。

        Args:
            session_factory: 短生命周期 SQLAlchemy 会话工厂。
        """
        self._session_factory = session_factory

    def overview(self, window_hours: int) -> OperationsOverviewResponse:
        """
        聚合指定时间窗口的成功率、延迟、费用和失败分类。

        Args:
            window_hours: 从当前 UTC 时间向前统计的小时数。

        Returns:
            可直接供管理页面展示的运营总览。
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=window_hours)
        with self._session_factory() as session:
            jobs = list(
                session.scalars(
                    select(TripGenerationJobRecord).where(
                        TripGenerationJobRecord.created_at >= cutoff
                    )
                )
            )
            calls = list(
                session.scalars(
                    select(ExternalCallRecord)
                    .join(
                        TripGenerationJobRecord,
                        TripGenerationJobRecord.job_id == ExternalCallRecord.job_id,
                    )
                    .where(TripGenerationJobRecord.created_at >= cutoff)
                )
            )
            feedback = list(
                session.scalars(
                    select(TripFeedbackRecord).where(
                        TripFeedbackRecord.created_at >= cutoff
                    )
                )
            )

        terminal_durations = [
            max(
                0.0,
                (_aware(job.finished_at) - _aware(job.created_at)).total_seconds(),
            )
            for job in jobs
            if job.finished_at is not None
        ]
        status_counts = Counter(job.status for job in jobs)
        failure_counts = Counter(
            job.error_code or "UNKNOWN"
            for job in jobs
            if job.status == "failed"
        )
        succeeded = status_counts["succeeded"]
        finished = sum(
            status_counts[name]
            for name in ("succeeded", "failed", "cancelled")
        )
        ratings = [item.rating for item in feedback]
        cache_hits, cache_misses = _image_cache_counts()
        cache_total = cache_hits + cache_misses

        return OperationsOverviewResponse(
            generated_at=now,
            window_hours=window_hours,
            jobs_total=len(jobs),
            success_rate=round(succeeded / finished, 4) if finished else 0.0,
            p50_job_seconds=_percentile(terminal_durations, 0.50),
            p95_job_seconds=_percentile(terminal_durations, 0.95),
            known_cost_cny=round(sum(call.cost_cny or 0.0 for call in calls), 4),
            status_counts=[
                StatusCount(name=name, count=count)
                for name, count in sorted(status_counts.items())
            ],
            failure_counts=[
                StatusCount(name=name, count=count)
                for name, count in failure_counts.most_common()
            ],
            provider_metrics=self._provider_metrics(calls),
            recent_failures=self._recent_failures(jobs),
            feedback_count=len(feedback),
            average_rating=(
                round(sum(ratings) / len(ratings), 2) if ratings else None
            ),
            image_cache_hits=cache_hits,
            image_cache_misses=cache_misses,
            image_cache_hit_rate=(
                round(cache_hits / cache_total, 4) if cache_total else None
            ),
        )

    def job_trace(self, locator: str) -> Optional[JobTraceResponse]:
        """
        使用 job_id 或 request_id 查询一次请求的完整执行证据。

        Args:
            locator: 公开任务 ID 或响应头中的请求 ID。

        Returns:
            找到时返回任务、预算、调用和 Agent 轨迹，否则返回 None。
        """
        with self._session_factory() as session:
            job = session.scalar(
                select(TripGenerationJobRecord)
                .where(
                    or_(
                        TripGenerationJobRecord.job_id == locator,
                        TripGenerationJobRecord.request_id == locator,
                    )
                )
                .order_by(TripGenerationJobRecord.created_at.desc())
            )
            if job is None:
                return None
            calls = list(
                session.scalars(
                    select(ExternalCallRecord)
                    .where(ExternalCallRecord.job_id == job.job_id)
                    .order_by(ExternalCallRecord.requested_at)
                )
            )
            agent_trace = self._load_agent_trace(session, job.trip_id)
            return JobTraceResponse(
                job_id=job.job_id,
                request_id=job.request_id,
                trip_id=job.trip_id,
                status=job.status,
                city=str(job.request_json.get("city") or ""),
                request=job.request_json,
                budget=job.budget_json or {},
                error_code=job.error_code,
                error_message=job.error_message,
                created_at=job.created_at,
                updated_at=job.updated_at,
                finished_at=job.finished_at,
                external_calls=[
                    ExternalCallTraceResponse.model_validate(call)
                    for call in calls
                ],
                agent_trace=agent_trace,
            )

    @staticmethod
    def _provider_metrics(
        calls: Iterable[ExternalCallRecord],
    ) -> List[ProviderOperationMetrics]:
        """
        按提供方和操作汇总成功率、延迟和已知费用。

        Args:
            calls: 时间窗口内的调用记录。

        Returns:
            排序稳定的提供方指标列表。
        """
        groups: DefaultDict[Tuple[str, str], list[ExternalCallRecord]] = defaultdict(list)
        for call in calls:
            groups[(call.provider, call.operation)].append(call)
        result: List[ProviderOperationMetrics] = []
        for (provider, operation), records in sorted(groups.items()):
            latencies = [record.elapsed_seconds for record in records]
            successes = sum(1 for record in records if record.succeeded)
            result.append(
                ProviderOperationMetrics(
                    provider=provider,
                    operation=operation,
                    calls=len(records),
                    success_rate=round(successes / len(records), 4),
                    average_latency_seconds=round(
                        sum(latencies) / len(latencies),
                        3,
                    ),
                    p95_latency_seconds=_percentile(latencies, 0.95),
                    known_cost_cny=round(
                        sum(record.cost_cny or 0.0 for record in records),
                        4,
                    ),
                )
            )
        return result

    @staticmethod
    def _recent_failures(
        jobs: Iterable[TripGenerationJobRecord],
    ) -> List[RecentFailure]:
        """
        生成最多十条且不包含用户身份的近期失败摘要。

        Args:
            jobs: 时间窗口内的任务记录。

        Returns:
            按创建时间倒序排列的失败任务。
        """
        failed = sorted(
            (job for job in jobs if job.status == "failed"),
            key=lambda item: item.created_at,
            reverse=True,
        )[:10]
        return [
            RecentFailure(
                job_id=job.job_id,
                request_id=job.request_id,
                city=str(job.request_json.get("city") or ""),
                error_code=job.error_code or "UNKNOWN",
                error_message=job.error_message or "",
                created_at=job.created_at,
                finished_at=job.finished_at,
            )
            for job in failed
        ]

    @staticmethod
    def _load_agent_trace(
        session: Session,
        trip_id: Optional[str],
    ) -> List[Dict]:
        """
        从成功任务的最新不可变版本读取 Agent 决策轨迹。

        Args:
            session: 当前数据库会话。
            trip_id: 成功任务关联的可选行程 ID。

        Returns:
            已持久化 Agent 轨迹；失败任务或旧数据返回空列表。
        """
        if not trip_id:
            return []
        version = session.scalar(
            select(TripVersionRecord)
            .where(TripVersionRecord.trip_id == trip_id)
            .order_by(TripVersionRecord.version_number.desc())
        )
        if version is None:
            return []
        raw = version.plan_json.get("agent_trace", [])
        return raw if isinstance(raw, list) else []
