"""Celery worker 丢失后的真实 Redis broker 故障恢复测试。"""

import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
from typing import Callable
from uuid import uuid4

import pytest
from celery.result import AsyncResult
from redis import Redis

from tests.support.celery_fault_app import (
    REDIS_URL,
    STATE_PREFIX,
    VISIBILITY_TIMEOUT_SECONDS,
    app,
    block_until_released,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_CELERY_FAULT_TEST") != "1",
    reason="设置 RUN_CELERY_FAULT_TEST=1 后运行真实 worker 故障注入测试",
)


def start_worker() -> subprocess.Popen:
    """
    启动使用 solo 池的独立 Celery 测试 worker。

    Returns:
        正在后台运行的 worker 子进程。
    """
    backend_dir = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["TEST_REDIS_URL"] = REDIS_URL
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "tests.support.celery_fault_app:app",
            "worker",
            "--pool=solo",
            "--loglevel=INFO",
            "--without-gossip",
            "--without-mingle",
        ],
        cwd=backend_dir,
        env=environment,
    )


def stop_worker(worker: subprocess.Popen, force: bool = False) -> None:
    """
    停止测试 worker，并保证子进程在测试退出前被回收。

    Args:
        worker: 需要停止的 worker 子进程。
        force: 是否模拟进程异常丢失并直接终止。
    """
    if worker.poll() is not None:
        return
    if force:
        worker.kill()
    else:
        worker.terminate()
    try:
        worker.wait(timeout=10)
    except subprocess.TimeoutExpired:
        worker.kill()
        worker.wait(timeout=10)


def restore_expired_messages() -> None:
    """
    触发 Kombu Redis transport 对过期未确认消息的恢复扫描。

    测试显式调用该维护动作，避免结果依赖空闲 worker 内部轮询次数。
    """
    connection = app.connection_for_read()
    channel = connection.channel()
    try:
        channel.qos.restore_visible(interval=1)
    finally:
        channel.close()
        connection.close()


def wait_until(
    predicate: Callable[[], bool],
    timeout_seconds: float,
) -> None:
    """
    在限定时间内轮询一个最终应为真的测试条件。

    Args:
        predicate: 返回当前条件是否满足的无参数函数。
        timeout_seconds: 最大等待秒数。

    Raises:
        AssertionError: 超时后条件仍未满足时抛出。
    """
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.2)
    raise AssertionError(f"等待 Celery 故障恢复超过 {timeout_seconds} 秒")


def test_worker_loss_redelivers_unacknowledged_task() -> None:
    """强制终止首个 worker 后，同一消息应由第二个 worker 重投执行。"""
    client = Redis.from_url(REDIS_URL, decode_responses=True)
    client.ping()
    client.flushdb()
    run_id = uuid4().hex
    task_id = f"fault-{run_id}"
    state_key = f"{STATE_PREFIX}:{run_id}:state"
    release_key = f"{STATE_PREFIX}:{run_id}:release"
    first_worker = start_worker()
    second_worker = None

    try:
        block_until_released.apply_async(args=[run_id], task_id=task_id)
        wait_until(
            lambda: client.hget(state_key, "started_1") == "1",
            timeout_seconds=20,
        )
        stop_worker(first_worker, force=True)

        sleep(VISIBILITY_TIMEOUT_SECONDS + 1)
        restore_expired_messages()
        second_worker = start_worker()
        wait_until(
            lambda: int(client.hget(state_key, "attempts") or 0) >= 2,
            timeout_seconds=30,
        )
        client.set(release_key, "1", ex=30)
        result = AsyncResult(task_id, app=app).get(timeout=20)

        assert result == {"attempt": 2, "redelivered": True}
        assert client.hget(state_key, "redelivered_1") == "false"
        assert client.hget(state_key, "redelivered_2") == "true"
    finally:
        stop_worker(first_worker, force=True)
        if second_worker is not None:
            stop_worker(second_worker)
        client.flushdb()
