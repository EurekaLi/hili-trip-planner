"""Wikimedia Commons 图片搜索服务。"""

from typing import Any, Dict, Optional

import requests


class CommonsService:
    """封装 Wikimedia Commons 搜索和上游错误分类。"""

    def __init__(self) -> None:
        """初始化 Commons API 地址和可复用 HTTP 会话。"""
        self.base_url = "https://commons.wikimedia.org/w/api.php"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "HiliAgentsTripPlanner/1.0 "
                "(educational travel planning project)"
            ),
        })

    def get_photo_result(self, name: str, city: str = "") -> Dict[str, Any]:
        """
        搜索与景点名称匹配的 Commons 图片。

        Args:
            name: 景点名称。
            city: 用于缩小搜索范围的城市名称。

        Returns:
            包含图片、署名信息和错误状态的结构化结果。
        """
        query = " ".join(part for part in (f'"{name}"', city) if part)

        try:
            response = self.session.get(
                self.base_url,
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": query,
                    "gsrnamespace": 6,
                    "gsrlimit": 6,
                    "prop": "imageinfo",
                    "iiprop": "url|mime|extmetadata",
                    "iiurlwidth": 1200,
                    "format": "json",
                    "formatversion": 2,
                    "origin": "*",
                },
                timeout=(3.05, 8),
            )
            response.raise_for_status()
            pages = (response.json().get("query") or {}).get("pages", [])

            for page in pages:
                image_info = (page.get("imageinfo") or [{}])[0]
                mime_type = str(image_info.get("mime") or "")
                if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
                    continue

                image_url = image_info.get("thumburl") or image_info.get("url")
                if not image_url:
                    continue

                metadata = image_info.get("extmetadata") or {}
                return {
                    "provider": "wikimedia_commons",
                    "status": "success",
                    "url": image_url,
                    "thumbnail_url": image_info.get("thumburl"),
                    "attribution": self._metadata_value(metadata, "Artist"),
                    "attribution_url": self._metadata_value(
                        metadata,
                        "LicenseUrl",
                    ),
                    "license": self._metadata_value(
                        metadata,
                        "LicenseShortName",
                    ),
                    "source_url": image_info.get("descriptionurl"),
                    "error_code": None,
                    "error_message": None,
                }

            return self._error_result(
                status="not_found",
                error_code="empty_results",
                error_message=f"Commons 未找到与 {query} 匹配的图片",
            )
        except requests.Timeout:
            return self._error_result(
                status="timeout",
                error_code="request_timeout",
                error_message="Commons 图片搜索超过8秒",
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
                error_message=f"Commons 返回 HTTP {status_code or '错误'}",
            )
        except ValueError:
            return self._error_result(
                status="upstream_error",
                error_code="invalid_json",
                error_message="Commons 返回了无法解析的 JSON",
            )
        except requests.RequestException as exc:
            return self._error_result(
                status="network_error",
                error_code="request_failed",
                error_message=str(exc),
            )

    @staticmethod
    def _metadata_value(metadata: Dict[str, Any], key: str) -> Optional[str]:
        """
        读取 Commons 扩展元数据中的字符串值。

        Args:
            metadata: Commons imageinfo.extmetadata 对象。
            key: 需要读取的元数据字段名。

        Returns:
            元数据字符串；字段不存在时返回 None。
        """
        value = metadata.get(key) or {}
        return value.get("value") if isinstance(value, dict) else None

    @staticmethod
    def _error_result(
            status: str,
            error_code: str,
            error_message: str,
    ) -> Dict[str, Any]:
        """
        创建统一的 Commons 错误结果。

        Args:
            status: 归一化后的错误状态。
            error_code: 便于日志与监控聚合的错误码。
            error_message: 面向开发者的错误说明。

        Returns:
            不包含图片的结构化错误结果。
        """
        return {
            "provider": "wikimedia_commons",
            "status": status,
            "url": None,
            "error_code": error_code,
            "error_message": error_message,
        }


_commons_service: Optional[CommonsService] = None


def get_commons_service() -> CommonsService:
    """
    获取进程内复用的 Commons 服务实例。

    Returns:
        Commons 服务单例。
    """
    global _commons_service

    if _commons_service is None:
        _commons_service = CommonsService()

    return _commons_service
