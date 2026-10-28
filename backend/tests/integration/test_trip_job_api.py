"""旅行计划异步任务 HTTP 接口测试。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.trip import router
from app.jobs.repository import InMemoryTripJobRepository
from app.jobs.service import TripJobService, get_trip_job_service
from app.models.schemas import TripRequest


TEST_IDENTITY_HEADERS = {"X-Guest-ID": "guest-api-1234567890"}


class FakeTripJobQueue:
    """为接口测试记录任务投递与撤销操作。"""

    def __init__(self) -> None:
        """初始化空的任务操作记录。"""
        self.cancelled_task_ids: list[str] = []

    def enqueue(self, job_id: str, task_id: str) -> None:
        """
        接收已经随任务记录持久化的队列任务ID。

        Args:
            job_id: 公开任务ID。
            task_id: 已写入任务记录的队列内部ID。

        Returns:
            None。
        """

    def cancel(self, task_id: str) -> None:
        """
        记录接口要求撤销的队列任务ID。

        Args:
            task_id: 队列内部任务ID。
        """
        self.cancelled_task_ids.append(task_id)


def build_request(city: str = "北京") -> TripRequest:
    """
    构造接口测试使用的合法旅行请求。

    Args:
        city: 请求中的目的地城市。

    Returns:
        单日公共交通旅行请求。
    """
    return TripRequest(
        city=city,
        start_date="2026-09-15",
        end_date="2026-09-15",
        travel_days=1,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
    )


def build_client() -> tuple[TestClient, FakeTripJobQueue]:
    """
    创建仅挂载旅行路由且注入测试任务服务的 FastAPI 客户端。

    Returns:
        测试客户端和可检查操作记录的测试队列。
    """
    queue = FakeTripJobQueue()
    service = TripJobService(InMemoryTripJobRepository(), queue)
    app = FastAPI()
    app.include_router(router, prefix="/api")

    def provide_service() -> TripJobService:
        """
        为当前接口测试返回隔离的任务服务。

        Returns:
            使用内存仓储和测试队列的任务服务。
        """
        return service

    app.dependency_overrides[get_trip_job_service] = provide_service
    return TestClient(app), queue


def test_job_api_submit_query_and_cancel_flow() -> None:
    """异步接口应支持提交、公开状态查询和排队任务取消。"""
    client, queue = build_client()
    request = build_request()

    submitted = client.post(
        "/api/trip/jobs",
        headers={**TEST_IDENTITY_HEADERS, "Idempotency-Key": "api-flow-1"},
        json=request.model_dump(),
    )
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]

    queried = client.get(f"/api/trip/jobs/{job_id}", headers=TEST_IDENTITY_HEADERS)
    assert queried.status_code == 200
    assert queried.json()["status"] == "queued"
    assert "task_id" not in queried.json()
    assert "request" not in queried.json()

    forbidden = client.get(
        f"/api/trip/jobs/{job_id}",
        headers={"X-Guest-ID": "different-guest-123456"},
    )
    assert forbidden.status_code == 404

    cancelled = client.delete(f"/api/trip/jobs/{job_id}", headers=TEST_IDENTITY_HEADERS)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert queue.cancelled_task_ids == [f"trip-{job_id}"]


def test_job_api_returns_conflict_for_changed_idempotent_request() -> None:
    """接口层应把幂等键请求冲突转换为 HTTP 409。"""
    client, _ = build_client()
    first_request = build_request("北京")
    second_request = build_request("上海")

    first = client.post(
        "/api/trip/jobs",
        headers={**TEST_IDENTITY_HEADERS, "Idempotency-Key": "api-flow-2"},
        json=first_request.model_dump(),
    )
    second = client.post(
        "/api/trip/jobs",
        headers={**TEST_IDENTITY_HEADERS, "Idempotency-Key": "api-flow-2"},
        json=second_request.model_dump(),
    )

    assert first.status_code == 202
    assert second.status_code == 409


def test_job_api_returns_not_found_for_unknown_job() -> None:
    """查询不存在任务时接口应返回 HTTP 404。"""
    client, _ = build_client()

    response = client.get("/api/trip/jobs/missing-job")

    assert response.status_code == 404
