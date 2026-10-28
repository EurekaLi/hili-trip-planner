"""配置管理模块"""

import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

backend_env = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(backend_env, override=False)

class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用基本配置
    app_name: str = "HiliAgents智能旅行助手"
    app_version: str = "1.0.0"
    debug: bool = False

    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8000

    # CORS配置 - 使用字符串,在代码中分割
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000"

    # 高德地图API配置
    amap_api_key: str = ""

    # Unsplash API配置
    unsplash_access_key: str = ""
    unsplash_secret_key: str = ""

    # LLM配置 (从环境变量读取,由 HiliAgents 管理)
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4"

    # 日志配置
    log_level: str = "INFO"
    environment: str = "development"
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0
    metrics_bearer_token: str = ""

    # API 安全、限流与隐私配置
    request_max_body_bytes: int = 1024 * 1024
    rate_limit_enabled: bool = True
    rate_limit_fail_open: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_trip_generation: int = 5
    rate_limit_authentication: int = 10
    rate_limit_feedback: int = 20
    rate_limit_poi_lookup: int = 60
    privacy_contact_email: str = ""
    data_retention_days: int = 90
    turnstile_enabled: bool = False
    turnstile_site_key: str = ""
    turnstile_secret_key: str = ""
    turnstile_expected_hostnames: str = ""
    turnstile_verify_url: str = (
        "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    )

    # Redis 与异步旅行规划任务配置
    redis_url: str = "redis://127.0.0.1:6380/0"
    trip_job_key_prefix: str = "helloagents:trip-planner"
    trip_job_ttl_seconds: int = 86400
    trip_job_event_poll_seconds: float = 0.5
    trip_job_soft_time_limit_seconds: int = 300
    trip_job_time_limit_seconds: int = 360
    trip_job_visibility_timeout_seconds: int = 420
    trip_job_max_attempts: int = 2
    llm_input_cost_per_million_tokens_cny: float = 0
    llm_output_cost_per_million_tokens_cny: float = 0
    itinerary_route_call_budget: int = 18
    itinerary_cross_region_minutes: int = 75
    generation_llm_call_budget: int = 4
    generation_external_call_budget: int = 40
    generation_cost_budget_cny: float = 0
    circuit_failure_threshold: int = 3
    circuit_recovery_seconds: int = 60
    image_provider_call_budget: int = 2

    # SQL 持久化与账号会话配置
    database_url: str = "sqlite:///./data/trip_planner.db"
    database_auto_create: bool = False
    auth_session_ttl_days: int = 30
    auth_cookie_secure: bool = False

    def get_cors_origins_list(self) -> List[str]:
        """获取CORS origins列表"""
        return [origin.strip() for origin in self.cors_origins.split(',')]


# 创建全局配置实例
settings = Settings()


def get_settings() -> Settings:
    """获取配置实例"""
    return settings


# 验证必要的配置
def validate_config():
    """验证配置是否完整"""
    errors = []
    warnings = []

    if not settings.amap_api_key:
        errors.append("AMAP_API_KEY未配置")

    if not 1 <= settings.trip_job_max_attempts <= 5:
        errors.append("TRIP_JOB_MAX_ATTEMPTS必须在1到5之间")

    if settings.trip_job_soft_time_limit_seconds >= settings.trip_job_time_limit_seconds:
        errors.append("任务软超时时间必须小于硬超时时间")

    if settings.trip_job_visibility_timeout_seconds <= settings.trip_job_time_limit_seconds:
        errors.append("TRIP_JOB_VISIBILITY_TIMEOUT_SECONDS必须大于任务硬超时时间")

    if (
        settings.llm_input_cost_per_million_tokens_cny < 0
        or settings.llm_output_cost_per_million_tokens_cny < 0
    ):
        errors.append("LLM token单价不能为负数")

    if not 1 <= settings.itinerary_route_call_budget <= 60:
        errors.append("ITINERARY_ROUTE_CALL_BUDGET必须在1到60之间")

    if not 15 <= settings.itinerary_cross_region_minutes <= 240:
        errors.append("ITINERARY_CROSS_REGION_MINUTES必须在15到240之间")

    if not 1 <= settings.generation_llm_call_budget <= 12:
        errors.append("GENERATION_LLM_CALL_BUDGET必须在1到12之间")

    if not 4 <= settings.generation_external_call_budget <= 200:
        errors.append("GENERATION_EXTERNAL_CALL_BUDGET必须在4到200之间")

    if settings.generation_cost_budget_cny < 0:
        errors.append("GENERATION_COST_BUDGET_CNY不能为负数")

    if not 1 <= settings.circuit_failure_threshold <= 20:
        errors.append("CIRCUIT_FAILURE_THRESHOLD必须在1到20之间")

    if not 10 <= settings.circuit_recovery_seconds <= 3600:
        errors.append("CIRCUIT_RECOVERY_SECONDS必须在10到3600之间")

    if not 1 <= settings.image_provider_call_budget <= 2:
        errors.append("IMAGE_PROVIDER_CALL_BUDGET必须在1到2之间")

    if not 1024 <= settings.request_max_body_bytes <= 10 * 1024 * 1024:
        errors.append("REQUEST_MAX_BODY_BYTES必须在1KB到10MB之间")

    rate_limits = (
        settings.rate_limit_trip_generation,
        settings.rate_limit_authentication,
        settings.rate_limit_feedback,
        settings.rate_limit_poi_lookup,
    )
    if any(limit < 1 for limit in rate_limits):
        errors.append("所有RATE_LIMIT配置必须大于0")

    if not 1 <= settings.rate_limit_window_seconds <= 3600:
        errors.append("RATE_LIMIT_WINDOW_SECONDS必须在1到3600之间")

    if not 0 <= settings.sentry_traces_sample_rate <= 1:
        errors.append("SENTRY_TRACES_SAMPLE_RATE必须在0到1之间")

    if not 1 <= settings.data_retention_days <= 3650:
        errors.append("DATA_RETENTION_DAYS必须在1到3650之间")

    if settings.turnstile_enabled and not (
        settings.turnstile_site_key
        and settings.turnstile_secret_key
        and settings.turnstile_expected_hostnames.strip()
    ):
        errors.append(
            "启用TURNSTILE时必须配置站点Key、Secret和允许的Hostname"
        )

    if not 1 <= settings.auth_session_ttl_days <= 365:
        errors.append("AUTH_SESSION_TTL_DAYS必须在1到365之间")

    llm_api_key = os.getenv("DASHSCOPE_API_KEY")
    if not llm_api_key:
        warnings.append("DASHSCOPE_API_KEY未配置,旅行规划功能将无法使用")

    if errors:
        error_msg = "配置错误:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValueError(error_msg)

    if warnings:
        print("\n⚠️  配置警告:")
        for w in warnings:
            print(f"  - {w}")

    return True


# 打印配置信息(用于调试)
def print_config():
    """打印当前配置(隐藏敏感信息)"""
    print(f"应用名称: {settings.app_name}")
    print(f"版本: {settings.app_version}")
    print(f"服务器: {settings.host}:{settings.port}")
    print(f"高德地图API Key: {'已配置' if settings.amap_api_key else '未配置'}")

    # 检查LLM配置
    llm_api_key = os.getenv("DASHSCOPE_API_KEY")
    llm_base_url = os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    llm_model = os.getenv("DASHSCOPE_MODEL_ID", "qwen3.6-plus")

    print(f"LLM API Key: {'已配置' if llm_api_key else '未配置'}")
    print(f"LLM Base URL: {llm_base_url}")
    print(f"LLM Model: {llm_model}")
    print(f"日志级别: {settings.log_level}")

