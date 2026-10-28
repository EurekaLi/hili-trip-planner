"""旅行计划任务队列接口及 Celery 适配器。"""

from typing import Protocol


class TripJobQueue(Protocol):
    """旅行计划任务服务所依赖的最小队列契约。"""

    def enqueue(self, job_id: str, task_id: str) -> None:
        """使用预先持久化的队列ID投递旅行计划任务。"""

    def cancel(self, task_id: str) -> None:
        """撤销仍在等待执行的队列任务。"""


class CeleryTripJobQueue:
    """基于 Celery 的生产任务队列适配器。"""

    def enqueue(self, job_id: str, task_id: str) -> None:
        """
        使用已写入 Redis 的任务ID向 Celery broker 投递生成任务。

        Args:
            job_id: 已经持久化的公开任务ID。
            task_id: 已经随任务记录持久化的 Celery 内部任务ID。

        Returns:
            None。
        """
        from .celery_app import celery_app

        celery_app.send_task(
            "trip.generate",
            args=[job_id],
            task_id=task_id,
        )

    def cancel(self, task_id: str) -> None:
        """
        撤销尚未开始的 Celery 任务，不强制终止正在执行的进程。

        Args:
            task_id: Celery 内部任务ID。
        """
        from .celery_app import celery_app

        celery_app.control.revoke(task_id, terminate=False)
