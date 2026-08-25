"""Create PipelineVersion and AgentRun generation evidence tables.

Revision ID: 0005_agent_generation
Revises: 0004_file_assets
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_agent_generation"
down_revision: str | None = "0004_file_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 Pipeline、不可变版本、AgentRun 和生成尝试证据表。"""
    op.create_table(
        "pipelines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "code", name="uq_pipeline_project_code"),
    )
    op.create_index("ix_pipelines_project_status", "pipelines", ["project_id", "status"])
    op.create_table(
        "pipeline_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("immutable", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("artifact_digest", sa.String(length=64), nullable=True),
        sa.Column("etl_plan_json", sa.JSON(), nullable=True),
        sa.Column("hocon", sa.Text(), nullable=True),
        sa.Column("source_profile_ids", sa.JSON(), nullable=False),
        sa.Column("target_profile_ids", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pipeline_id"], ["pipelines.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_digest"),
        sa.UniqueConstraint("pipeline_id", "version_number", name="uq_pipeline_version_number"),
    )
    op.create_index(
        "ix_pipeline_versions_pipeline_status", "pipeline_versions", ["pipeline_id", "status"]
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_version_id", sa.Uuid(), nullable=True),
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="running", nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_digest", sa.String(length=64), nullable=False),
        sa.Column("result_digest", sa.String(length=64), nullable=True),
        sa.Column("repair_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("node_trace", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_version_id"], ["pipeline_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id"),
    )
    op.create_index("ix_agent_runs_project_created", "agent_runs", ["project_id", "created_at"])
    op.create_index("ix_agent_runs_thread", "agent_runs", ["thread_id"])
    op.create_table(
        "generation_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=True),
        sa.Column("output_digest", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_generation_attempts_run_number",
        "generation_attempts",
        ["agent_run_id", "attempt_number"],
    )


def downgrade() -> None:
    """按依赖顺序删除阶段 3 证据表，不删除 LangGraph checkpoint 表。"""
    op.drop_index("ix_generation_attempts_run_number", table_name="generation_attempts")
    op.drop_table("generation_attempts")
    op.drop_index("ix_agent_runs_thread", table_name="agent_runs")
    op.drop_index("ix_agent_runs_project_created", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_pipeline_versions_pipeline_status", table_name="pipeline_versions")
    op.drop_table("pipeline_versions")
    op.drop_index("ix_pipelines_project_status", table_name="pipelines")
    op.drop_table("pipelines")
