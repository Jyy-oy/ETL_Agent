"""Identity, project, connection and metadata profile persistence models."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProjectRole(StrEnum):
    MAKER = "maker"
    CHECKER_1 = "checker_1"
    CHECKER_2 = "checker_2"
    OPERATOR = "operator"
    AUDITOR = "auditor"


class ConnectionType(StrEnum):
    """首期支持登记的数据源或目标连接类型。"""

    MYSQL = "mysql"
    POSTGRESQL = "postgresql"
    ORACLE = "oracle"
    DORIS = "doris"
    CLICKHOUSE = "clickhouse"


class ConnectionStatus(StrEnum):
    """连接登记的生命周期状态。"""

    ACTIVE = "active"
    DISABLED = "disabled"


class ProfileStatus(StrEnum):
    """元数据 Profile 的生成状态。"""

    READY = "ready"
    FAILED = "failed"


class PipelineStatus(StrEnum):
    """Pipeline 的生命周期状态。"""

    ACTIVE = "active"
    DISABLED = "disabled"


class PipelineVersionStatus(StrEnum):
    """PipelineVersion 从草稿到冻结的状态。"""

    DRAFT = "draft"
    READY = "ready"
    REJECTED = "rejected"


class AgentRunStatus(StrEnum):
    """AgentRun 的可恢复状态。"""

    RUNNING = "running"
    NEEDS_CLARIFICATION = "needs_clarification"
    COMPLETED = "completed"
    FAILED = "failed"
    VALIDATION_FAILED = "validation_failed"


class ExecutionRunStatus(StrEnum):
    """ExecutionRun 的最小运行状态集合，后续数据面会继续扩展。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


class QualityStatus(StrEnum):
    """执行结果的质量分流状态。"""

    PENDING = "pending"
    PASSED = "passed"
    REJECTED = "rejected"
    FAILED = "failed"


class PublishStatus(StrEnum):
    """影子表到正式表的发布状态。"""

    NOT_STARTED = "not_started"
    SWAP_REQUESTED = "swap_requested"
    PUBLISHED = "published"
    CLEANED = "cleaned"


class RollbackStatus(StrEnum):
    """受管回滚的生命周期状态。"""

    NOT_REQUESTED = "not_requested"
    REQUESTED = "requested"
    COMPLETED = "completed"
    FAILED = "failed"


class OutboxEventStatus(StrEnum):
    """Transactional Outbox 事件的投递状态。"""

    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class ProjectMembership(TimestampMixin, Base):
    __tablename__ = "project_memberships"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_membership"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class ProjectRoleGrant(TimestampMixin, Base):
    __tablename__ = "project_role_grants"
    __table_args__ = (UniqueConstraint("project_id", "role", name="uq_project_role_slot"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)


class Connection(TimestampMixin, Base):
    """保存项目级非敏感连接参数，凭据只能通过 secret_ref 引用。"""

    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_connection_project_code"),
        Index("ix_connections_project_status", "project_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    connection_type: Mapped[str] = mapped_column(String(32), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(nullable=False)
    database_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    secret_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=ConnectionStatus.ACTIVE, nullable=False)


class MetadataProfile(TimestampMixin, Base):
    """保存连接探查得到的脱敏、可复用元数据快照。"""

    __tablename__ = "metadata_profiles"
    __table_args__ = (
        UniqueConstraint("connection_id", "fingerprint", name="uq_profile_connection_fingerprint"),
        Index("ix_metadata_profiles_connection_created", "connection_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    profile_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_snapshot: Mapped[dict[str, Any]] = mapped_column("schema_json", JSON, nullable=False)
    redacted_sample: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    estimated_row_count: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=ProfileStatus.READY, nullable=False)
    error_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)


class FileAsset(TimestampMixin, Base):
    """保存 MinIO 文件对象引用、摘要和脱敏文件 Profile。"""

    __tablename__ = "file_assets"
    __table_args__ = (Index("ix_file_assets_project_created", "project_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(256), nullable=False)
    file_format: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_snapshot: Mapped[dict[str, Any]] = mapped_column("schema_json", JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ready", nullable=False)


class Pipeline(TimestampMixin, Base):
    """保存项目级 Pipeline 标识，不保存可变的生成候选内容。"""

    __tablename__ = "pipelines"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_pipeline_project_code"),
        Index("ix_pipelines_project_status", "project_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=PipelineStatus.ACTIVE, nullable=False)


class PipelineVersion(TimestampMixin, Base):
    """保存 ETL 计划候选或冻结制品；冻结后禁止原地修改。"""

    __tablename__ = "pipeline_versions"
    __table_args__ = (
        UniqueConstraint("pipeline_id", "version_number", name="uq_pipeline_version_number"),
        Index("ix_pipeline_versions_pipeline_status", "pipeline_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    pipeline_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=PipelineVersionStatus.DRAFT, nullable=False
    )
    immutable: Mapped[bool] = mapped_column(default=False, nullable=False)
    artifact_digest: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    etl_plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    hocon: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_profile_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    target_profile_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class AgentRun(TimestampMixin, Base):
    """记录一次生成图执行的状态、摘要和可恢复 Thread ID。"""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_project_created", "project_id", "created_at"),
        Index("ix_agent_runs_thread", "thread_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pipeline_versions.id", ondelete="SET NULL"), nullable=True
    )
    thread_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=AgentRunStatus.RUNNING, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    prompt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    repair_count: Mapped[int] = mapped_column(default=0, nullable=False)
    node_trace: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)


class GenerationAttempt(TimestampMixin, Base):
    """保存每次候选/修复尝试的摘要，不保存完整敏感 Prompt。"""

    __tablename__ = "generation_attempts"
    __table_args__ = (Index("ix_generation_attempts_run_number", "agent_run_id", "attempt_number"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    agent_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    input_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    validation_errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )


class Preparation(TimestampMixin, Base):
    """保存 PDP 决策和不可变输入事实，Prepare 阶段不产生外部副作用。"""

    __tablename__ = "preparations"
    __table_args__ = (
        Index("ix_preparations_project_status", "project_id", "status"),
        Index("ix_preparations_version_created", "pipeline_version_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pipeline_versions.id", ondelete="RESTRICT"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="approval_pending", nullable=False)
    risk_level: Mapped[str] = mapped_column(String(8), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    required_roles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    resource_scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    runtime_budget: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    facts_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApprovalRequest(TimestampMixin, Base):
    """保存 Preparation 的独立职责槽审批事实和决定。"""

    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("preparation_id", "required_role", name="uq_approval_preparation_role"),
        Index("ix_approval_requests_project_status", "project_id", "status"),
        Index("ix_approval_requests_preparation_status", "preparation_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    preparation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("preparations.id", ondelete="CASCADE"), nullable=False
    )
    required_role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approver_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionRun(TimestampMixin, Base):
    """保存 Commit 创建的执行事实，不允许反向修改冻结版本。"""

    __tablename__ = "execution_runs"
    __table_args__ = (
        UniqueConstraint("preparation_id", name="uq_execution_runs_preparation"),
        UniqueConstraint("idempotency_key", name="uq_execution_runs_idempotency_key"),
        Index("ix_execution_runs_project_status", "project_id", "status"),
        Index("ix_execution_runs_project_created", "project_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    preparation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("preparations.id", ondelete="RESTRICT"), nullable=False
    )
    pipeline_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pipeline_versions.id", ondelete="RESTRICT"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default=ExecutionRunStatus.QUEUED, nullable=False
    )
    engine_name: Mapped[str] = mapped_column(String(64), default="seatunnel", nullable=False)
    engine_job_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    quality_status: Mapped[str] = mapped_column(
        String(32), default=QualityStatus.PENDING, nullable=False
    )
    publish_status: Mapped[str] = mapped_column(
        String(32), default=PublishStatus.NOT_STARTED, nullable=False
    )
    rollback_status: Mapped[str] = mapped_column(
        String(32), default=RollbackStatus.NOT_REQUESTED, nullable=False
    )
    shadow_table: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error_table: Mapped[str | None] = mapped_column(String(256), nullable=True)


class OutboxEvent(TimestampMixin, Base):
    """保存与 ExecutionRun 同事务创建的待投递命令。"""

    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("deduplication_key", name="uq_outbox_events_deduplication_key"),
        Index("ix_outbox_events_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_outbox_events_project_created", "project_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=OutboxEventStatus.PENDING, nullable=False
    )
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # MVP 阶段用于 Worker 取回签发令牌；生产阶段应替换为 Vault/KMS 信封加密。
    capability_token: Mapped[str] = mapped_column(Text, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvidenceLedgerEvent(TimestampMixin, Base):
    """以项目为边界保存追加式哈希链证据。"""

    __tablename__ = "evidence_ledger_events"
    __table_args__ = (
        UniqueConstraint("project_id", "sequence_number", name="uq_evidence_project_sequence"),
        UniqueConstraint("event_hash", name="uq_evidence_event_hash"),
        Index("ix_evidence_ledger_project_created", "project_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    prev_event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class RuntimeSupervisionSnapshot(TimestampMixin, Base):
    """按运行保存预算、引擎状态和质量指标快照。"""

    __tablename__ = "runtime_supervision_snapshots"
    __table_args__ = (
        Index("ix_runtime_snapshots_execution_created", "execution_run_id", "created_at"),
        Index("ix_runtime_snapshots_project_created", "project_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    execution_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False
    )
    engine_status: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    exceeded_budget_fields: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)


class ExecutionQualityResult(TimestampMixin, Base):
    """保存一次执行最终质量报告和影子/错误表引用。"""

    __tablename__ = "execution_quality_results"
    __table_args__ = (
        UniqueConstraint("execution_run_id", name="uq_quality_execution_run"),
        Index("ix_quality_results_project_created", "project_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    execution_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_records: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    output_records: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    rejected_records: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    rejection_rate: Mapped[float] = mapped_column(nullable=False)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    shadow_table: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error_table: Mapped[str | None] = mapped_column(String(256), nullable=True)


class BenchmarkRun(TimestampMixin, Base):
    """保存 Benchmark 的可追踪运行事实，但不保存任何业务样本。"""

    __tablename__ = "benchmark_runs"
    __table_args__ = (
        Index("ix_benchmark_runs_project_created", "project_id", "created_at"),
        Index("ix_benchmark_runs_project_level_created", "project_id", "level", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    dataset_rows: Mapped[int] = mapped_column(nullable=False)
    repeat: Mapped[int] = mapped_column(nullable=False)
    seed: Mapped[int] = mapped_column(nullable=False)
    dataset_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
