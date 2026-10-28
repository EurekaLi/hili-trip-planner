"""Unsplash 图片服务。"""

from typing import Any, Dict, List, Optional

import requests

from ..config import get_settings


class UnsplashService:
    """封装 Unsplash 图片搜索和上游错误分类。"""

    def __init__(self) -> None:
        """初始化访问凭据、HTTP 会话和 API 地址。"""
        settings = get_settings()
        self.access_key = settings.unsplash_access_key
        self.base_url = "https://api.unsplash.com"
        self.session = requests.Session()

    def search_photos(self, query: str, per_page: int = 5) -> List[dict]:
        """
        搜索图片并保持原有列表返回接口兼容。

        Args:
            query: 图片搜索关键词。
            per_page: 最多返回的图片数量。

        Returns:
            标准化后的 Unsplash 图片列表。
        """
        result = self._request_photos(query=query, per_page=per_page)
        return result.get("photos", [])

    def get_photo_result(self, query: str) -> Dict[str, Any]:
        """
        获取一张图片及其提供方、署名和错误状态。

        Args:
            query: 图片搜索关键词。

        Returns:
            可供图片降级链消费的结构化结果。
        """
        result = self._request_photos(query=query, per_page=1)
        photos = result.pop("photos", [])
        if result["status"] != "success" or not photos:
            return result

        photo = photos[0]
        return {
            **result,
            "url": photo.get("url"),
            "thumbnail_url": photo.get("thumb"),
            "attribution": photo.get("photographer"),
            "attribution_url": photo.get("photographer_url"),
            "source_url": photo.get("source_url"),
        }

    def get_photo_url(self, query: str) -> Optional[str]:
        """
        获取单张图片 URL，并保持原有调用方式兼容。

        Args:
            query: 图片搜索关键词。

        Returns:
            成功时返回图片 URL，否则返回 None。
        """
        result = self.get_photo_result(query)
        return result.get("url") if result["status"] == "success" else None

    def _request_photos(self, query: str, per_page: int) -> Dict[str, Any]:
        """
        调用 Unsplash 搜索接口并分类网络与 HTTP 错误。

        Args:
            query: 图片搜索关键词。
            per_page: 最多请求的图片数量。

        Returns:
            包含状态、图片列表和限额信息的结构化结果。
        """
        if not self.access_key:
            return self._error_result(
                status="config_error",
                error_code="missing_access_key",
                error_message="UNSPLASH_ACCESS_KEY 未配置",
            )

        try:
            response = self.session.get(
                f"{self.base_url}/search/photos",
                params={
                    "query": query,
                    "per_page": max(1, min(per_page, 30)),
                    "orientation": "landscape",
                    "content_filter": "high",
                },
                headers={
                    "Authorization": f"Client-ID {self.access_key}",
                    "Accept-Version": "v1",
                },
                timeout=(3.05, 8),
            )

            rate_limit = response.headers.get("X-Ratelimit-Limit")
            rate_remaining = response.headers.get("X-Ratelimit-Remaining")
            if response.status_code == 429:
                return self._error_result(
                    status="rate_limited",
                    error_code="http_429",
                    error_message="Unsplash API 调用频率已达到上限",
                    rate_limit=rate_limit,
                    rate_remaining=rate_remaining,
                )

            response.raise_for_status()
            payload = response.json()
            photos = []
            for photo in payload.get("results", []):
                user = photo.get("user") or {}
                links = photo.get("links") or {}
                photos.append({
                    "id": photo.get("id"),
                    "url": (photo.get("urls") or {}).get("regular"),
                    "thumb": (photo.get("urls") or {}).get("thumb"),
                    "description": (
                        photo.get("description")
                        or photo.get("alt_description")
                    ),
                    "photographer": user.get("name"),
                    "photographer_url": (user.get("links") or {}).get("html"),
                    "source_url": links.get("html"),
                })

            if not photos:
                return self._error_result(
                    status="not_found",
                    error_code="empty_results",
                    error_message=f"Unsplash 未找到与 {query} 匹配的图片",
                    rate_limit=rate_limit,
                    rate_remaining=rate_remaining,
                )

            return {
                "provider": "unsplash",
                "status": "success",
                "photos": photos,
                "error_code": None,
                "error_message": None,
                "rate_limit": rate_limit,
                "rate_remaining": rate_remaining,
            }
        except requests.Timeout:
            return self._error_result(
                status="timeout",
                error_code="request_timeout",
                error_message="Unsplash 图片搜索超过8秒",
            )
        except requests.HTTPError as exc:
            status_code = (
                exc.response.status_code
                if exc.response is not None
                else None
            )
            return self._error_result(
                status="upstream_error",
                error_code=f"http_{status_code or 'unknown'}",
                error_message=f"Unsplash 返回 HTTP {status_code or '错误'}",
            )
        except ValueError:
            return self._error_result(
                status="upstream_error",
                error_code="invalid_json",
                error_message="Unsplash 返回了无法解析的 JSON",
            )
        except requests.RequestException as exc:
            return self._error_result(
                status="network_error",
                error_code="request_failed",
                error_message=str(exc),
            )

    @staticmethod
    def _error_result(
            status: str,
            error_code: str,
            error_message: str,
            rate_limit: Optional[str] = None,
            rate_remaining: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        创建统一的 Unsplash 错误结果。

        Args:
            status: 归一化后的错误状态。
            error_code: 便于日志与监控聚合的错误码。
            error_message: 面向开发者的错误说明。
            rate_limit: Unsplash 返回的总调用限额。
            rate_remaining: Unsplash 返回的剩余调用次数。

        Returns:
            不包含图片的结构化错误结果。
        """
        return {
            "provider": "unsplash",
            "status": status,
            "photos": [],
            "url": None,
            "error_code": error_code,
            "error_message": error_message,
            "rate_limit": rate_limit,
            "rate_remaining": rate_remaining,
        }


_unsplash_service: Optional[UnsplashService] = None


def get_unsplash_service() -> UnsplashService:
    """
    获取进程内复用的 Unsplash 服务实例。

    Returns:
        Unsplash 服务单例。
    """
    global _unsplash_service

    if _unsplash_service is None:
        _unsplash_service = UnsplashService()

    return _unsplash_service
