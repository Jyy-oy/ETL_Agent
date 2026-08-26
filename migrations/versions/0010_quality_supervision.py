"""补齐 M5 质量结果、运行监督和发布回滚事实。

Revision ID: 0010_quality_supervision
Revises: 0009_execution_outbox_ledger
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_quality_supervision"
down_revision: str | None = "0009_execution_outbox_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加执行闭环所需的状态字段和事实表。"""
    with op.batch_alter_table("execution_runs") as batch:
        batch.add_column(
            sa.Column(
                "quality_status", sa.String(length=32), server_default="pending", nullable=False
            )
        )
        batch.add_column(
            sa.Column(
                "publish_status", sa.String(length=32), server_default="not_started", nullable=False
            )
        )
        batch.add_column(
            sa.Column(
                "rollback_status",
                sa.String(length=32),
                server_default="not_requested",
                nullable=False,
            )
        )
        batch.add_column(sa.Column("shadow_table", sa.String(length=256), nullable=True))
        batch.add_column(sa.Column("error_table", sa.String(length=256), nullable=True))

    op.create_table(
        "runtime_supervision_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("execution_run_id", sa.Uuid(), nullable=False),
        sa.Column("engine_status", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column(
            "observed_metrics", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False
        ),
        sa.Column(
            "exceeded_budget_fields",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column("detail", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_run_id"], ["execution_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runtime_snapshots_execution_created",
        "runtime_supervision_snapshots",
        ["execution_run_id", "created_at"],
    )
    op.create_index(
        "ix_runtime_snapshots_project_created",
        "runtime_supervision_snapshots",
        ["project_id", "created_at"],
    )

    op.create_table(
        "execution_quality_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("execution_run_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_records", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("output_records", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("rejected_records", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("rejection_rate", sa.Float(), nullable=False),
        sa.Column("report_json", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("shadow_table", sa.String(length=256), nullable=True),
        sa.Column("error_table", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_run_id"], ["execution_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_run_id", name="uq_quality_execution_run"),
    )
    op.create_index(
        "ix_quality_results_project_created",
        "execution_quality_results",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    """按依赖逆序删除质量和监督事实。"""
    op.drop_index("ix_quality_results_project_created", table_name="execution_quality_results")
    op.drop_table("execution_quality_results")
    op.drop_index(
        "ix_runtime_snapshots_project_created", table_name="runtime_supervision_snapshots"
    )
    op.drop_index(
        "ix_runtime_snapshots_execution_created", table_name="runtime_supervision_snapshots"
    )
    op.drop_table("runtime_supervision_snapshots")
    with op.batch_alter_table("execution_runs") as batch:
        batch.drop_column("error_table")
        batch.drop_column("shadow_table")
        batch.drop_column("rollback_status")
        batch.drop_column("publish_status")
        batch.drop_column("quality_status")
