"""Create M2 connection and metadata profile tables.

Revision ID: 0002_connections_profiles
Revises: 0001_identity_project
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_connections_profiles"
down_revision: str | None = "0001_identity_project"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建连接登记和脱敏元数据 Profile 表。"""
    op.create_table(
        "connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("connection_type", sa.String(length=32), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database_name", sa.String(length=256), nullable=True),
        sa.Column("username", sa.String(length=128), nullable=True),
        sa.Column("secret_ref", sa.String(length=512), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "code", name="uq_connection_project_code"),
    )
    op.create_index("ix_connections_project_status", "connections", ["project_id", "status"])
    op.create_table(
        "metadata_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("profile_version", sa.String(length=32), server_default="v1", nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("schema_json", sa.JSON(), nullable=False),
        sa.Column("redacted_sample", sa.JSON(), nullable=False),
        sa.Column("estimated_row_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="ready", nullable=False),
        sa.Column("error_detail", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["connection_id"], ["connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id", "fingerprint", name="uq_profile_connection_fingerprint"
        ),
    )
    op.create_index(
        "ix_metadata_profiles_connection_created",
        "metadata_profiles",
        ["connection_id", "created_at"],
    )


def downgrade() -> None:
    """按外键依赖逆序删除 Profile 和连接登记表。"""
    op.drop_index("ix_metadata_profiles_connection_created", table_name="metadata_profiles")
    op.drop_table("metadata_profiles")
    op.drop_index("ix_connections_project_status", table_name="connections")
    op.drop_table("connections")
