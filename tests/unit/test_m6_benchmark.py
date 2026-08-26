"""M6 L0/L1 Benchmark 的确定性和指标边界测试。"""

from uuid import uuid4

from etl_agent.api.benchmarks import benchmark_report_from_row
from etl_agent.benchmark import BenchmarkLevel, BenchmarkRequest, run_benchmark
from etl_agent.infrastructure.models import BenchmarkRun


def test_l0_benchmark_is_repeatable_for_same_inputs() -> None:
    """验证相同参数只改变运行 ID，不改变数据摘要和统计指标。"""
    request = BenchmarkRequest(project_id=uuid4(), dataset_rows=1000, seed=7, repeat=2)
    first = run_benchmark(request)
    second = run_benchmark(request)

    assert first.benchmark_id != second.benchmark_id
    assert first.dataset_digest == second.dataset_digest
    assert first.metrics == second.metrics
    assert first.metrics["rejected_records"] == 0
    assert first.metrics["p0_interception_rate"] == 1.0


def test_l1_benchmark_reports_quality_rejection_and_p0_interception() -> None:
    """验证 L1 注入的坏记录会被质量分流且危险用例全部被拦截。"""
    report = run_benchmark(
        BenchmarkRequest(
            project_id=uuid4(),
            level=BenchmarkLevel.L1,
            dataset_rows=100,
            repeat=3,
        )
    )

    assert report.metrics["rejected_records"] == 6
    assert report.metrics["quality_decision"] == "rejected"
    assert report.metrics["p0_interception_rate"] == 1.0
    assert report.metrics["schema_coverage"] < 1.0


def test_persisted_benchmark_row_round_trips_to_api_report() -> None:
    """验证历史记录转换不会丢失摘要、参数和指标。"""
    source = run_benchmark(BenchmarkRequest(project_id=uuid4(), dataset_rows=12, seed=9))
    row = BenchmarkRun(
        id=source.benchmark_id,
        project_id=source.project_id,
        created_by=uuid4(),
        status=source.status,
        level=source.level.value,
        dataset_rows=source.dataset_rows,
        repeat=source.repeat,
        seed=source.seed,
        dataset_digest=source.dataset_digest,
        artifact_digest=source.artifact_digest,
        policy_version=source.policy_version,
        environment=source.environment,
        started_at=source.started_at,
        completed_at=source.completed_at,
        metrics_json=source.metrics,
    )

    restored = benchmark_report_from_row(row)

    assert restored == source
