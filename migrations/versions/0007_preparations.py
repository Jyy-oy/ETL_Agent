"""Create immutable Preparation facts for the M4.1 Prepare stage.

Revision ID: 0007_preparations
Revises: 0006_agent_run_request
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_preparations"
down_revision: str | None = "0006_agent_run_request"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 Prepare 阶段冻结的策略决策和输入事实表。"""
    op.create_table(
        "preparations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_version_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="approval_pending", nullable=False
        ),
        sa.Column("risk_level", sa.String(length=8), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "required_roles", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False
        ),
        sa.Column(
            "resource_scope", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False
        ),
        sa.Column(
            "runtime_budget", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False
        ),
        sa.Column("facts_json", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["pipeline_version_id"], ["pipeline_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_preparations_project_status", "preparations", ["project_id", "status"])
    op.create_index(
        "ix_preparations_version_created",
        "preparations",
        ["pipeline_version_id", "created_at"],
    )


def downgrade() -> None:
    """删除 Preparation 表及其索引，不影响前序生成事实。"""
    op.drop_index("ix_preparations_version_created", table_name="preparations")
    op.drop_index("ix_preparations_project_status", table_name="preparations")
    op.drop_table("preparations")
