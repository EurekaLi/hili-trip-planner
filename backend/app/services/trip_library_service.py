"""用户行程资产、版本、分享、反馈和生成审计服务。"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
from typing import List, Optional, Tuple

from sqlalchemy import delete, desc, select, update
from sqlalchemy.orm import Session, selectinload, sessionmaker

from ..models.library_schemas import (
    FeedbackResponse,
    SavedTripResponse,
    ShareCreateResponse,
    SharedTripResponse,
    TripSummaryResponse,
    TripVersionResponse,
)
from ..models.schemas import TripPlan, TripRequest
from ..observability import get_request_id
from ..persistence.models import (
    ExternalCallRecord,
    TripFeedbackRecord,
    TripGenerationJobRecord,
    TripRecord,
    TripShareRecord,
    TripVersionRecord,
    utc_now,
)
from .trip_mutation_service import TripMutationService


class TripNotFoundError(LookupError):
    """行程、版本或分享链接不存在。"""


class TripAccessDeniedError(PermissionError):
    """当前身份不是行程所有者。"""


class TripVersionConflictError(ValueError):
    """客户端编辑基于过期版本。"""


@dataclass(frozen=True)
class OwnerIdentity:
    """一次请求解析出的登录用户或游客身份。"""

    user_id: Optional[str] = None
    guest_id: Optional[str] = None

    def is_available(self) -> bool:
        """
        判断请求是否具有可持久化所有者。

        Returns:
            存在用户 ID 或游客 ID 时返回 True。
        """
        return bool(self.user_id or self.guest_id)


class TripLibraryService:
    """以不可变版本管理用户行程和只读分享。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        mutation_service: Optional[TripMutationService] = None,
    ) -> None:
        """
        初始化持久化和可选编辑依赖。

        Args:
            session_factory: SQLAlchemy 会话工厂。
            mutation_service: 需要编辑重算时使用的服务。
        """
        self._session_factory = session_factory
        self._mutation_service = mutation_service

    def create_trip(
        self,
        identity: OwnerIdentity,
        plan: TripPlan,
        title: Optional[str] = None,
        source_job_id: Optional[str] = None,
        change_type: str = "created",
        change_summary: str = "创建旅行计划",
    ) -> SavedTripResponse:
        """
        创建行程资产和首个不可变版本。

        Args:
            identity: 登录用户或游客身份。
            plan: 需要保存的完整计划。
            title: 可选自定义标题。
            source_job_id: 可选生成任务 ID。
            change_type: 首版本来源类型。
            change_summary: 首版本说明。

        Returns:
            新建行程及其当前版本。

        Raises:
            TripAccessDeniedError: 请求没有可识别所有者时抛出。
        """
        self._require_identity(identity)
        with self._session_factory() as session:
            if source_job_id:
                existing = session.scalar(
                    select(TripRecord).where(TripRecord.source_job_id == source_job_id)
                )
                if existing is not None:
                    self._assert_owner(existing, identity)
                    return self._build_saved_trip(session, existing)

            trip = TripRecord(
                owner_user_id=identity.user_id,
                guest_id=None if identity.user_id else identity.guest_id,
                source_job_id=source_job_id,
                title=(title or f"{plan.city} {plan.start_date} 行程").strip(),
                city=plan.city,
                current_version=1,
                locked_poi_ids=[],
            )
            session.add(trip)
            session.flush()
            session.add(
                TripVersionRecord(
                    trip_id=trip.id,
                    version_number=1,
                    plan_json=plan.model_dump(mode="json"),
                    change_type=change_type,
                    change_summary=change_summary,
                    created_by_user_id=identity.user_id,
                )
            )
            session.commit()
            return self.get_trip(identity, trip.id)

    def list_trips(self, identity: OwnerIdentity) -> List[TripSummaryResponse]:
        """
        按最近更新时间列出当前身份拥有的行程。

        Args:
            identity: 登录用户或游客身份。

        Returns:
            不包含完整计划 JSON 的行程摘要列表。
        """
        self._require_identity(identity)
        with self._session_factory() as session:
            query = select(TripRecord).where(TripRecord.deleted_at.is_(None))
            query = self._apply_owner_filter(query, identity)
            trips = session.scalars(query.order_by(desc(TripRecord.updated_at))).all()
            summaries = []
            for trip in trips:
                version = self._get_version(session, trip.id, trip.current_version)
                plan = TripPlan.model_validate(version.plan_json)
                summaries.append(
                    TripSummaryResponse(
                        id=trip.id,
                        title=trip.title,
                        city=trip.city,
                        start_date=plan.start_date,
                        end_date=plan.end_date,
                        current_version=trip.current_version,
                        created_at=trip.created_at,
                        updated_at=trip.updated_at,
                    )
                )
            return summaries

    def get_trip(self, identity: OwnerIdentity, trip_id: str) -> SavedTripResponse:
        """
        获取当前身份拥有的完整行程和版本历史。

        Args:
            identity: 登录用户或游客身份。
            trip_id: 行程资产 ID。

        Returns:
            当前计划、锁定信息和版本历史。
        """
        self._require_identity(identity)
        with self._session_factory() as session:
            trip = self._get_trip_record(session, trip_id)
            self._assert_owner(trip, identity)
            return self._build_saved_trip(session, trip)

    def edit_trip(
        self,
        identity: OwnerIdentity,
        trip_id: str,
        base_version: int,
        operations,
        change_summary: str,
    ) -> SavedTripResponse:
        """
        基于当前版本应用编辑、重算并追加新版本。

        Args:
            identity: 登录用户或游客身份。
            trip_id: 行程资产 ID。
            base_version: 客户端编辑所基于的版本号。
            operations: 已通过 Pydantic 校验的结构化操作。
            change_summary: 用户可读的版本说明。

        Returns:
            保存后的新当前版本。

        Raises:
            TripVersionConflictError: 当前版本已经变化时抛出。
        """
        if self._mutation_service is None:
            raise RuntimeError("行程编辑服务未配置")
        current = self.get_trip(identity, trip_id)
        if current.current_version != base_version:
            raise TripVersionConflictError("行程已在其他位置更新，请刷新后重试")
        mutated, locked = self._mutation_service.apply(
            current.plan,
            current.locked_poi_ids,
            operations,
        )
        return self._append_version(
            identity=identity,
            trip_id=trip_id,
            base_version=base_version,
            plan=mutated,
            locked_poi_ids=locked,
            change_type="edited",
            change_summary=change_summary,
        )

    def restore_version(
        self,
        identity: OwnerIdentity,
        trip_id: str,
        base_version: int,
        version_number: int,
    ) -> SavedTripResponse:
        """
        把历史快照复制成新的当前版本而不删除中间历史。

        Args:
            identity: 登录用户或游客身份。
            trip_id: 行程资产 ID。
            base_version: 客户端看到的当前版本。
            version_number: 需要恢复的历史版本。

        Returns:
            新追加的恢复版本。
        """
        current = self.get_trip(identity, trip_id)
        with self._session_factory() as session:
            historical = self._get_version(session, trip_id, version_number)
            plan = TripPlan.model_validate(historical.plan_json)
        return self._append_version(
            identity=identity,
            trip_id=trip_id,
            base_version=base_version,
            plan=plan,
            locked_poi_ids=current.locked_poi_ids,
            change_type="restored",
            change_summary=f"恢复版本 {version_number}",
        )

    def delete_trip(self, identity: OwnerIdentity, trip_id: str) -> None:
        """
        软删除一个行程，保留版本和审计数据。

        Args:
            identity: 登录用户或游客身份。
            trip_id: 行程资产 ID。
        """
        with self._session_factory() as session:
            trip = self._get_trip_record(session, trip_id)
            self._assert_owner(trip, identity)
            trip.deleted_at = utc_now()
            trip.updated_at = utc_now()
            session.commit()

    def rename_trip(
        self,
        identity: OwnerIdentity,
        trip_id: str,
        title: str,
    ) -> SavedTripResponse:
        """
        修改行程展示标题而不创建内容版本。

        Args:
            identity: 登录用户或游客身份。
            trip_id: 行程资产 ID。
            title: 已校验的新标题。

        Returns:
            标题更新后的完整行程。
        """
        with self._session_factory() as session:
            trip = self._get_trip_record(session, trip_id)
            self._assert_owner(trip, identity)
            trip.title = title.strip()
            trip.updated_at = utc_now()
            session.commit()
        return self.get_trip(identity, trip_id)

    def create_share(
        self,
        identity: OwnerIdentity,
        trip_id: str,
        version_number: Optional[int],
        expires_in_days: Optional[int],
    ) -> ShareCreateResponse:
        """
        为一个固定版本创建可撤销只读令牌。

        Args:
            identity: 行程所有者身份。
            trip_id: 行程资产 ID。
            version_number: 可选历史版本，默认当前版本。
            expires_in_days: 可选有效天数，None 表示不过期。

        Returns:
            只返回一次原始令牌和前端分享路径。
        """
        with self._session_factory() as session:
            trip = self._get_trip_record(session, trip_id)
            self._assert_owner(trip, identity)
            selected_version = version_number or trip.current_version
            self._get_version(session, trip.id, selected_version)
            token = token_urlsafe(24)
            expires_at = (
                datetime.now(timezone.utc) + timedelta(days=expires_in_days)
                if expires_in_days is not None
                else None
            )
            share = TripShareRecord(
                trip_id=trip.id,
                token_hash=self._hash_token(token),
                version_number=selected_version,
                expires_at=expires_at,
            )
            session.add(share)
            session.commit()
            return ShareCreateResponse(
                share_id=share.id,
                share_token=token,
                share_path=f"/share/{token}",
                trip_id=trip.id,
                version_number=selected_version,
                expires_at=expires_at,
            )

    def revoke_share(
        self,
        identity: OwnerIdentity,
        trip_id: str,
        share_id: str,
    ) -> None:
        """
        撤销当前所有者创建的分享链接。

        Args:
            identity: 行程所有者身份。
            trip_id: 行程资产 ID。
            share_id: 需要撤销的分享记录 ID。

        Raises:
            TripNotFoundError: 分享记录不属于指定行程时抛出。
        """
        with self._session_factory() as session:
            trip = self._get_trip_record(session, trip_id)
            self._assert_owner(trip, identity)
            share = session.scalar(
                select(TripShareRecord).where(
                    TripShareRecord.id == share_id,
                    TripShareRecord.trip_id == trip_id,
                )
            )
            if share is None:
                raise TripNotFoundError("分享链接不存在")
            share.active = False
            session.commit()

    def get_shared_trip(self, token: str) -> SharedTripResponse:
        """
        解析有效分享令牌并读取固定版本快照。

        Args:
            token: URL 中的原始分享令牌。

        Returns:
            不包含所有者信息的只读行程。
        """
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            share = session.scalar(
                select(TripShareRecord)
                .options(selectinload(TripShareRecord.trip))
                .where(
                    TripShareRecord.token_hash == self._hash_token(token),
                    TripShareRecord.active.is_(True),
                )
            )
            if share is None or share.trip.deleted_at is not None:
                raise TripNotFoundError("分享链接不存在或已失效")
            if share.expires_at is not None:
                expires_at = self._as_utc(share.expires_at)
                if expires_at <= now:
                    raise TripNotFoundError("分享链接不存在或已失效")
            version = self._get_version(
                session,
                share.trip_id,
                share.version_number,
            )
            return SharedTripResponse(
                trip_id=share.trip_id,
                title=share.trip.title,
                version_number=share.version_number,
                plan=TripPlan.model_validate(version.plan_json),
                created_at=share.created_at,
            )

    def add_feedback(
        self,
        token: str,
        identity: OwnerIdentity,
        rating: int,
        comment: str,
    ) -> FeedbackResponse:
        """
        对有效分享行程写入评分和文本反馈。

        Args:
            token: 有效分享令牌。
            identity: 可选登录用户或游客身份。
            rating: 1 到 5 星评分。
            comment: 可选文本建议。

        Returns:
            新建反馈的公开字段。
        """
        shared = self.get_shared_trip(token)
        with self._session_factory() as session:
            feedback = TripFeedbackRecord(
                trip_id=shared.trip_id,
                user_id=identity.user_id,
                guest_id=None if identity.user_id else identity.guest_id,
                rating=rating,
                comment=comment.strip(),
            )
            session.add(feedback)
            session.commit()
            session.refresh(feedback)
            return FeedbackResponse(
                id=feedback.id,
                rating=feedback.rating,
                comment=feedback.comment,
                created_at=feedback.created_at,
            )

    def record_job_submission(
        self,
        job_id: str,
        identity: OwnerIdentity,
        request: TripRequest,
        status: str,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        """
        幂等记录一次 Redis/Celery 生成任务的长期快照。

        Args:
            job_id: 公开任务 ID。
            identity: 提交任务的用户或游客。
            request: 原始旅行需求。
            status: 当前任务状态。
            created_at: Redis 记录创建时间。
            updated_at: Redis 记录更新时间。
        """
        with self._session_factory() as session:
            existing = session.get(TripGenerationJobRecord, job_id)
            if existing is None:
                session.add(
                    TripGenerationJobRecord(
                        job_id=job_id,
                        request_id=get_request_id() or None,
                        owner_user_id=identity.user_id,
                        guest_id=None if identity.user_id else identity.guest_id,
                        status=status,
                        request_json=request.model_dump(mode="json"),
                        created_at=created_at,
                        updated_at=updated_at,
                    )
                )
            elif not existing.request_id and get_request_id():
                existing.request_id = get_request_id()
            session.commit()

    def archive_generated_trip(
        self,
        job_id: str,
        identity: OwnerIdentity,
        request: TripRequest,
        plan: TripPlan,
        created_at: datetime,
        updated_at: datetime,
        finished_at: datetime,
    ) -> SavedTripResponse:
        """
        幂等归档成功任务、行程、首版本和外部调用明细。

        Args:
            job_id: 公开任务 ID。
            identity: 任务所有者身份。
            request: 原始旅行需求。
            plan: 成功生成的计划。
            created_at: 任务创建时间。
            updated_at: 任务更新时间。
            finished_at: 任务完成时间。

        Returns:
            已创建或幂等恢复的行程资产。
        """
        self.record_job_submission(
            job_id,
            identity,
            request,
            "succeeded",
            created_at,
            updated_at,
        )
        effective_identity = identity
        with self._session_factory() as session:
            job = session.get(TripGenerationJobRecord, job_id)
            if job is not None and job.owner_user_id:
                effective_identity = OwnerIdentity(user_id=job.owner_user_id)
        saved = self.create_trip(
            identity=effective_identity,
            plan=plan,
            source_job_id=job_id,
            change_type="generated",
            change_summary="多 Agent 生成行程",
        )
        with self._session_factory() as session:
            job = session.get(TripGenerationJobRecord, job_id)
            if job is None:
                raise RuntimeError("生成任务审计记录不存在")
            job.status = "succeeded"
            job.trip_id = saved.id
            job.budget_json = self._summarize_external_budget(plan)
            job.updated_at = updated_at
            job.finished_at = finished_at
            session.execute(
                delete(ExternalCallRecord).where(ExternalCallRecord.job_id == job_id)
            )
            session.add_all(
                [
                    ExternalCallRecord(
                        job_id=job_id,
                        trip_id=saved.id,
                        provider=metric.provider,
                        operation=metric.operation,
                        succeeded=metric.succeeded,
                        elapsed_seconds=metric.elapsed_seconds,
                        units=metric.units,
                        cost_cny=metric.cost_cny,
                        error_type=metric.error_type,
                        requested_at=metric.requested_at,
                    )
                    for metric in plan.external_calls
                ]
            )
            session.commit()
        return saved

    def record_job_failure(
        self,
        job_id: str,
        identity: OwnerIdentity,
        request: TripRequest,
        status: str,
        error_code: str,
        error_message: str,
        created_at: datetime,
        updated_at: datetime,
        finished_at: datetime,
        budget_snapshot: Optional[dict] = None,
    ) -> None:
        """
        保存失败或取消任务的最终审计状态。

        Args:
            job_id: 公开任务 ID。
            identity: 任务所有者身份。
            request: 原始旅行需求。
            status: 失败或取消状态。
            error_code: 稳定错误码。
            error_message: 脱敏错误摘要。
            created_at: 任务创建时间。
            updated_at: 任务更新时间。
            finished_at: 任务结束时间。
            budget_snapshot: 失败前已经消费的任务预算快照。
        """
        self.record_job_submission(
            job_id,
            identity,
            request,
            status,
            created_at,
            updated_at,
        )
        with self._session_factory() as session:
            job = session.get(TripGenerationJobRecord, job_id)
            if job is None:
                return
            job.status = status
            job.error_code = error_code
            job.error_message = error_message[:500]
            if budget_snapshot is not None:
                job.budget_json = budget_snapshot
            job.updated_at = updated_at
            job.finished_at = finished_at
            session.commit()

    @staticmethod
    def _summarize_external_budget(plan: TripPlan) -> dict:
        """
        从成功计划的调用明细计算任务预算实际用量。

        Args:
            plan: 包含地图和 LLM 调用明细的最终计划。

        Returns:
            可供运营查询的次数和已知费用汇总。
        """
        llm_calls = sum(
            metric.units
            for metric in plan.external_calls
            if metric.provider == "llm"
        )
        external_calls = sum(
            metric.units
            for metric in plan.external_calls
            if metric.provider != "llm"
        )
        known_cost = sum(
            metric.cost_cny or 0.0
            for metric in plan.external_calls
        )
        return {
            "llm_calls": llm_calls,
            "external_calls": external_calls,
            "known_cost_cny": round(known_cost, 6),
        }

    def _append_version(
        self,
        identity: OwnerIdentity,
        trip_id: str,
        base_version: int,
        plan: TripPlan,
        locked_poi_ids: List[str],
        change_type: str,
        change_summary: str,
    ) -> SavedTripResponse:
        """
        使用乐观锁原子追加版本并更新当前指针。

        Args:
            identity: 行程所有者身份。
            trip_id: 行程资产 ID。
            base_version: 客户端读取时的当前版本。
            plan: 新版本完整快照。
            locked_poi_ids: 新版本对应的锁定标识。
            change_type: 稳定变更类型。
            change_summary: 用户可读说明。

        Returns:
            新当前版本。
        """
        new_version = base_version + 1
        with self._session_factory() as session:
            trip = self._get_trip_record(session, trip_id)
            self._assert_owner(trip, identity)
            owner_conditions = self._owner_conditions(identity)
            result = session.execute(
                update(TripRecord)
                .where(
                    TripRecord.id == trip_id,
                    TripRecord.current_version == base_version,
                    *owner_conditions,
                )
                .values(
                    current_version=new_version,
                    locked_poi_ids=locked_poi_ids,
                    city=plan.city,
                    updated_at=utc_now(),
                )
            )
            if not result.rowcount:
                session.rollback()
                raise TripVersionConflictError("行程已在其他位置更新，请刷新后重试")
            session.add(
                TripVersionRecord(
                    trip_id=trip_id,
                    version_number=new_version,
                    plan_json=plan.model_dump(mode="json"),
                    change_type=change_type,
                    change_summary=change_summary.strip(),
                    created_by_user_id=identity.user_id,
                )
            )
            session.commit()
        return self.get_trip(identity, trip_id)

    def _build_saved_trip(
        self,
        session: Session,
        trip: TripRecord,
    ) -> SavedTripResponse:
        """
        把关系模型转换为稳定 API 响应。

        Args:
            session: 当前数据库会话。
            trip: 已授权的行程记录。

        Returns:
            当前计划和按版本倒序排列的历史元数据。
        """
        versions = session.scalars(
            select(TripVersionRecord)
            .where(TripVersionRecord.trip_id == trip.id)
            .order_by(desc(TripVersionRecord.version_number))
        ).all()
        current = next(
            (item for item in versions if item.version_number == trip.current_version),
            None,
        )
        if current is None:
            raise TripNotFoundError("当前行程版本不存在")
        return SavedTripResponse(
            id=trip.id,
            title=trip.title,
            city=trip.city,
            current_version=trip.current_version,
            locked_poi_ids=list(trip.locked_poi_ids or []),
            plan=TripPlan.model_validate(current.plan_json),
            versions=[
                TripVersionResponse(
                    version_number=version.version_number,
                    change_type=version.change_type,
                    change_summary=version.change_summary,
                    created_at=version.created_at,
                )
                for version in versions
            ],
            created_at=trip.created_at,
            updated_at=trip.updated_at,
        )

    @staticmethod
    def _get_trip_record(session: Session, trip_id: str) -> TripRecord:
        """
        读取一个未删除行程。

        Args:
            session: 当前数据库会话。
            trip_id: 行程资产 ID。

        Returns:
            未删除的行程记录。

        Raises:
            TripNotFoundError: 行程不存在或已删除时抛出。
        """
        trip = session.scalar(
            select(TripRecord).where(
                TripRecord.id == trip_id,
                TripRecord.deleted_at.is_(None),
            )
        )
        if trip is None:
            raise TripNotFoundError("行程不存在")
        return trip

    @staticmethod
    def _get_version(
        session: Session,
        trip_id: str,
        version_number: int,
    ) -> TripVersionRecord:
        """
        读取指定行程版本。

        Args:
            session: 当前数据库会话。
            trip_id: 行程资产 ID。
            version_number: 版本号。

        Returns:
            不可变版本记录。

        Raises:
            TripNotFoundError: 版本不存在时抛出。
        """
        version = session.scalar(
            select(TripVersionRecord).where(
                TripVersionRecord.trip_id == trip_id,
                TripVersionRecord.version_number == version_number,
            )
        )
        if version is None:
            raise TripNotFoundError("行程版本不存在")
        return version

    @staticmethod
    def _assert_owner(trip: TripRecord, identity: OwnerIdentity) -> None:
        """
        校验行程属于当前用户或游客。

        Args:
            trip: 待访问行程。
            identity: 当前请求身份。

        Raises:
            TripAccessDeniedError: 所有权不匹配时抛出。
        """
        if identity.user_id and trip.owner_user_id == identity.user_id:
            return
        if (
            not trip.owner_user_id
            and identity.guest_id
            and trip.guest_id == identity.guest_id
        ):
            return
        raise TripAccessDeniedError("无权访问该行程")

    @staticmethod
    def _require_identity(identity: OwnerIdentity) -> None:
        """
        要求请求至少具有一种所有者身份。

        Args:
            identity: 当前请求身份。

        Raises:
            TripAccessDeniedError: 用户和游客标识均缺失时抛出。
        """
        if not identity.is_available():
            raise TripAccessDeniedError("缺少游客或登录身份")

    @staticmethod
    def _owner_conditions(identity: OwnerIdentity):
        """
        生成乐观更新使用的所有权 SQL 条件。

        Args:
            identity: 当前请求身份。

        Returns:
            只匹配当前用户或游客的 SQL 条件列表。
        """
        if identity.user_id:
            return [TripRecord.owner_user_id == identity.user_id]
        return [
            TripRecord.owner_user_id.is_(None),
            TripRecord.guest_id == identity.guest_id,
        ]

    @staticmethod
    def _apply_owner_filter(query, identity: OwnerIdentity):
        """
        给行程列表查询添加所有者过滤。

        Args:
            query: SQLAlchemy Select 查询。
            identity: 当前请求身份。

        Returns:
            只返回当前身份行程的查询。
        """
        if identity.user_id:
            return query.where(TripRecord.owner_user_id == identity.user_id)
        return query.where(
            TripRecord.owner_user_id.is_(None),
            TripRecord.guest_id == identity.guest_id,
        )

    @staticmethod
    def _hash_token(token: str) -> str:
        """
        计算分享令牌的不可逆摘要。

        Args:
            token: URL 中的原始令牌。

        Returns:
            SHA-256 十六进制摘要。
        """
        return sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """
        把 SQLite 返回的无时区时间恢复为 UTC。

        Args:
            value: 数据库时间。

        Returns:
            带 UTC 时区的时间。
        """
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
