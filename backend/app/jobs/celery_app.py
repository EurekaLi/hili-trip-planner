"""Celery 应用及旅行计划 worker 的执行策略。"""

from celery import Celery

from ..config import get_settings


settings = get_settings()

celery_app = Celery(
    "helloagents_trip_planner",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.jobs.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_acks_on_failure_or_timeout=True,
    task_reject_on_worker_lost=True,
    task_publish_retry=True,
    task_publish_retry_policy={
        "max_retries": 2,
        "interval_start": 0,
        "interval_step": 0.5,
        "interval_max": 1,
    },
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": settings.trip_job_visibility_timeout_seconds,
    },
    worker_cancel_long_running_tasks_on_connection_loss=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=settings.trip_job_soft_time_limit_seconds,
    task_time_limit=settings.trip_job_time_limit_seconds,
    result_expires=settings.trip_job_ttl_seconds,
    worker_redirect_stdouts=False,
)
