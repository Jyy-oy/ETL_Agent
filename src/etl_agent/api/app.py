"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from etl_agent.api.auth import router as auth_router
from etl_agent.api.benchmarks import router as benchmarks_router
from etl_agent.api.connections import router as connections_router
from etl_agent.api.errors import (
    ApiError,
    api_error_handler,
    http_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from etl_agent.api.file_assets import router as file_assets_router
from etl_agent.api.generation import router as generation_router
from etl_agent.api.health import router as health_router
from etl_agent.api.middleware import RequestIdMiddleware
from etl_agent.api.preparations import router as preparations_router
from etl_agent.api.projects import router as projects_router
from etl_agent.config import Settings, get_settings
from etl_agent.infrastructure.database import create_session_factory
from etl_agent.infrastructure.health import HealthService
from etl_agent.infrastructure.llm import create_llm_provider
from etl_agent.infrastructure.object_store import create_object_store
from etl_agent.infrastructure.secrets import create_secret_provider


def create_app(
    settings: Settings | None = None,
    health_service: HealthService | None = None,
) -> FastAPI:
    """创建并配置 FastAPI 应用实例及其中间件、异常处理器和路由。"""
    app_settings = settings or get_settings()
    app = FastAPI(title=app_settings.app_name, version="0.1.0")
    app.state.settings = app_settings
    app.state.health_service = health_service or HealthService(app_settings)
    app.state.db_session_factory = create_session_factory(app_settings.database_url)
    app.state.secret_provider = create_secret_provider(app_settings)
    app.state.object_store = create_object_store(app_settings)
    app.state.llm_provider = create_llm_provider(app_settings)

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(benchmarks_router)
    app.include_router(connections_router)
    app.include_router(file_assets_router)
    app.include_router(generation_router)
    app.include_router(projects_router)
    app.include_router(preparations_router)
    return app
