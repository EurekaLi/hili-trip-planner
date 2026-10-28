"""多 Agent 控制层的结构化输入输出契约。"""

import json
from typing import Dict, List, Literal, Optional, Type, TypeVar

from pydantic import BaseModel, Field, field_validator

from ..models.schemas import AgentRunMetrics


class ControlDecision(BaseModel):
    """控制 Agent 决策共有的运行状态。"""

    status: Literal["success", "degraded"] = "success"
    error_message: Optional[str] = None
    metrics: Optional[AgentRunMetrics] = None


class AttractionControlDecision(ControlDecision):
    """景点控制 Agent 的结构化决策。"""

    selected_poi_ids: List[str] = Field(default_factory=list)
    daily_groups: List[List[str]] = Field(default_factory=list)
    selection_reason: str = ""

    @field_validator("selected_poi_ids", mode="before")
    @classmethod
    def normalize_selected_ids(cls, value):
        """
        将景点 ID 字段归一化为字符串列表。

        Args:
            value: LLM 返回的景点 ID 字段。

        Returns:
            去除空值后的字符串 ID 列表。
        """
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]


class WeatherDateAdvice(BaseModel):
    """单个日期的天气风险建议。"""

    date: str
    risk: str = ""
    suggestion: str = ""


class WeatherControlDecision(ControlDecision):
    """天气控制 Agent 的结构化决策。"""

    date_advice: List[WeatherDateAdvice] = Field(default_factory=list)


class HotelControlDecision(ControlDecision):
    """住宿控制 Agent 的结构化决策。"""

    selected_hotel_ids: List[str] = Field(default_factory=list)
    selection_reason: str = ""

    @field_validator("selected_hotel_ids", mode="before")
    @classmethod
    def normalize_selected_ids(cls, value):
        """
        将酒店 ID 字段归一化为字符串列表。

        Args:
            value: LLM 返回的酒店 ID 字段。

        Returns:
            去除空值后的字符串 ID 列表。
        """
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]


class ControlAgentDecisions(BaseModel):
    """一次旅行规划请求的全部控制 Agent 决策。"""

    attraction: AttractionControlDecision
    weather: WeatherControlDecision
    hotel: HotelControlDecision
    degraded_agents: List[str] = Field(default_factory=list)


DecisionModel = TypeVar("DecisionModel", bound=ControlDecision)


def extract_json_object(response: str) -> Dict:
    """
    从纯 JSON 或 Markdown 代码块中解析一个 JSON 对象。

    Args:
        response: 控制 Agent 返回的原始文本。

    Returns:
        解析后的 JSON 对象。

    Raises:
        ValueError: 响应中没有完整 JSON 对象或根节点不是对象时抛出。
    """
    text = response.strip()
    if "```json" in text:
        start = text.find("```json") + len("```json")
        end = text.find("```", start)
        text = text[start:end if end != -1 else len(text)].strip()
    elif "```" in text:
        start = text.find("```") + len("```")
        end = text.find("```", start)
        text = text[start:end if end != -1 else len(text)].strip()

    object_start = text.find("{")
    object_end = text.rfind("}")
    if object_start == -1 or object_end <= object_start:
        raise ValueError("控制 Agent 响应中没有完整 JSON 对象")

    payload = json.loads(text[object_start:object_end + 1])
    if not isinstance(payload, dict):
        raise ValueError("控制 Agent JSON 根节点必须是对象")
    return payload


def parse_control_decision(
        response: str,
        model_type: Type[DecisionModel],
) -> DecisionModel:
    """
    将控制 Agent 原始文本解析为指定 Pydantic 决策模型。

    Args:
        response: 控制 Agent 返回的原始文本。
        model_type: 目标决策模型类型。

    Returns:
        完成 JSON 与字段校验的结构化决策。
    """
    return model_type.model_validate(extract_json_object(response))
