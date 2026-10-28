"""高德地图MCP服务封装"""

from contextvars import ContextVar, Token
from datetime import datetime, timezone
import os
import sys
from threading import Lock
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple

from hili_agents.tools import MCPTool
from ..config import get_settings
from ..models.schemas import (
    ExternalCallMetric,
    FactQuality,
    FactSource,
    Location,
    POIInfo,
    WeatherInfo,
)
from .itinerary_optimizer import build_visit_constraints

# 全局MCP工具实例
_amap_mcp_tool = None


def get_amap_mcp_tool() -> MCPTool:
    """
    获取高德地图MCP工具实例(单例模式)
    
    Returns:
        MCPTool实例
    """
    global _amap_mcp_tool
    
    if _amap_mcp_tool is None:
        settings = get_settings()
        
        if not settings.amap_api_key:
            raise ValueError("高德地图API Key未配置,请在.env文件中设置AMAP_API_KEY")

        from pathlib import Path

        backend_dir = Path(__file__).resolve().parents[2]
        amap_mcp_dir = backend_dir / "vendor" / "amap-mcp-server"
        amap_server_script = amap_mcp_dir / "amap_mcp_server" / "server.py"
        inherited_pythonpath = os.getenv("PYTHONPATH", "")
        server_pythonpath = os.pathsep.join(
            path
            for path in (str(amap_mcp_dir), inherited_pythonpath)
            if path
        )

        _amap_mcp_tool = MCPTool(
            name="amap",
            description="高德地图服务，支持POI、天气和路线规划",
            server_command=[
                sys.executable,
                str(amap_server_script),
            ],
            env={
                "AMAP_MAPS_API_KEY": settings.amap_api_key,
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": server_pythonpath,
            },
            auto_expand=True,
            operation_timeout=20,
        )
        
        print(f"✅ 高德地图MCP工具初始化成功")
        print(f"   工具数量: {len(_amap_mcp_tool._available_tools)}")

        
        # 打印可用工具列表
        if _amap_mcp_tool._available_tools:
            print("   可用工具:")
            for tool in _amap_mcp_tool._available_tools[:5]:  # 只打印前5个
                print(f"     - {tool.get('name', 'unknown')}")
            if len(_amap_mcp_tool._available_tools) > 5:
                print(f"     ... 还有 {len(_amap_mcp_tool._available_tools) - 5} 个工具")
    
    return _amap_mcp_tool


class AmapService:
    """高德地图服务封装类"""

    def __init__(self):


        """初始化服务"""
        self.mcp_tool = get_amap_mcp_tool()
        self._call_trace: ContextVar[Optional[List[ExternalCallMetric]]] = (
            ContextVar("amap_call_trace", default=None)
        )
        self._call_counts: Dict[str, int] = {}
        self._call_count_lock = Lock()

    def begin_call_trace(
            self,
    ) -> Tuple[List[ExternalCallMetric], Token]:
        """
        为当前旅行规划上下文创建独立的高德调用记录列表。

        Returns:
            当前上下文的可变指标列表和用于恢复上下文的令牌。
        """
        metrics: List[ExternalCallMetric] = []
        token = self._call_trace.set(metrics)
        return metrics, token

    def end_call_trace(self, token: Token) -> None:
        """
        恢复调用方进入高德调用跟踪前的上下文。

        Args:
            token: `begin_call_trace` 返回的上下文令牌。
        """
        self._call_trace.reset(token)

    def _record_call_metric(self, metric: ExternalCallMetric) -> int:
        """
        保存请求级指标并递增进程内操作调用计数。

        Args:
            metric: 已完成的单次高德 MCP 调用指标。

        Returns:
            当前进程内该操作累计调用次数。
        """
        trace = self._call_trace.get()
        if trace is not None:
            trace.append(metric)

        with self._call_count_lock:
            count = self._call_counts.get(metric.operation, 0) + metric.units
            self._call_counts[metric.operation] = count
            return count

    @staticmethod
    def _build_fact_source(note: str) -> FactSource:
        """
        为一次成功解析的高德结果创建统一来源信息。

        Args:
            note: 说明当前结果对应的高德数据类型。

        Returns:
            带当前UTC抓取时间的已验证来源。
        """
        return FactSource(
            provider="amap",
            quality=FactQuality.VERIFIED,
            retrieved_at=datetime.now(timezone.utc),
            note=note,
        )

    def search_poi(
            self,
            keywords: str,
            city: str,
            citylimit: bool = True,
            limit: int = 6,
    ) -> List[POIInfo]:
        """
        搜索并补全指定数量的 POI。

        Args:
            keywords: POI 搜索关键词。
            city: 搜索城市。
            citylimit: 是否限制在指定城市内。
            limit: 最多补全的 POI 数量。

        Returns:
            包含真实地址和坐标的 POI 列表。
        """
        try:
            safe_limit = max(1, min(limit, 20))
            data = self._call_tool("maps_text_search", {
                "keywords": keywords,
                "city": city,
                "citylimit": str(citylimit).lower(),
                "offset": safe_limit,
                "extensions": "all",
            })

            if not data:
                return []

            # 高德常见返回: {"pois": [...]}
            # 有些 MCP 可能直接返回 list
            pois = []
            if isinstance(data, dict):
                pois = data.get("pois") or data.get("data") or data.get("results") or []
            elif isinstance(data, list):
                pois = data

            result: List[POIInfo] = []

            for item in pois[:safe_limit]:
                if not isinstance(item, dict):
                    continue

                poi_id = str(item.get("id") or "")
                if not poi_id:
                    continue

                # 扩展搜索通常已经包含位置和照片；旧版 MCP 返回字段不足时
                # 再调用详情接口，避免为每个候选重复产生一次计费调用。
                detail = item
                location = self._parse_location(item.get("location"))
                if not location:
                    detail_result = self._call_tool(
                        "maps_search_detail",
                        {"id": poi_id}
                    )
                    if not isinstance(detail_result, dict):
                        continue
                    detail = {**item, **detail_result}
                    location = self._parse_location(detail.get("location"))

                if not location:
                    print(f"跳过无坐标 POI: {item.get('name')}, detail={detail}")
                    continue

                biz_ext = detail.get("biz_ext") or {}
                if not isinstance(biz_ext, dict):
                    biz_ext = {}
                rating = self._parse_optional_float(
                    detail.get("rating") or biz_ext.get("rating")
                )
                raw_opening_hours = self._parse_optional_text(
                    detail.get("opentime2")
                    or detail.get("opentime")
                    or detail.get("open_time")
                    or biz_ext.get("opentime2")
                    or biz_ext.get("opentime")
                )
                source = self._build_fact_source("高德POI搜索结果")

                result.append(
                    POIInfo(
                        id=poi_id,
                        name=str(detail.get("name") or item.get("name") or ""),
                        type=str(
                            detail.get("type")
                            or item.get("type")
                            or item.get("typecode")
                            or ""
                        ),
                        address=str(
                            detail.get("address")
                            or item.get("address")
                            or ""
                        ),
                        location=location,
                        tel=self._parse_optional_text(detail.get("tel")),
                        rating=rating,
                        photos=self._parse_photo_urls(detail.get("photos")),
                        source=source,
                        visit_constraints=build_visit_constraints(
                            name=str(detail.get("name") or item.get("name") or ""),
                            category=str(
                                detail.get("type")
                                or item.get("type")
                                or item.get("typecode")
                                or ""
                            ),
                            raw_opening_hours=raw_opening_hours,
                            verified_source=(source if raw_opening_hours else None),
                        ),
                    )
                )

            return result

        except Exception as e:
            print(f"❌ POI搜索失败: {str(e)}")
            return []

    def get_weather(self, city: str) -> List[WeatherInfo]:
        try:
            data = self._call_tool("maps_weather", {
                "city": city
            })

            if not data:
                return []

            weather_items = []

            if isinstance(data, dict):
                # 高德天气 forecast 常见结构
                forecasts = data.get("forecasts") or []

                if forecasts and isinstance(forecasts, list):
                    first_item = forecasts[0]

                    # 原始高德接口：
                    # {"forecasts": [{"city": "...", "casts": [...]}]}
                    if isinstance(first_item, dict) and "casts" in first_item:
                        weather_items.extend(first_item.get("casts") or [])

                    # amap-mcp-server：
                    # {"city": "...", "forecasts": [...]}
                    else:
                        weather_items.extend(forecasts)

                # 高德天气 live 常见结构
                lives = data.get("lives") or []
                for live in lives:
                    weather_items.append({
                        "date": live.get("reporttime", "")[:10],
                        "dayweather": live.get("weather", ""),
                        "nightweather": live.get("weather", ""),
                        "daytemp": live.get("temperature", 0),
                        "nighttemp": live.get("temperature", 0),
                        "daywind": live.get("winddirection", ""),
                        "daypower": live.get("windpower", "")
                    })

            elif isinstance(data, list):
                weather_items = data

            result: List[WeatherInfo] = []

            for item in weather_items:
                if not isinstance(item, dict):
                    continue

                result.append(WeatherInfo(
                    date=str(item.get("date") or ""),
                    day_weather=str(item.get("dayweather") or item.get("day_weather") or ""),
                    night_weather=str(item.get("nightweather") or item.get("night_weather") or ""),
                    day_temp=item.get("daytemp") or item.get("day_temp") or 0,
                    night_temp=item.get("nighttemp") or item.get("night_temp") or 0,
                    wind_direction=str(item.get("daywind") or item.get("wind_direction") or ""),
                    wind_power=str(item.get("daypower") or item.get("wind_power") or ""),
                    source=self._build_fact_source("高德天气预报"),
                ))

            return result

        except Exception as e:
            print(f"❌ 天气查询失败: {str(e)}")
            return []
    
    def plan_route(
        self,
        origin_address: str,
        destination_address: str,
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
        route_type: str = "walking"
    ) -> Dict[str, Any]:
        """
        规划路线
        
        Args:
            origin_address: 起点地址
            destination_address: 终点地址
            origin_city: 起点城市
            destination_city: 终点城市
            route_type: 路线类型 (walking/driving/transit)
            
        Returns:
            路线信息
        """
        try:
            # 根据路线类型选择工具
            tool_map = {
                "walking": "maps_direction_walking_by_address",
                "driving": "maps_direction_driving_by_address",
                "transit": "maps_direction_transit_integrated_by_address"
            }
            
            tool_name = tool_map.get(route_type, "maps_direction_walking_by_address")
            
            # 构建参数
            arguments = {
                "origin_address": origin_address,
                "destination_address": destination_address
            }
            
            # 公共交通需要城市参数
            if route_type == "transit":
                if origin_city:
                    arguments["origin_city"] = origin_city
                if destination_city:
                    arguments["destination_city"] = destination_city
            else:
                # 其他路线类型也可以提供城市参数提高准确性
                if origin_city:
                    arguments["origin_city"] = origin_city
                if destination_city:
                    arguments["destination_city"] = destination_city
            
            # 调用MCP工具
            data = self._call_tool(tool_name, arguments)

            route_info = self._parse_route_result(data, route_type)
            if route_info:
                route_info["source"] = self._build_fact_source(
                    "高德地址路线规划"
                ).model_dump()
            return route_info
            
        except Exception as e:
            print(f"❌ 路线规划失败: {str(e)}")
            return {}

    def plan_route_by_coordinates(
            self,
            origin: Location,
            destination: Location,
            route_type: str = "walking",
            origin_city: Optional[str] = None,
            destination_city: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        根据真实经纬度规划两点之间的路线。

        Args:
            origin: 起点经纬度。
            destination: 终点经纬度。
            route_type: 路线类型，支持walking、driving和transit。
            origin_city: 起点城市，公交路线必填。
            destination_city: 终点城市，公交路线必填。

        Returns:
            包含距离、时长和路线说明的结构化数据。

        Raises:
            ValueError: 路线类型不支持或公交城市缺失时抛出。
            RuntimeError: MCP路线调用失败时抛出。
        """
        tool_map = {
            "walking": "maps_direction_walking_by_coordinates",
            "driving": "maps_direction_driving_by_coordinates",
            "transit": "maps_direction_transit_integrated_by_coordinates",
        }

        tool_name = tool_map.get(route_type)
        if tool_name is None:
            raise ValueError(f"不支持的路线类型: {route_type}")

        arguments = {
            "origin": f"{origin.longitude},{origin.latitude}",
            "destination": (
                f"{destination.longitude},{destination.latitude}"
            ),
        }

        if route_type == "transit":
            if not origin_city:
                raise ValueError("公交路线必须提供起点城市")

            arguments["city"] = origin_city
            arguments["cityd"] = destination_city or origin_city

        try:
            data = self._call_tool(tool_name, arguments)
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(str(data["error"]))
            route_info = self._parse_route_result(data, route_type)
            if route_info:
                route_info["source"] = self._build_fact_source(
                    "高德坐标路线规划"
                ).model_dump()
            return route_info
        except Exception as exc:
            raise RuntimeError(
                f"{route_type}坐标路线规划失败: {exc}"
            ) from exc

    def _parse_route_result(self, data: Any, route_type: str) -> Dict[str, Any]:
        """解析步行/驾车/公交路线结果。"""
        if not data:
            return {}

        if not isinstance(data, dict):
            return {
                "distance": 0,
                "duration": 0,
                "route_type": route_type,
                "description": str(data)
            }

        route = data.get("route") or data

        if data.get("error"):
            return {}

        # walking / driving 常见结构: route.paths[0]
        paths = route.get("paths") or data.get("paths") or []
        if paths:
            path = paths[0]
            steps = path.get("steps") or []

            instructions = []
            for step in steps:
                if isinstance(step, dict):
                    instruction = step.get("instruction") or step.get("road") or ""
                    if instruction:
                        instructions.append(str(instruction))

            return {
                "distance": float(path.get("distance") or 0),
                "duration": int(float(path.get("duration") or 0)),
                "route_type": route_type,
                "description": "；".join(instructions[:8]) or "路线规划成功",
                "geometry": self._parse_route_geometry(
                    [path.get("polyline"), path.get("path")]
                    + [step.get("polyline") for step in steps if isinstance(step, dict)]
                ),
            }

        # transit 常见结构: route.transits[0]
        transits = route.get("transits") or data.get("transits") or []
        if transits:
            transit = transits[0]
            segments = transit.get("segments") or []

            instructions = []
            for segment in segments:
                if not isinstance(segment, dict):
                    continue

                walking = segment.get("walking") or {}
                bus = segment.get("bus") or {}

                if walking.get("distance"):
                    instructions.append(f"步行 {walking.get('distance')} 米")

                buslines = bus.get("buslines") or []
                if buslines:
                    line = buslines[0]
                    name = line.get("name") or "公交/地铁"
                    departure = line.get("departure_stop", {}).get("name", "")
                    arrival = line.get("arrival_stop", {}).get("name", "")
                    instructions.append(f"乘坐 {name}: {departure} 到 {arrival}")

            geometry_parts: List[Any] = []
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                walking = segment.get("walking") or {}
                for step in walking.get("steps") or []:
                    if isinstance(step, dict):
                        geometry_parts.append(step.get("polyline"))
                bus = segment.get("bus") or {}
                for busline in bus.get("buslines") or []:
                    if isinstance(busline, dict):
                        geometry_parts.append(busline.get("polyline"))
                railway = segment.get("railway") or {}
                if isinstance(railway, dict):
                    geometry_parts.append(railway.get("polyline"))

            return {
                "distance": float(
                    transit.get("distance")
                    or route.get("distance")
                    or 0
                ),
                "duration": int(float(transit.get("duration") or 0)),
                "route_type": route_type,
                "description": (
                        "；".join(instructions[:8])
                        or "公交路线规划成功"
                ),
                "geometry": self._parse_route_geometry(geometry_parts),
            }

        return {
            "distance": 0,
            "duration": 0,
            "route_type": route_type,
            "description": str(data)[:500]
        }

    def _parse_route_geometry(self, values: List[Any]) -> List[Dict[str, float]]:
        """
        将高德 path 或 step 中的 polyline 字段合并为有序坐标列表。

        Args:
            values: 字符串、坐标列表或空值组成的 geometry 片段。

        Returns:
            去除相邻重复点后的经纬度字典列表。
        """
        geometry: List[Dict[str, float]] = []
        for value in values:
            points: List[Any]
            if isinstance(value, str):
                points = [point for point in value.split(";") if point]
            elif isinstance(value, list):
                points = value
            else:
                continue

            for point in points:
                location = self._parse_location(point)
                if location is None:
                    continue
                payload = location.model_dump()
                if not geometry or payload != geometry[-1]:
                    geometry.append(payload)
        return geometry

    def geocode(self, address: str, city: Optional[str] = None) -> Optional[Location]:
        try:
            arguments = {"address": address}
            if city:
                arguments["city"] = city

            data = self._call_tool("maps_geo", arguments)

            geocodes = []
            if isinstance(data, dict):
                geocodes = data.get("geocodes") or data.get("data") or []
            elif isinstance(data, list):
                geocodes = data

            if not geocodes:
                return None

            return self._parse_location(geocodes[0].get("location"))

        except Exception as e:
            print(f"❌ 地理编码失败: {str(e)}")
            return None

    def get_poi_detail(self, poi_id: str) -> Dict[str, Any]:
        """
        获取POI详情

        Args:
            poi_id: POI ID

        Returns:
            POI详情信息
        """
        try:
            result = self.mcp_tool.run({
                "action": "call_tool",
                "tool_name": "maps_search_detail",
                "arguments": {
                    "id": poi_id
                }
            })

            print(f"POI详情结果: {result[:200]}...")

            # 解析结果并提取图片
            import json
            import re

            # 尝试从结果中提取JSON
            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return data

            return {"raw": result}

        except Exception as e:
            print(f"❌ 获取POI详情失败: {str(e)}")
            return {}

    def _call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        调用并解析 MCP 工具，同时记录次数、耗时和失败类型。

        Args:
            tool_name: 高德 MCP 工具名称。
            arguments: 经过服务层构造的工具参数。

        Returns:
            已解析为 Python 字典、列表或包装对象的工具结果。

        Raises:
            Exception: MCP 传输、超时或解析过程中的原始异常。
        """
        requested_at = datetime.now(timezone.utc)
        started_at = perf_counter()
        succeeded = False
        error_type: Optional[str] = None

        try:
            if hasattr(self.mcp_tool, "call_tool_raw"):
                raw = self.mcp_tool.call_tool_raw(
                    tool_name,
                    arguments,
                    timeout=20,
                )
            else:
                raw = self.mcp_tool.run({
                    "action": "call_tool",
                    "tool_name": tool_name,
                    "arguments": arguments
                })

            parsed = self._parse_mcp_result(raw)
            succeeded = True
            return parsed
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            metric = ExternalCallMetric(
                provider="amap_mcp",
                operation=tool_name,
                succeeded=succeeded,
                elapsed_seconds=round(perf_counter() - started_at, 3),
                requested_at=requested_at,
                error_type=error_type,
            )
            call_count = self._record_call_metric(metric)
            print(
                "[MCP调用统计] "
                f"tool={tool_name}, count={call_count}, "
                f"success={succeeded}, elapsed={metric.elapsed_seconds:.3f}s"
            )

    def _parse_mcp_result(self, raw: Any) -> Any:
        """兼容 MCPTool 返回的文本包装、JSON 字符串、Python 字面量。"""
        import ast
        import json
        import re

        if raw is None:
            return None

        if isinstance(raw, (dict, list)):
            return raw

        text = str(raw).strip()

        # hili_agents 的 MCPTool.run 会包装成:
        # 工具 'maps_xxx' 执行结果:
        # {...}
        if "执行结果:" in text:
            text = text.split("执行结果:", 1)[1].strip()

        # 先尝试整体 JSON
        try:
            return json.loads(text)
        except Exception:
            pass

        # 再尝试 Python 字面量，比如 "{'status': '1'}"
        try:
            return ast.literal_eval(text)
        except Exception:
            pass

        # 再从文本中截取 JSON 对象或数组
        extracted = self._extract_json_text(text)
        if extracted:
            try:
                return json.loads(extracted)
            except Exception:
                try:
                    return ast.literal_eval(extracted)
                except Exception:
                    pass

        return {"raw": text}

    def _extract_json_text(self, text: str) -> Optional[str]:
        """从混合文本里提取最外层 JSON 对象或数组。"""
        object_start = text.find("{")
        object_end = text.rfind("}")

        array_start = text.find("[")
        array_end = text.rfind("]")

        candidates = []

        if object_start != -1 and object_end != -1 and object_end > object_start:
            candidates.append(text[object_start:object_end + 1])

        if array_start != -1 and array_end != -1 and array_end > array_start:
            candidates.append(text[array_start:array_end + 1])

        if not candidates:
            return None

        return max(candidates, key=len)

    def _parse_location(self, value: Any) -> Optional[Location]:
        """解析高德常见 location 字段: 'lng,lat' 或 dict。"""
        if not value:
            return None

        if isinstance(value, dict):
            lng = value.get("longitude") or value.get("lng")
            lat = value.get("latitude") or value.get("lat")
            if lng is not None and lat is not None:
                return Location(longitude=float(lng), latitude=float(lat))

        if isinstance(value, str) and "," in value:
            lng, lat = value.split(",", 1)
            return Location(longitude=float(lng), latitude=float(lat))

        return None

    def _parse_photo_urls(self, value: Any) -> List[str]:
        """
        从高德照片字段中提取可展示的 URL。

        Args:
            value: 高德返回的照片数组、字符串数组或空值。

        Returns:
            去重后的非空照片 URL 列表。
        """
        if not isinstance(value, list):
            return []

        urls: List[str] = []
        for photo in value:
            if isinstance(photo, str):
                url = photo.strip()
            elif isinstance(photo, dict):
                url = str(photo.get("url") or "").strip()
            else:
                continue

            if url and url not in urls:
                urls.append(url)

        return urls

    def _parse_optional_float(self, value: Any) -> Optional[float]:
        """
        将高德可能返回的字符串评分转换为浮点数。

        Args:
            value: 字符串、数字、空数组或空值形式的评分。

        Returns:
            可解析的浮点数；无法解析时返回 None。
        """
        if value in (None, "", []):
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_optional_text(value: Any) -> Optional[str]:
        """
        将高德可能返回的字符串、列表或空对象归一化为可选文本。

        Args:
            value: 原始扩展字段。

        Returns:
            非空文本；没有有效内容时返回 None。
        """
        if isinstance(value, str):
            text = value.strip()
            return text or None
        if isinstance(value, list):
            parts = [str(item).strip() for item in value if str(item).strip()]
            return "；".join(parts) or None
        return None
# 创建全局服务实例
_amap_service = None


def get_amap_service() -> AmapService:
    """获取高德地图服务实例(单例模式)"""
    global _amap_service
    
    if _amap_service is None:
        _amap_service = AmapService()
    
    return _amap_service

