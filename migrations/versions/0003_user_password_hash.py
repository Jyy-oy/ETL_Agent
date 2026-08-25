"""Add local development password hashes to users.

Revision ID: 0003_user_password_hash
Revises: 0002_connections_profiles
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_user_password_hash"
down_revision: str | None = "0002_connections_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为本地账号增加可轮换的密码哈希字段，不保存密码明文。"""
    op.add_column("users", sa.Column("password_hash", sa.String(length=256), nullable=True))


def downgrade() -> None:
    """删除本地账号密码哈希字段，保留其他用户身份数据。"""
    op.drop_column("users", "password_hash")
