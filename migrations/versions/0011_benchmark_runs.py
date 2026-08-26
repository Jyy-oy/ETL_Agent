"""持久化 M6 Benchmark 运行摘要，支持历史查询和审计追踪。

只保存固定参数、摘要和统计指标，不保存生成的业务样本。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_benchmark_runs"
down_revision: str | None = "0010_quality_supervision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建项目级 Benchmark 运行事实表和查询索引。"""
    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("dataset_rows", sa.Integer(), nullable=False),
        sa.Column("repeat", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("dataset_digest", sa.String(length=64), nullable=False),
        sa.Column("artifact_digest", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics_json", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_benchmark_runs_project_created", "benchmark_runs", ["project_id", "created_at"]
    )
    op.create_index(
        "ix_benchmark_runs_project_level_created",
        "benchmark_runs",
        ["project_id", "level", "created_at"],
    )


def downgrade() -> None:
    """按创建顺序逆向删除 Benchmark 索引和运行事实表。"""
    op.drop_index("ix_benchmark_runs_project_level_created", table_name="benchmark_runs")
    op.drop_index("ix_benchmark_runs_project_created", table_name="benchmark_runs")
    op.drop_table("benchmark_runs")
