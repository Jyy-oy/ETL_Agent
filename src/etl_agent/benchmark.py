"""M6 L0/L1 Benchmark 的确定性模型和执行函数。

Benchmark 只生成统计摘要，不保存业务样本。固定数据集规模和随机种子后，
质量指标保持稳定，便于学习、回归和后续接入真实数据面。
"""

import hashlib
import random
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class BenchmarkLevel(StrEnum):
    """定义首期 Benchmark 的两个隔离级别。"""

    L0 = "l0"
    L1 = "l1"


class BenchmarkRequest(BaseModel):
    """声明一次可重复 Benchmark 所需的版本、环境和数据规模。"""

    project_id: UUID
    level: BenchmarkLevel = BenchmarkLevel.L0
    dataset_rows: int = Field(default=1000, ge=1, le=1_000_000)
    seed: int = Field(default=20260826, ge=0, le=2_147_483_647)
    repeat: int = Field(default=1, ge=1, le=20)
    artifact_digest: str = Field(default="synthetic-etl-plan-v1", min_length=1, max_length=128)
    policy_version: str = Field(default="pdp-v1", min_length=1, max_length=64)
    environment: str = Field(default="development", min_length=1, max_length=32)


class BenchmarkReport(BaseModel):
    """返回脱敏 Benchmark 报告和可追踪的运行事实。"""

    benchmark_id: UUID
    status: str
    project_id: UUID
    level: BenchmarkLevel
    dataset_rows: int
    repeat: int
    seed: int
    dataset_digest: str
    artifact_digest: str
    policy_version: str
    environment: str
    started_at: datetime
    completed_at: datetime
    metrics: dict[str, float | int | str]


def _synthetic_dataset_digest(request: BenchmarkRequest) -> str:
    """根据 Benchmark 参数生成稳定的数据集摘要，不构造或持久化真实样本。"""
    payload = (
        f"{request.level.value}:{request.dataset_rows}:{request.seed}:"
        f"{request.repeat}:{request.artifact_digest}:{request.policy_version}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _run_one_iteration(request: BenchmarkRequest, iteration: int) -> dict[str, float | int]:
    """执行一次固定规则的 L0/L1 模拟并返回统计指标。"""
    rng = random.Random(request.seed + iteration)
    injected_faults = 0
    if request.level is BenchmarkLevel.L1:
        # L1 注入可预测的坏记录，模拟 Schema 漂移和质量拒绝。
        injected_faults = max(1, request.dataset_rows // 50)
    jitter_ms = rng.randint(0, 3)
    accepted_rows = request.dataset_rows - injected_faults
    base_latency_ms = 8 + request.dataset_rows // 2_000 + jitter_ms
    return {
        "accepted_rows": accepted_rows,
        "rejected_rows": injected_faults,
        "latency_ms": base_latency_ms,
        "schema_checks": request.dataset_rows,
        "schema_matches": request.dataset_rows - (1 if request.level is BenchmarkLevel.L1 else 0),
        "p0_cases": 3 if request.level is BenchmarkLevel.L1 else 0,
        "p0_intercepted": 3 if request.level is BenchmarkLevel.L1 else 0,
    }


def run_benchmark(request: BenchmarkRequest) -> BenchmarkReport:
    """运行可离线复现的 L0/L1 Benchmark 并汇总质量、拦截率和吞吐量。"""
    started_at = datetime.now(UTC)
    iterations = [_run_one_iteration(request, index) for index in range(request.repeat)]
    accepted = sum(int(item["accepted_rows"]) for item in iterations)
    rejected = sum(int(item["rejected_rows"]) for item in iterations)
    latency_ms = sum(int(item["latency_ms"]) for item in iterations)
    schema_checks = sum(int(item["schema_checks"]) for item in iterations)
    schema_matches = sum(int(item["schema_matches"]) for item in iterations)
    p0_cases = sum(int(item["p0_cases"]) for item in iterations)
    p0_intercepted = sum(int(item["p0_intercepted"]) for item in iterations)
    elapsed_seconds = max(latency_ms / 1000, 0.001)
    completed_at = started_at + timedelta(milliseconds=latency_ms)
    metrics: dict[str, float | int | str] = {
        "input_records": request.dataset_rows * request.repeat,
        "output_records": accepted,
        "rejected_records": rejected,
        "rejection_rate": round(rejected / max(request.dataset_rows * request.repeat, 1), 6),
        "schema_coverage": round(schema_matches / max(schema_checks, 1), 6),
        "p0_interception_rate": round(p0_intercepted / max(p0_cases, 1), 6) if p0_cases else 1.0,
        "latency_ms": latency_ms,
        "throughput_records_per_second": round(accepted / elapsed_seconds, 3),
        "quality_decision": "passed" if rejected == 0 else "rejected",
    }
    return BenchmarkReport(
        benchmark_id=uuid4(),
        status="completed",
        project_id=request.project_id,
        level=request.level,
        dataset_rows=request.dataset_rows,
        repeat=request.repeat,
        seed=request.seed,
        dataset_digest=_synthetic_dataset_digest(request),
        artifact_digest=request.artifact_digest,
        policy_version=request.policy_version,
        environment=request.environment,
        started_at=started_at,
        completed_at=completed_at,
        metrics=metrics,
    )
