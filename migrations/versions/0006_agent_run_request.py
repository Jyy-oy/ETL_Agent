"""Store sanitized generation request for clarification recovery.

Revision ID: 0006_agent_run_request
Revises: 0005_agent_generation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_agent_run_request"
down_revision: str | None = "0005_agent_generation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为 AgentRun 增加脱敏请求快照，以支持澄清回答后的恢复。"""
    op.add_column(
        "agent_runs",
        sa.Column("request_json", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
    )


def downgrade() -> None:
    """删除澄清恢复请求快照列。"""
    op.drop_column("agent_runs", "request_json")
