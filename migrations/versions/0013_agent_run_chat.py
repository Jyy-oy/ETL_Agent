"""为 AgentRun 增加候选审查对话快照。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_agent_run_chat"
down_revision: str | None = "0012_agent_run_progress"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加对话状态、消息和稳定错误字段，供前端轮询。"""
    op.add_column(
        "agent_runs",
        sa.Column("chat_status", sa.String(length=16), server_default="idle", nullable=False),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "chat_messages",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.add_column("agent_runs", sa.Column("chat_error_code", sa.String(length=64), nullable=True))
    op.add_column(
        "agent_runs", sa.Column("chat_error_detail", sa.String(length=512), nullable=True)
    )


def downgrade() -> None:
    """删除候选审查对话字段。"""
    op.drop_column("agent_runs", "chat_error_detail")
    op.drop_column("agent_runs", "chat_error_code")
    op.drop_column("agent_runs", "chat_messages")
    op.drop_column("agent_runs", "chat_status")
