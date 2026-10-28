"""失败任务也可保留的外部调用逐次审计。"""

import logging
from datetime import datetime, timezone
from typing import Optional

from ..observability import get_job_id
from ..persistence.database import SessionLocal
from ..persistence.models import ExternalCallRecord, TripGenerationJobRecord


logger = logging.getLogger(__name__)


def record_external_call_audit(
    provider: str,
    operation: str,
    succeeded: bool,
    elapsed_seconds: float,
    cost_cny: Optional[float] = None,
    error_type: Optional[str] = None,
    requested_at: Optional[datetime] = None,
    job_id: Optional[str] = None,
) -> None:
    """
    尽力把一次 Worker 外部调用写入 SQL，不影响原始业务结果。

    Args:
        provider: 外部服务提供方。
        operation: 有界操作名称。
        succeeded: 调用是否成功。
        elapsed_seconds: 调用耗时秒数。
        cost_cny: 可选已知费用。
        error_type: 可选稳定失败类型。
        requested_at: 调用开始时间；省略时使用当前 UTC 时间。
        job_id: 显式任务 ID；省略时读取当前观测上下文。
    """
    effective_job_id = job_id or get_job_id()
    if not effective_job_id:
        return
    try:
        with SessionLocal() as session:
            if session.get(TripGenerationJobRecord, effective_job_id) is None:
                return
            session.add(
                ExternalCallRecord(
                    job_id=effective_job_id,
                    provider=provider,
                    operation=operation,
                    succeeded=succeeded,
                    elapsed_seconds=max(0.0, elapsed_seconds),
                    units=1,
                    cost_cny=cost_cny,
                    error_type=error_type,
                    requested_at=requested_at or datetime.now(timezone.utc),
                )
            )
            session.commit()
    except Exception as exc:
        logger.warning("external_call_audit_failed error=%s", exc)
