"""create phase 4 user closure tables

Revision ID: 20260916_0001
Revises:
Create Date: 2026-09-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260916_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建用户、任务审计、行程版本、分享和反馈表。"""
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"], unique=True)
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table(
        "trip_generation_jobs",
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.String(length=32), nullable=True),
        sa.Column("guest_id", sa.String(length=64), nullable=True),
        sa.Column("trip_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index("ix_trip_generation_jobs_owner_user_id", "trip_generation_jobs", ["owner_user_id"])
    op.create_index("ix_trip_generation_jobs_guest_id", "trip_generation_jobs", ["guest_id"])
    op.create_index("ix_trip_generation_jobs_trip_id", "trip_generation_jobs", ["trip_id"])
    op.create_index("ix_trip_generation_jobs_status", "trip_generation_jobs", ["status"])
    op.create_table(
        "trips",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.String(length=32), nullable=True),
        sa.Column("guest_id", sa.String(length=64), nullable=True),
        sa.Column("source_job_id", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("city", sa.String(length=80), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("locked_poi_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_job_id"], ["trip_generation_jobs.job_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_job_id"),
    )
    op.create_index("ix_trips_owner_user_id", "trips", ["owner_user_id"])
    op.create_index("ix_trips_guest_id", "trips", ["guest_id"])
    op.create_index("ix_trips_city", "trips", ["city"])
    op.create_index("ix_trips_owner_updated", "trips", ["owner_user_id", "updated_at"])
    op.create_index("ix_trips_guest_updated", "trips", ["guest_id", "updated_at"])
    op.create_table(
        "trip_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("trip_id", sa.String(length=32), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("change_type", sa.String(length=40), nullable=False),
        sa.Column("change_summary", sa.String(length=300), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trip_id", "version_number", name="uq_trip_version"),
    )
    op.create_index("ix_trip_versions_trip_id", "trip_versions", ["trip_id"])
    op.create_table(
        "external_call_records",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("trip_id", sa.String(length=32), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("operation", sa.String(length=160), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("elapsed_seconds", sa.Float(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("cost_cny", sa.Float(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["trip_generation_jobs.job_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_external_call_records_job_id", "external_call_records", ["job_id"])
    op.create_index("ix_external_call_records_trip_id", "external_call_records", ["trip_id"])
    op.create_table(
        "trip_shares",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("trip_id", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trip_shares_trip_id", "trip_shares", ["trip_id"])
    op.create_index("ix_trip_shares_token_hash", "trip_shares", ["token_hash"], unique=True)
    op.create_table(
        "trip_feedback",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("trip_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=True),
        sa.Column("guest_id", sa.String(length=64), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trip_feedback_trip_id", "trip_feedback", ["trip_id"])


def downgrade() -> None:
    """按依赖逆序删除 Phase 4 用户闭环表。"""
    op.drop_table("trip_feedback")
    op.drop_table("trip_shares")
    op.drop_table("external_call_records")
    op.drop_table("trip_versions")
    op.drop_table("trips")
    op.drop_table("trip_generation_jobs")
    op.drop_table("auth_sessions")
    op.drop_table("users")
