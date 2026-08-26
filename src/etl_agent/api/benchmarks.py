"""Benchmark 运行、持久化和项目级查询 API。"""

from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from etl_agent.api.auth_dependencies import CurrentUser, DbSession, require_project_role
from etl_agent.api.errors import ApiError
from etl_agent.benchmark import BenchmarkLevel, BenchmarkReport, BenchmarkRequest, run_benchmark
from etl_agent.infrastructure.models import BenchmarkRun, ProjectRole

router = APIRouter(prefix="/api/v1", tags=["benchmarks"])


def benchmark_report_from_row(row: BenchmarkRun) -> BenchmarkReport:
    """把数据库中的 Benchmark 运行事实转换为稳定的 API 报告。"""
    return BenchmarkReport(
        benchmark_id=row.id,
        status=row.status,
        project_id=row.project_id,
        level=BenchmarkLevel(row.level),
        dataset_rows=row.dataset_rows,
        repeat=row.repeat,
        seed=row.seed,
        dataset_digest=row.dataset_digest,
        artifact_digest=row.artifact_digest,
        policy_version=row.policy_version,
        environment=row.environment,
        started_at=row.started_at,
        completed_at=row.completed_at,
        metrics=dict(row.metrics_json),
    )


@router.post("/benchmarks/run", response_model=BenchmarkReport, status_code=status.HTTP_200_OK)
async def run_benchmark_endpoint(
    payload: BenchmarkRequest,
    current_user: CurrentUser,
    session: DbSession,
) -> BenchmarkReport:
    """由 Operator 或 Auditor 启动 Benchmark，并保存可复现的运行摘要。"""
    await require_project_role(
        payload.project_id,
        current_user,
        session,
        {ProjectRole.OPERATOR, ProjectRole.AUDITOR},
    )
    report = run_benchmark(payload)
    session.add(
        BenchmarkRun(
            id=report.benchmark_id,
            project_id=report.project_id,
            created_by=current_user.id,
            status=report.status,
            level=report.level.value,
            dataset_rows=report.dataset_rows,
            repeat=report.repeat,
            seed=report.seed,
            dataset_digest=report.dataset_digest,
            artifact_digest=report.artifact_digest,
            policy_version=report.policy_version,
            environment=report.environment,
            started_at=report.started_at,
            completed_at=report.completed_at,
            metrics_json=report.metrics,
        )
    )
    await session.commit()
    return report


@router.get(
    "/projects/{project_id}/benchmarks",
    response_model=list[BenchmarkReport],
)
async def list_benchmark_runs(
    project_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[BenchmarkReport]:
    """按项目成员权限查询最近 Benchmark，默认只返回最近 20 条。"""
    await require_project_role(
        project_id,
        current_user,
        session,
        {
            ProjectRole.MAKER,
            ProjectRole.CHECKER_1,
            ProjectRole.CHECKER_2,
            ProjectRole.OPERATOR,
            ProjectRole.AUDITOR,
        },
    )
    rows = await session.scalars(
        select(BenchmarkRun)
        .where(BenchmarkRun.project_id == project_id)
        .order_by(BenchmarkRun.created_at.desc())
        .limit(limit)
    )
    return [benchmark_report_from_row(row) for row in rows]


@router.get("/benchmarks/{benchmark_id}", response_model=BenchmarkReport)
async def get_benchmark_run(
    benchmark_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> BenchmarkReport:
    """查询单条 Benchmark，并再次校验其所属项目成员边界。"""
    row = await session.get(BenchmarkRun, benchmark_id)
    if row is None:
        raise ApiError("BENCHMARK_NOT_FOUND", "Benchmark 运行记录不存在", status_code=404)
    await require_project_role(
        row.project_id,
        current_user,
        session,
        {
            ProjectRole.MAKER,
            ProjectRole.CHECKER_1,
            ProjectRole.CHECKER_2,
            ProjectRole.OPERATOR,
            ProjectRole.AUDITOR,
        },
    )
    return benchmark_report_from_row(row)
