"""POI相关API路由"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from ...services.image_service import get_image_service
from ...services.resilience_service import get_guarded_amap_service

router = APIRouter(prefix="/poi", tags=["POI"])


class POIDetailResponse(BaseModel):
    """POI详情响应"""
    success: bool
    message: str
    data: Optional[dict] = None


@router.get(
    "/detail/{poi_id}",
    response_model=POIDetailResponse,
    summary="获取POI详情",
    description="根据POI ID获取详细信息,包括图片"
)
async def get_poi_detail(poi_id: str):
    """
    获取POI详情
    
    Args:
        poi_id: POI ID
        
    Returns:
        POI详情响应
    """
    try:
        amap_service = get_guarded_amap_service()
        
        # 调用高德地图POI详情API
        result = amap_service.get_poi_detail(poi_id)
        
        return POIDetailResponse(
            success=True,
            message="获取POI详情成功",
            data=result
        )
        
    except Exception as e:
        print(f"❌ 获取POI详情失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"获取POI详情失败: {str(e)}"
        )


@router.get(
    "/search",
    summary="搜索POI",
    description="根据关键词搜索POI"
)
async def search_poi(keywords: str, city: str = "北京"):
    """
    搜索POI

    Args:
        keywords: 搜索关键词
        city: 城市名称

    Returns:
        搜索结果
    """
    try:
        amap_service = get_guarded_amap_service()
        result = amap_service.search_poi(keywords, city)

        return {
            "success": True,
            "message": "搜索成功",
            "data": result
        }

    except Exception as e:
        print(f"❌ 搜索POI失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"搜索POI失败: {str(e)}"
        )


@router.get(
    "/photo",
    summary="获取景点图片",
    description=(
        "按高德POI图片、Wikimedia Commons、Unsplash和本地占位图的"
        "顺序获取景点图片"
    ),
)
def get_attraction_photo(
        name: str,
        city: str = "",
        amap_photo: Optional[str] = None,
):
    """
    获取景点图片并返回来源、缓存状态和降级原因。

    Args:
        name: 景点名称。
        city: 景点所在城市。
        amap_photo: 行程数据中已有的高德图片 URL。

    Returns:
        统一的图片结果和各提供方尝试状态。
    """
    try:
        image_service = get_image_service()
        result = image_service.get_photo(
            name=name,
            city=city,
            amap_photos=[amap_photo] if amap_photo else [],
        )
        has_external_photo = result["status"] == "success"

        return {
            "success": has_external_photo,
            "message": (
                "获取图片成功"
                if has_external_photo
                else "外部图片不可用，请使用本地占位图"
            ),
            "data": {
                "name": name,
                "photo_url": result.get("url"),
                **result,
            },
        }

    except Exception as e:
        print(f"❌ 获取景点图片失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"获取景点图片失败: {str(e)}"
        )

