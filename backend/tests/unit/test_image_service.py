"""景点图片提供链、缓存和错误分类测试。"""

from typing import Any, Dict

from app.services.image_service import ImageService


class FakePhotoProvider:
    """按固定结果响应并记录调用次数的测试图片提供方。"""

    def __init__(self, result: Dict[str, Any]) -> None:
        """
        初始化固定返回结果和调用计数。

        Args:
            result: 每次查询需要返回的结构化结果。
        """
        self.result = result
        self.call_count = 0

    def get_photo_result(self, *args: str) -> Dict[str, Any]:
        """
        返回固定图片结果并累计调用次数。

        Args:
            *args: 图片服务传入的景点名称或查询词。

        Returns:
            初始化时提供的结构化结果副本。
        """
        self.call_count += 1
        return dict(self.result)


def error_result(provider: str, status: str) -> Dict[str, Any]:
    """
    构造图片提供方错误结果。

    Args:
        provider: 图片提供方标识。
        status: 归一化错误状态。

    Returns:
        不包含图片 URL 的结构化错误结果。
    """
    return {
        "provider": provider,
        "status": status,
        "url": None,
        "error_code": status,
        "error_message": f"{provider} {status}",
    }


def success_result(provider: str, url: str) -> Dict[str, Any]:
    """
    构造图片提供方成功结果。

    Args:
        provider: 图片提供方标识。
        url: 测试图片 URL。

    Returns:
        包含图片 URL 的结构化成功结果。
    """
    return {
        "provider": provider,
        "status": "success",
        "url": url,
        "error_code": None,
        "error_message": None,
    }


def test_amap_photo_skips_all_external_providers():
    """验证已有高德图片时不会调用 Commons 或 Unsplash。"""
    commons = FakePhotoProvider(error_result("wikimedia_commons", "not_found"))
    unsplash = FakePhotoProvider(error_result("unsplash", "not_found"))
    service = ImageService(commons_service=commons, unsplash_service=unsplash)

    result = service.get_photo(
        name="故宫博物院",
        city="北京",
        amap_photos=[{"url": "https://amap.example/forbidden-city.jpg"}],
    )

    assert result["provider"] == "amap"
    assert result["url"] == "https://amap.example/forbidden-city.jpg"
    assert commons.call_count == 0
    assert unsplash.call_count == 0


def test_commons_success_is_cached():
    """验证 Commons 成功后跳过 Unsplash，重复查询命中缓存。"""
    commons = FakePhotoProvider(
        success_result("wikimedia_commons", "https://commons.example/palace.jpg"),
    )
    unsplash = FakePhotoProvider(error_result("unsplash", "not_found"))
    service = ImageService(commons_service=commons, unsplash_service=unsplash)

    first = service.get_photo("故宫博物院", "北京")
    second = service.get_photo("故宫博物院", "北京")

    assert first["provider"] == "wikimedia_commons"
    assert first["cached"] is False
    assert second["cached"] is True
    assert commons.call_count == 1
    assert unsplash.call_count == 0


def test_unsplash_is_used_after_commons_miss():
    """验证 Commons 无结果后会继续使用 Unsplash。"""
    commons = FakePhotoProvider(error_result("wikimedia_commons", "not_found"))
    unsplash = FakePhotoProvider(
        success_result("unsplash", "https://images.example/hutong.jpg"),
    )
    service = ImageService(commons_service=commons, unsplash_service=unsplash)

    result = service.get_photo("什刹海", "北京")

    assert result["provider"] == "unsplash"
    assert [attempt["provider"] for attempt in result["attempts"]] == [
        "wikimedia_commons",
        "unsplash",
    ]


def test_all_failures_return_classified_local_fallback():
    """验证所有外部来源失败后返回带诊断信息的本地降级结果。"""
    commons = FakePhotoProvider(error_result("wikimedia_commons", "timeout"))
    unsplash = FakePhotoProvider(error_result("unsplash", "rate_limited"))
    service = ImageService(commons_service=commons, unsplash_service=unsplash)

    result = service.get_photo("测试景点", "北京")

    assert result["provider"] == "local"
    assert result["status"] == "fallback"
    assert result["error_code"] == "all_providers_failed"
    assert [attempt["status"] for attempt in result["attempts"]] == [
        "timeout",
        "rate_limited",
    ]
