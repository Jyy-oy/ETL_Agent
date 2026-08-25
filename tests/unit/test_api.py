import pytest
from fastapi.testclient import TestClient

from etl_agent.api.app import create_app
from etl_agent.api.errors import ApiError
from etl_agent.api.health_models import DependencyHealth
from etl_agent.api.projects import validate_role_assignment
from etl_agent.config import Settings
from etl_agent.infrastructure.health import HealthReport
from etl_agent.infrastructure.models import ProjectRole


class FakeHealthService:
    settings = Settings(_env_file=None, app_name="test-app", app_env="test")

    async def check(self) -> HealthReport:
        """返回固定的健康报告，隔离 API 测试与真实基础设施。"""
        return HealthReport(
            status="ok",
            dependencies={
                "postgresql": DependencyHealth(status="ok", detail="ready"),
            },
        )


def test_health_returns_request_id() -> None:
    """验证健康接口会回传请求 ID 并提供检查时间。"""
    service = FakeHealthService()
    client = TestClient(create_app(settings=service.settings, health_service=service))

    response = client.get("/health", headers={"X-Request-ID": "test-request"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request"
    assert response.json()["request_id"] == "test-request"
    assert response.json()["checked_at"]


def test_m1_auth_and_project_routes_are_registered() -> None:
    """验证 M1.2 认证、项目和成员 API 已挂载到版本化路由。"""
    service = FakeHealthService()
    client = TestClient(create_app(settings=service.settings, health_service=service))
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/v1/auth/login" in paths
    assert "/api/v1/projects" in paths
    assert "/api/v1/projects/{project_id}/members" in paths
    assert "/api/v1/connections/{connection_id}/tests" in paths
    assert "/api/v1/connections/{connection_id}/profiles" in paths
    assert "/api/v1/file-assets" in paths
    assert "/api/v1/projects/{project_id}/file-assets" in paths
    assert "/api/v1/pipelines" in paths
    assert "/api/v1/pipelines/{pipeline_id}/versions" in paths
    assert "/api/v1/versions/{version_id}/generation" in paths
    assert "/api/v1/agent-runs/{run_id}/answers" in paths
    assert "/api/v1/versions/{version_id}/design" in paths


def test_role_assignment_rejects_checker_overlap() -> None:
    """验证职责校验拒绝 Maker/Operator 与 Checker 混用。"""
    with pytest.raises(ApiError, match="不得兼任"):
        validate_role_assignment({ProjectRole.MAKER}, ProjectRole.CHECKER_1)
