"""景点图片聚合、缓存和降级服务。"""

import copy
import threading
import time
from typing import Any, Dict, List, Optional

from ..config import get_settings
from ..observability import IMAGE_CACHE_LOOKUPS, record_external_call

from .commons_service import CommonsService, get_commons_service
from .resilience_service import CircuitOpenError, get_circuit_breaker
from .unsplash_service import UnsplashService, get_unsplash_service


class ImageService:
    """按高德、Commons、Unsplash 顺序提供景点图片。"""

    def __init__(
            self,
            commons_service: Optional[CommonsService] = None,
            unsplash_service: Optional[UnsplashService] = None,
            cache_ttl_seconds: int = 24 * 60 * 60,
            fallback_ttl_seconds: int = 10 * 60,
            max_concurrency: int = 3,
            provider_call_budget: int = 2,
            circuit_breaker: Optional[Any] = None,
    ) -> None:
        """
        初始化图片提供方、TTL 缓存和并发限制。

        Args:
            commons_service: 可替换的 Commons 服务，便于测试。
            unsplash_service: 可替换的 Unsplash 服务，便于测试。
            cache_ttl_seconds: 成功结果的缓存秒数。
            fallback_ttl_seconds: 全部失败结果的缓存秒数。
            max_concurrency: 允许同时执行的外部图片查询数量。
            provider_call_budget: 单个缺图请求最多尝试的外部提供方数。
            circuit_breaker: 可选的共享提供方熔断器。
        """
        self.commons_service = commons_service or get_commons_service()
        self.unsplash_service = unsplash_service or get_unsplash_service()
        self.cache_ttl_seconds = max(1, cache_ttl_seconds)
        self.fallback_ttl_seconds = max(1, fallback_ttl_seconds)
        self.provider_call_budget = max(1, min(provider_call_budget, 2))
        self.circuit_breaker = circuit_breaker
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        self._request_slots = threading.BoundedSemaphore(max(1, max_concurrency))

    def get_photo(
            self,
            name: str,
            city: str = "",
            amap_photos: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """
        按优先级获取景点图片并返回每次降级尝试的状态。

        高德图片已经包含在行程数据中，因此始终优先使用且不会产生
        Commons 或 Unsplash 请求；其余来源使用进程内 TTL 缓存。

        Args:
            name: 景点名称。
            city: 景点所在城市。
            amap_photos: 高德 POI 已返回的图片列表。

        Returns:
            包含最终 URL、来源、缓存状态和错误分类的结构化结果。
        """
        amap_url = self._select_amap_photo(amap_photos or [])
        if amap_url:
            IMAGE_CACHE_LOOKUPS.labels("bypass").inc()
            return {
                "provider": "amap",
                "status": "success",
                "url": amap_url,
                "cached": False,
                "attribution": "高德地图",
                "attribution_url": None,
                "source_url": None,
                "license": None,
                "attempts": [],
                "error_code": None,
                "error_message": None,
            }

        cache_key = self._build_cache_key(name=name, city=city)
        cached_result = self._get_cached(cache_key)
        if cached_result is not None:
            IMAGE_CACHE_LOOKUPS.labels("hit").inc()
            cached_result["cached"] = True
            return cached_result
        IMAGE_CACHE_LOOKUPS.labels("miss").inc()

        with self._request_slots:
            cached_result = self._get_cached(cache_key)
            if cached_result is not None:
                IMAGE_CACHE_LOOKUPS.labels("hit_after_wait").inc()
                cached_result["cached"] = True
                return cached_result

            attempts: List[Dict[str, Any]] = []
            commons_result = self._call_provider(
                provider="wikimedia",
                operation="photo_search",
                callback=self.commons_service.get_photo_result,
                args=(name, city),
            )
            attempts.append(self._summarize_attempt(commons_result))
            if commons_result.get("status") == "success":
                result = self._success_result(commons_result, attempts)
                self._set_cached(cache_key, result, self.cache_ttl_seconds)
                return result

            unsplash_query = " ".join(
                part for part in (name, city, "China landmark") if part
            )
            if self.provider_call_budget < 2:
                unsplash_result = {
                    "provider": "unsplash",
                    "status": "budget_exhausted",
                    "error_code": "provider_budget_exhausted",
                    "error_message": "图片提供方调用预算已用尽",
                }
            else:
                unsplash_result = self._call_provider(
                    provider="unsplash",
                    operation="photo_search",
                    callback=self.unsplash_service.get_photo_result,
                    args=(unsplash_query,),
                )
            attempts.append(self._summarize_attempt(unsplash_result))
            if unsplash_result.get("status") == "success":
                result = self._success_result(unsplash_result, attempts)
                self._set_cached(cache_key, result, self.cache_ttl_seconds)
                return result

            result = {
                "provider": "local",
                "status": "fallback",
                "url": None,
                "cached": False,
                "attribution": None,
                "attribution_url": None,
                "source_url": None,
                "license": None,
                "attempts": attempts,
                "error_code": "all_providers_failed",
                "error_message": "外部图片源均不可用，使用本地占位图",
            }
            self._set_cached(cache_key, result, self.fallback_ttl_seconds)
            return result

    def _call_provider(
            self,
            provider: str,
            operation: str,
            callback,
            args: tuple = (),
            **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        在熔断和指标保护下调用一个外部图片提供方。

        Args:
            provider: 有界提供方名称。
            operation: 有界操作名称。
            callback: 提供方结构化查询函数。
            args: 传递给查询函数的位置参数。
            **kwargs: 传递给查询函数的关键字参数。

        Returns:
            提供方结果；熔断时返回可进入下一降级层的失败结构。
        """
        started_at = time.perf_counter()
        try:
            if self.circuit_breaker is not None:
                self.circuit_breaker.before_call(provider)
            result = callback(*args, **kwargs)
            succeeded = result.get("status") == "success"
            provider_failed = result.get("status") in {
                "error",
                "timeout",
                "rate_limited",
                "unavailable",
            }
            if self.circuit_breaker is not None:
                if succeeded:
                    self.circuit_breaker.record_success(provider)
                elif provider_failed:
                    self.circuit_breaker.record_failure(provider)
            record_external_call(
                provider,
                operation,
                succeeded,
                time.perf_counter() - started_at,
            )
            return result
        except CircuitOpenError as exc:
            return {
                "provider": provider,
                "status": "circuit_open",
                "error_code": "provider_circuit_open",
                "error_message": str(exc),
            }
        except Exception as exc:
            if self.circuit_breaker is not None:
                self.circuit_breaker.record_failure(provider)
            record_external_call(
                provider,
                operation,
                False,
                time.perf_counter() - started_at,
            )
            return {
                "provider": provider,
                "status": "error",
                "error_code": type(exc).__name__,
                "error_message": str(exc),
            }

    @staticmethod
    def _build_cache_key(name: str, city: str) -> str:
        """
        创建忽略首尾空格和大小写的图片缓存键。

        Args:
            name: 景点名称。
            city: 景点所在城市。

        Returns:
            可用于缓存字典的稳定键。
        """
        return f"{city.strip()}|{name.strip()}".casefold()

    @staticmethod
    def _select_amap_photo(photos: List[Any]) -> Optional[str]:
        """
        从高德字符串或对象图片列表中选择首个可用 URL。

        Args:
            photos: 高德 POI 返回的图片列表。

        Returns:
            首个有效图片 URL；不存在时返回 None。
        """
        for photo in photos:
            if isinstance(photo, str) and photo.strip():
                return photo.strip()
            if isinstance(photo, dict):
                url = photo.get("url")
                if isinstance(url, str) and url.strip():
                    return url.strip()
        return None

    @staticmethod
    def _summarize_attempt(result: Dict[str, Any]) -> Dict[str, Any]:
        """
        提取图片提供方尝试中适合返回给调用方的诊断字段。

        Args:
            result: 单个图片提供方的结构化结果。

        Returns:
            不含冗余图片数据的诊断摘要。
        """
        return {
            "provider": result.get("provider"),
            "status": result.get("status"),
            "error_code": result.get("error_code"),
            "error_message": result.get("error_message"),
        }

    @staticmethod
    def _success_result(
            provider_result: Dict[str, Any],
            attempts: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        将提供方成功结果转换为图片聚合服务的统一结构。

        Args:
            provider_result: Commons 或 Unsplash 的成功结果。
            attempts: 截止成功时已经执行的提供方尝试。

        Returns:
            统一的成功图片结果。
        """
        return {
            "provider": provider_result.get("provider"),
            "status": "success",
            "url": provider_result.get("url"),
            "cached": False,
            "attribution": provider_result.get("attribution"),
            "attribution_url": provider_result.get("attribution_url"),
            "source_url": provider_result.get("source_url"),
            "license": provider_result.get("license"),
            "attempts": copy.deepcopy(attempts),
            "error_code": None,
            "error_message": None,
        }

    def _get_cached(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """
        读取尚未过期的缓存结果并清理过期记录。

        Args:
            cache_key: 由城市和景点名称构成的缓存键。

        Returns:
            缓存结果副本；未命中或已过期时返回 None。
        """
        now = time.monotonic()
        with self._cache_lock:
            entry = self._cache.get(cache_key)
            if entry is None:
                return None
            if entry["expires_at"] <= now:
                self._cache.pop(cache_key, None)
                return None
            return copy.deepcopy(entry["result"])

    def _set_cached(
            self,
            cache_key: str,
            result: Dict[str, Any],
            ttl_seconds: int,
    ) -> None:
        """
        将图片结果按指定 TTL 写入进程内缓存。

        Args:
            cache_key: 由城市和景点名称构成的缓存键。
            result: 需要缓存的统一图片结果。
            ttl_seconds: 缓存有效秒数。
        """
        with self._cache_lock:
            self._cache[cache_key] = {
                "expires_at": time.monotonic() + ttl_seconds,
                "result": copy.deepcopy(result),
            }


_image_service: Optional[ImageService] = None


def get_image_service() -> ImageService:
    """
    获取进程内复用的图片聚合服务实例。

    Returns:
        图片聚合服务单例。
    """
    global _image_service

    if _image_service is None:
        _image_service = ImageService(
            provider_call_budget=get_settings().image_provider_call_budget,
            circuit_breaker=get_circuit_breaker(),
        )

    return _image_service
