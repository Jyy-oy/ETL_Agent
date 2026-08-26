"""M5 运行监督、质量报告和影子表发布决策。"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.domain.generation import EtlPlan, QualityContract, RuntimeBudget
from etl_agent.infrastructure.models import (
    ExecutionQualityResult,
    ExecutionRun,
    ExecutionRunStatus,
    PipelineVersion,
    PublishStatus,
    QualityStatus,
    RuntimeSupervisionSnapshot,
)
from etl_agent.workers.engine import EngineJobStatus, ExecutionEngine
from etl_agent.workers.quality import assess_budget, assess_quality


async def supervise_execution_run(
    session: AsyncSession,
    execution_id,
    *,
    engine: ExecutionEngine,
) -> ExecutionRun:
    """查询一次引擎状态，落库监督快照，并在终态生成质量报告。"""
    execution = await session.scalar(
        select(ExecutionRun).where(ExecutionRun.id == execution_id).with_for_update()
    )
    if execution is None:
        raise ValueError("ExecutionRun 不存在")
    if not execution.engine_job_id:
        raise ValueError("ExecutionRun 缺少引擎作业 ID")
    status = await engine.get_status(execution.engine_job_id)
    metrics = dict(status.metrics or {})
    preparation_budget = await _runtime_budget_for_execution(session, execution)
    budget = assess_budget(preparation_budget, metrics)
    decision = budget.decision
    if execution.status in {
        ExecutionRunStatus.CANCEL_REQUESTED.value,
        ExecutionRunStatus.CANCELLED.value,
    }:
        # 用户或预算已经发起取消，迟到的引擎状态不能把取消事实改回成功。
        decision = "cancel_requested"
    elif status.status is EngineJobStatus.RUNNING and budget.decision == "hard_stop":
        execution.status = ExecutionRunStatus.CANCEL_REQUESTED.value
    elif status.status is EngineJobStatus.SUCCEEDED:
        contract = await _quality_contract_for_execution(session, execution)
        quality = assess_quality(contract, metrics)
        execution.quality_status = quality.status
        execution.metrics_json = {**execution.metrics_json, **metrics, "quality": quality.as_dict()}
        shadow_table = _optional_name(metrics.get("shadow_table"))
        error_table = _optional_name(metrics.get("error_table"))
        # SeaTunnel 原生指标通常不返回目标表名，保留提交阶段写入的安全运行元数据。
        if shadow_table is not None:
            execution.shadow_table = shadow_table
        if error_table is not None:
            execution.error_table = error_table
        execution.completed_at = execution.completed_at or datetime.now(UTC)
        if quality.status == QualityStatus.PASSED.value:
            execution.status = ExecutionRunStatus.SUCCEEDED.value
            # 重复监督可能在 Swap 已发布后到达，不能把已发布事实回写成待发布。
            if execution.publish_status not in {
                PublishStatus.PUBLISHED.value,
                PublishStatus.CLEANED.value,
            }:
                execution.publish_status = PublishStatus.SWAP_REQUESTED.value
        else:
            execution.status = ExecutionRunStatus.FAILED.value
            execution.error_code = quality.error_code
            execution.error_detail = quality.detail
    elif status.status is EngineJobStatus.FAILED:
        execution.status = ExecutionRunStatus.FAILED.value
        execution.error_code = "ENGINE_JOB_FAILED"
        execution.error_detail = (status.detail or "SeaTunnel 作业失败")[:512]
        execution.completed_at = execution.completed_at or datetime.now(UTC)
    elif status.status is EngineJobStatus.CANCELLED:
        execution.status = ExecutionRunStatus.CANCELLED.value
        execution.completed_at = execution.completed_at or datetime.now(UTC)
    elif status.status is EngineJobStatus.UNKNOWN:
        decision = "unknown"
    session.add(
        RuntimeSupervisionSnapshot(
            id=uuid4(),
            project_id=execution.project_id,
            execution_run_id=execution.id,
            engine_status=status.status.value,
            decision=decision,
            observed_metrics=metrics,
            exceeded_budget_fields=list(budget.exceeded),
            detail=status.detail,
        )
    )
    if status.status is EngineJobStatus.SUCCEEDED and execution.status not in {
        ExecutionRunStatus.CANCEL_REQUESTED.value,
        ExecutionRunStatus.CANCELLED.value,
    }:
        contract = await _quality_contract_for_execution(session, execution)
        quality = assess_quality(contract, metrics)
        existing = await session.scalar(
            select(ExecutionQualityResult).where(
                ExecutionQualityResult.execution_run_id == execution.id
            )
        )
        if existing is None:
            session.add(
                ExecutionQualityResult(
                    id=uuid4(),
                    project_id=execution.project_id,
                    execution_run_id=execution.id,
                    status=quality.status,
                    input_records=quality.input_records,
                    output_records=quality.output_records,
                    rejected_records=quality.rejected_records,
                    rejection_rate=quality.rejection_rate,
                    report_json=quality.as_dict(),
                    shadow_table=execution.shadow_table,
                    error_table=execution.error_table,
                )
            )
    await session.commit()
    await session.refresh(execution)
    return execution


async def _runtime_budget_for_execution(
    session: AsyncSession, execution: ExecutionRun
) -> RuntimeBudget:
    """从 Preparation 读取冻结预算，缺失时使用服务端默认值。"""
    from etl_agent.infrastructure.models import Preparation

    preparation = await session.get(Preparation, execution.preparation_id)
    return RuntimeBudget.model_validate(preparation.runtime_budget if preparation else {})


async def _quality_contract_for_execution(
    session: AsyncSession, execution: ExecutionRun
) -> QualityContract:
    """从不可变版本读取质量契约，禁止使用引擎返回的规则覆盖冻结事实。"""
    version = await session.get(PipelineVersion, execution.pipeline_version_id)
    plan = EtlPlan.model_validate(version.etl_plan_json or {}) if version else None
    return plan.quality_contract if plan else QualityContract()


def _optional_name(value: Any) -> str | None:
    """只接受短字符串表名，避免把引擎任意响应写入业务事实。"""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:256] if value else None
