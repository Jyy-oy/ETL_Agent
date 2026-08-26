"""Create independent approval slots for Preparation.

Revision ID: 0008_approval_requests
Revises: 0007_preparations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_approval_requests"
down_revision: str | None = "0007_preparations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建按职责槽拆分的审批请求表。"""
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("preparation_id", sa.Uuid(), nullable=False),
        sa.Column("required_role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("approver_id", sa.Uuid(), nullable=True),
        sa.Column("comment", sa.String(length=1000), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["approver_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["preparation_id"], ["preparations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("preparation_id", "required_role", name="uq_approval_preparation_role"),
    )
    op.create_index(
        "ix_approval_requests_project_status", "approval_requests", ["project_id", "status"]
    )
    op.create_index(
        "ix_approval_requests_preparation_status",
        "approval_requests",
        ["preparation_id", "status"],
    )


def downgrade() -> None:
    """删除审批请求表及其索引，不删除 Preparation 事实。"""
    op.drop_index("ix_approval_requests_preparation_status", table_name="approval_requests")
    op.drop_index("ix_approval_requests_project_status", table_name="approval_requests")
    op.drop_table("approval_requests")
