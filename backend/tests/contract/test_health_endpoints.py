"""容器编排依赖的公开健康探针契约测试。"""

from fastapi.testclient import TestClient

from app.api import main
from app.services.health_service import DependencyHealth


def test_liveness_does_not_require_external_dependencies() -> None:
    """存活探针必须只证明 API 进程可响应，避免依赖故障触发重启风暴。"""
    response = TestClient(main.app).get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_readiness_returns_200_when_dependencies_are_available(monkeypatch) -> None:
    """数据库和 Redis 均可用时就绪探针应允许编排器导入流量。"""
    monkeypatch.setattr(
        main,
        "check_dependency_health",
        lambda: DependencyHealth(True, "up", "up"),
    )

    response = TestClient(main.app).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "database": "up",
        "redis": "up",
    }


def test_readiness_returns_503_when_a_dependency_is_unavailable(monkeypatch) -> None:
    """任一关键依赖不可用时就绪探针必须拒绝新业务流量。"""
    monkeypatch.setattr(
        main,
        "check_dependency_health",
        lambda: DependencyHealth(False, "up", "down"),
    )

    response = TestClient(main.app).get("/health/ready")

    assert response.status_code == 503
    assert response.json()["redis"] == "down"
