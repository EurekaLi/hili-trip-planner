"""Cloudflare Turnstile 服务端验证码校验。"""

from functools import lru_cache
from time import perf_counter
from typing import Optional
from uuid import uuid4

import httpx

from ..config import get_settings
from ..observability import record_external_call
from .resilience_service import get_circuit_breaker


class CaptchaVerificationError(ValueError):
    """表示验证码缺失、失效或与当前部署不匹配。"""


class CaptchaService:
    """按官方 Siteverify 契约验证短期、单次使用的 Turnstile Token。"""

    def __init__(
        self,
        enabled: bool,
        secret_key: str,
        verify_url: str,
        expected_hostnames: set[str],
        timeout_seconds: float = 8.0,
    ) -> None:
        """
        初始化验证码校验配置。

        Args:
            enabled: 是否对受保护端点强制校验。
            secret_key: 仅保存在后端的 Turnstile Secret。
            verify_url: Cloudflare Siteverify 地址。
            expected_hostnames: 允许签发 Token 的前端域名集合。
            timeout_seconds: 校验请求最大等待秒数。
        """
        self.enabled = enabled
        self.secret_key = secret_key
        self.verify_url = verify_url
        self.expected_hostnames = expected_hostnames
        self.timeout_seconds = timeout_seconds

    def verify(
        self,
        token: Optional[str],
        remote_ip: Optional[str],
        expected_action: str,
    ) -> None:
        """
        校验 Token 成功、action 和 hostname 后允许敏感操作继续。

        Args:
            token: 浏览器 Turnstile 小组件签发的 Token。
            remote_ip: 可选客户端 IP。
            expected_action: 当前表单配置的稳定 action。

        Raises:
            CaptchaVerificationError: Token 缺失、网络失败或结果不匹配时抛出。
        """
        if not self.enabled:
            return
        if not token or len(token) > 2048:
            raise CaptchaVerificationError("请完成人机验证")

        payload = {
            "secret": self.secret_key,
            "response": token,
            "idempotency_key": str(uuid4()),
        }
        if remote_ip:
            payload["remoteip"] = remote_ip
        breaker = get_circuit_breaker()
        breaker.before_call("turnstile")
        started_at = perf_counter()
        try:
            response = httpx.post(
                self.verify_url,
                data=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
            valid = (
                result.get("success") is True
                and result.get("action") == expected_action
                and result.get("hostname") in self.expected_hostnames
            )
            if not valid:
                breaker.record_success("turnstile")
                codes = result.get("error-codes") or ["policy-mismatch"]
                raise CaptchaVerificationError(
                    "人机验证无效或已过期: " + ",".join(map(str, codes))
                )
        except CaptchaVerificationError:
            record_external_call(
                "turnstile",
                "siteverify",
                False,
                perf_counter() - started_at,
            )
            raise
        except Exception as exc:
            breaker.record_failure("turnstile")
            record_external_call(
                "turnstile",
                "siteverify",
                False,
                perf_counter() - started_at,
            )
            raise CaptchaVerificationError("人机验证服务暂时不可用") from exc

        breaker.record_success("turnstile")
        record_external_call(
            "turnstile",
            "siteverify",
            True,
            perf_counter() - started_at,
        )


@lru_cache(maxsize=1)
def get_captcha_service() -> CaptchaService:
    """
    从部署配置创建进程级 Turnstile 校验服务。

    Returns:
        默认关闭、配置后强制校验的验证码服务。
    """
    settings = get_settings()
    hostnames = {
        value.strip()
        for value in settings.turnstile_expected_hostnames.split(",")
        if value.strip()
    }
    return CaptchaService(
        enabled=settings.turnstile_enabled,
        secret_key=settings.turnstile_secret_key,
        verify_url=settings.turnstile_verify_url,
        expected_hostnames=hostnames,
    )
