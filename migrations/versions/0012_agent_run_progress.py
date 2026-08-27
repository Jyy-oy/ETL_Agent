"""为 AgentRun 增加澄清问题和逐节点进度快照。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_agent_run_progress"
down_revision: str | None = "0011_benchmark_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加异步 Agent 轮询所需的 JSON 快照字段。"""
    op.add_column(
        "agent_runs",
        sa.Column(
            "clarification_questions",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "validation_issues",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """删除 AgentRun 的异步进度快照字段。"""
    op.drop_column("agent_runs", "validation_issues")
    op.drop_column("agent_runs", "clarification_questions")
