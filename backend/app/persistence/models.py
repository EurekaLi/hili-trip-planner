"""用户闭环所需的 SQLAlchemy 关系模型。"""

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    """
    返回可直接写入数据库的 UTC 时间。

    Returns:
        带 UTC 时区的当前时间。
    """
    return datetime.now(timezone.utc)


def new_id() -> str:
    """
    生成不暴露数据库规模的随机主键。

    Returns:
        32 位十六进制 UUID 文本。
    """
    return uuid4().hex


class UserRecord(Base):
    """可跨设备登录并拥有旅行计划的用户。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(512))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )

    sessions: Mapped[list["AuthSessionRecord"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    trips: Mapped[list["TripRecord"]] = relationship(back_populates="owner")


class AuthSessionRecord(Base):
    """只保存随机会话令牌摘要的可撤销登录会话。"""

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )

    user: Mapped[UserRecord] = relationship(back_populates="sessions")


class TripRecord(Base):
    """行程资产的所有权、当前版本和编辑元数据。"""

    __tablename__ = "trips"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    guest_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    source_job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("trip_generation_jobs.job_id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    title: Mapped[str] = mapped_column(String(160))
    city: Mapped[str] = mapped_column(String(80), index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    locked_poi_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    owner: Mapped[Optional[UserRecord]] = relationship(back_populates="trips")
    versions: Mapped[list["TripVersionRecord"]] = relationship(
        back_populates="trip",
        cascade="all, delete-orphan",
        order_by="TripVersionRecord.version_number",
    )
    shares: Mapped[list["TripShareRecord"]] = relationship(
        back_populates="trip",
        cascade="all, delete-orphan",
    )
    feedback: Mapped[list["TripFeedbackRecord"]] = relationship(
        back_populates="trip",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_trips_owner_updated", "owner_user_id", "updated_at"),
        Index("ix_trips_guest_updated", "guest_id", "updated_at"),
    )


class TripVersionRecord(Base):
    """一次不可变的完整旅行计划快照。"""

    __tablename__ = "trip_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"),
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer)
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    change_type: Mapped[str] = mapped_column(String(40))
    change_summary: Mapped[str] = mapped_column(String(300), default="")
    created_by_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )

    trip: Mapped[TripRecord] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint("trip_id", "version_number", name="uq_trip_version"),
    )


class TripGenerationJobRecord(Base):
    """Redis 任务在长期数据库中的审计快照。"""

    __tablename__ = "trip_generation_jobs"

    job_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    request_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    owner_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    guest_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    trip_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    budget_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ExternalCallRecord(Base):
    """一次生成任务产生的可计费外部调用审计记录。"""

    __tablename__ = "external_call_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("trip_generation_jobs.job_id", ondelete="CASCADE"),
        index=True,
    )
    trip_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(80))
    operation: Mapped[str] = mapped_column(String(160))
    succeeded: Mapped[bool] = mapped_column(Boolean)
    elapsed_seconds: Mapped[float] = mapped_column(Float)
    units: Mapped[int] = mapped_column(Integer, default=1)
    cost_cny: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TripShareRecord(Base):
    """只读分享链接及其撤销和过期状态。"""

    __tablename__ = "trip_shares"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"),
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )

    trip: Mapped[TripRecord] = relationship(back_populates="shares")


class TripFeedbackRecord(Base):
    """用户或游客针对一个只读行程提交的评分和文本反馈。"""

    __tablename__ = "trip_feedback"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    guest_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )

    trip: Mapped[TripRecord] = relationship(back_populates="feedback")
