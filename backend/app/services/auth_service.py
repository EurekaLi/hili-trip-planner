"""账号密码、可撤销会话和游客资产认领服务。"""

from datetime import datetime, timedelta, timezone
from hashlib import scrypt, sha256
from hmac import compare_digest
from secrets import token_bytes, token_urlsafe
from typing import Optional, Tuple

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..persistence.models import (
    AuthSessionRecord,
    TripGenerationJobRecord,
    TripFeedbackRecord,
    TripRecord,
    UserRecord,
)


class AccountConflictError(ValueError):
    """注册邮箱已存在。"""


class AuthenticationError(ValueError):
    """登录凭据无效。"""


class AuthService:
    """管理用户、密码摘要和服务端登录会话。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        session_ttl_days: int,
    ) -> None:
        """
        初始化认证服务依赖。

        Args:
            session_factory: 短生命周期 SQLAlchemy 会话工厂。
            session_ttl_days: 登录会话有效天数。
        """
        self._session_factory = session_factory
        self._session_ttl_days = session_ttl_days

    def register(
        self,
        email: str,
        password: str,
        display_name: str,
        guest_id: Optional[str] = None,
    ) -> Tuple[UserRecord, str, int]:
        """
        创建账号、签发会话并认领当前游客行程。

        Args:
            email: 用户邮箱。
            password: 待哈希的明文密码。
            display_name: 用户显示名称。
            guest_id: 当前浏览器的可选游客标识。

        Returns:
            新用户、原始会话令牌和认领行程数。

        Raises:
            AccountConflictError: 邮箱已被注册时抛出。
        """
        normalized_email = email.strip().lower()
        with self._session_factory() as session:
            user = UserRecord(
                email=normalized_email,
                display_name=display_name.strip(),
                password_hash=self.hash_password(password),
            )
            session.add(user)
            try:
                session.flush()
            except IntegrityError as exc:
                session.rollback()
                raise AccountConflictError("该邮箱已注册") from exc
            claimed = self._claim_guest_trips(session, user.id, guest_id)
            token = self._create_session(session, user.id)
            session.commit()
            session.refresh(user)
            return user, token, claimed

    def login(
        self,
        email: str,
        password: str,
        guest_id: Optional[str] = None,
    ) -> Tuple[UserRecord, str, int]:
        """
        校验密码、签发新会话并认领游客行程。

        Args:
            email: 用户邮箱。
            password: 用户输入的明文密码。
            guest_id: 当前浏览器的可选游客标识。

        Returns:
            用户、原始会话令牌和认领行程数。

        Raises:
            AuthenticationError: 用户不存在或密码不匹配时抛出。
        """
        normalized_email = email.strip().lower()
        with self._session_factory() as session:
            user = session.scalar(
                select(UserRecord).where(UserRecord.email == normalized_email)
            )
            if user is None or not self.verify_password(password, user.password_hash):
                raise AuthenticationError("邮箱或密码错误")
            claimed = self._claim_guest_trips(session, user.id, guest_id)
            token = self._create_session(session, user.id)
            session.commit()
            return user, token, claimed

    def resolve_user(self, token: Optional[str]) -> Optional[UserRecord]:
        """
        根据原始会话令牌解析仍有效的用户。

        Args:
            token: Cookie 中的可选原始令牌。

        Returns:
            会话有效时返回用户，否则返回 None。
        """
        if not token:
            return None
        token_hash = self.hash_token(token)
        with self._session_factory() as session:
            return session.scalar(
                select(UserRecord)
                .join(AuthSessionRecord)
                .where(
                    AuthSessionRecord.token_hash == token_hash,
                    AuthSessionRecord.expires_at > datetime.now(timezone.utc),
                )
            )

    def logout(self, token: Optional[str]) -> None:
        """
        删除当前会话令牌摘要。

        Args:
            token: Cookie 中的可选原始令牌。
        """
        if not token:
            return
        with self._session_factory() as session:
            session.execute(
                delete(AuthSessionRecord).where(
                    AuthSessionRecord.token_hash == self.hash_token(token)
                )
            )
            session.commit()

    def delete_account(self, user_id: str, password: str) -> dict[str, int]:
        """
        永久删除账号、行程、反馈、任务和可关联外部调用。

        Args:
            user_id: 当前已认证用户 ID。
            password: 用户再次输入的明文密码。

        Returns:
            被删除的行程、任务和反馈数量。

        Raises:
            AuthenticationError: 用户不存在或密码不匹配时抛出。
        """
        with self._session_factory() as session:
            user = session.get(UserRecord, user_id)
            if user is None or not self.verify_password(
                password,
                user.password_hash,
            ):
                raise AuthenticationError("密码错误，未删除账号")
            feedback_result = session.execute(
                delete(TripFeedbackRecord).where(
                    TripFeedbackRecord.user_id == user_id
                )
            )
            trip_result = session.execute(
                delete(TripRecord).where(TripRecord.owner_user_id == user_id)
            )
            job_result = session.execute(
                delete(TripGenerationJobRecord).where(
                    TripGenerationJobRecord.owner_user_id == user_id
                )
            )
            session.delete(user)
            session.commit()
            return {
                "trips": int(trip_result.rowcount or 0),
                "jobs": int(job_result.rowcount or 0),
                "feedback": int(feedback_result.rowcount or 0),
            }

    def promote_admin(self, email: str) -> UserRecord:
        """
        由本地运维命令把现有账号提升为管理员。

        Args:
            email: 已完成注册的用户邮箱。

        Returns:
            更新后的管理员用户。

        Raises:
            AuthenticationError: 邮箱对应账号不存在时抛出。
        """
        normalized_email = email.strip().lower()
        with self._session_factory() as session:
            user = session.scalar(
                select(UserRecord).where(UserRecord.email == normalized_email)
            )
            if user is None:
                raise AuthenticationError("账号不存在，请先完成注册")
            user.is_admin = True
            session.commit()
            session.refresh(user)
            return user

    @staticmethod
    def hash_password(password: str) -> str:
        """
        使用带随机盐的 scrypt 派生密码摘要。

        Args:
            password: 明文密码。

        Returns:
            包含算法参数、盐和摘要的可验证文本。
        """
        salt = token_bytes(16)
        derived = scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )
        return f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"

    @staticmethod
    def verify_password(password: str, encoded: str) -> bool:
        """
        用常量时间比较校验明文密码。

        Args:
            password: 用户输入的明文密码。
            encoded: 数据库存储的 scrypt 摘要文本。

        Returns:
            密码匹配时返回 True，格式损坏或不匹配时返回 False。
        """
        try:
            algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$", 5)
            if algorithm != "scrypt":
                return False
            derived = scrypt(
                password.encode("utf-8"),
                salt=bytes.fromhex(salt_hex),
                n=int(n),
                r=int(r),
                p=int(p),
                dklen=len(bytes.fromhex(digest_hex)),
            )
            return compare_digest(derived.hex(), digest_hex)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def hash_token(token: str) -> str:
        """
        把不可恢复的会话或分享令牌摘要写入数据库。

        Args:
            token: 高熵原始令牌。

        Returns:
            SHA-256 十六进制摘要。
        """
        return sha256(token.encode("utf-8")).hexdigest()

    def _create_session(self, session: Session, user_id: str) -> str:
        """
        在现有事务中创建服务端会话。

        Args:
            session: 当前数据库事务会话。
            user_id: 会话所属用户 ID。

        Returns:
            只向客户端返回一次的原始令牌。
        """
        token = token_urlsafe(32)
        session.add(
            AuthSessionRecord(
                user_id=user_id,
                token_hash=self.hash_token(token),
                expires_at=(
                    datetime.now(timezone.utc)
                    + timedelta(days=self._session_ttl_days)
                ),
            )
        )
        return token

    @staticmethod
    def _claim_guest_trips(
        session: Session,
        user_id: str,
        guest_id: Optional[str],
    ) -> int:
        """
        把一个游客标识下的未删除行程原子转移给登录用户。

        Args:
            session: 当前数据库事务会话。
            user_id: 接收资产的用户 ID。
            guest_id: 当前浏览器游客标识。

        Returns:
            被认领的行程数量。
        """
        if not guest_id:
            return 0
        result = session.execute(
            update(TripRecord)
            .where(
                TripRecord.guest_id == guest_id,
                TripRecord.owner_user_id.is_(None),
                TripRecord.deleted_at.is_(None),
            )
            .values(owner_user_id=user_id, guest_id=None, updated_at=datetime.now(timezone.utc))
        )
        session.execute(
            update(TripGenerationJobRecord)
            .where(
                TripGenerationJobRecord.owner_user_id.is_(None),
                TripGenerationJobRecord.guest_id == guest_id,
            )
            .values(owner_user_id=user_id, guest_id=None)
        )
        return int(result.rowcount or 0)
