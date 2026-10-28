"""旅行计划生成进度契约。"""

from typing import Callable


TripProgressCallback = Callable[[str, int, str], None]


class TripGenerationCancelled(RuntimeError):
    """表示用户已请求取消当前旅行计划生成任务。"""
