"""add phase 5 operations and governance fields

Revision ID: 20260916_0002
Revises: 20260916_0001
Create Date: 2026-09-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260916_0002"
down_revision: Union[str, None] = "20260916_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """增加管理员、请求关联、预算和错误分类字段。"""
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index("ix_users_is_admin", "users", ["is_admin"])
    op.add_column(
        "trip_generation_jobs",
        sa.Column("request_id", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_trip_generation_jobs_request_id",
        "trip_generation_jobs",
        ["request_id"],
    )
    op.add_column(
        "trip_generation_jobs",
        sa.Column(
            "budget_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "external_call_records",
        sa.Column("error_type", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    """移除 Phase 5 新增字段并恢复 Phase 4 结构。"""
    op.drop_column("external_call_records", "error_type")
    op.drop_column("trip_generation_jobs", "budget_json")
    op.drop_index(
        "ix_trip_generation_jobs_request_id",
        table_name="trip_generation_jobs",
    )
    op.drop_column("trip_generation_jobs", "request_id")
    op.drop_index("ix_users_is_admin", table_name="users")
    op.drop_column("users", "is_admin")
