"""用于验证 Redis broker 重投语义的独立 Celery 测试应用。"""

import os
from time import monotonic, sleep
from typing import Dict, Union

from celery import Celery
from redis import Redis


REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://127.0.0.1:6380/0")
STATE_PREFIX = "helloagents:fault-test"
VISIBILITY_TIMEOUT_SECONDS = 2

app = Celery(
    "helloagents-fault-test",
    broker=REDIS_URL,
    backend=REDIS_URL,
)
app.conf.update(
    task_acks_late=True,
    task_acks_on_failure_or_timeout=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={
        "visibility_timeout": VISIBILITY_TIMEOUT_SECONDS,
    },
)


@app.task(bind=True, name="tests.block_until_released")
def block_until_released(
    self,
    run_id: str,
) -> Dict[str, Union[int, bool]]:
    """
    持有未确认消息，直到测试进程允许当前 worker 完成。

    Args:
        self: Celery 注入的绑定任务实例。
        run_id: 隔离单次故障注入测试的随机标识。

    Returns:
        当前执行次数和消息是否属于重新投递。

    Raises:
        TimeoutError: 测试进程未在一分钟内释放任务时抛出。
    """
    client = Redis.from_url(REDIS_URL, decode_responses=True)
    state_key = f"{STATE_PREFIX}:{run_id}:state"
    release_key = f"{STATE_PREFIX}:{run_id}:release"
    attempt = int(client.hincrby(state_key, "attempts", 1))
    delivery_info = self.request.delivery_info or {}
    redelivered = bool(delivery_info.get("redelivered"))
    client.hset(
        state_key,
        mapping={
            f"started_{attempt}": "1",
            f"redelivered_{attempt}": str(redelivered).lower(),
        },
    )

    deadline = monotonic() + 60
    while not client.exists(release_key):
        if monotonic() >= deadline:
            raise TimeoutError("故障注入测试未及时释放 Celery 任务")
        sleep(0.1)

    return {"attempt": attempt, "redelivered": redelivered}
