"""Readiness endpoint and dependency probes."""

from fastapi import APIRouter, Request, Response, status

from etl_agent.api.errors import request_id_for
from etl_agent.api.health_models import HealthResponse
from etl_agent.infrastructure.health import HealthService

router = APIRouter()


async def _health(request: Request, response: Response) -> HealthResponse:
    """执行依赖探针并返回应用就绪状态，异常时设置 503 状态码。"""
    service: HealthService = request.app.state.health_service
    report = await service.check()
    if report.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status=report.status,
        app=service.settings.app_name,
        environment=service.settings.app_env,
        request_id=request_id_for(request),
        dependencies=report.dependencies,
    )


router.add_api_route("/health", _health, methods=["GET"], response_model=HealthResponse)
router.add_api_route(
    "/api/v1/health",
    _health,
    methods=["GET"],
    response_model=HealthResponse,
    include_in_schema=False,
)
