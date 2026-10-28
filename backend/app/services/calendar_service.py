"""把结构化行程转换为可导入主流日历的 iCalendar 文本。"""

from datetime import date, datetime, timedelta
from typing import List

from ..models.schemas import TripPlan


def build_trip_calendar(plan: TripPlan, trip_id: str) -> str:
    """
    为行程时间轴生成 RFC 5545 兼容的浮动本地时间事件。

    Args:
        plan: 已由后端校验并重算的旅行计划。
        trip_id: 用于生成稳定事件 UID 的行程 ID。

    Returns:
        使用 CRLF 分隔的 iCalendar 文本。
    """
    lines: List[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//HiliAgents//Trip Planner//ZH-CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape_text(plan.city)}旅行计划",
    ]
    for day in plan.days:
        event_date = date.fromisoformat(day.date)
        for index, entry in enumerate(day.timeline):
            start = _combine_local(event_date, entry.start_time)
            end = _combine_local(event_date, entry.end_time)
            if end <= start:
                end += timedelta(days=1)
            lines.extend(
                [
                    "BEGIN:VEVENT",
                    f"UID:{trip_id}-{day.day_index}-{index}@hiliagents.local",
                    f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
                    f"DTSTART:{start.strftime('%Y%m%dT%H%M%S')}",
                    f"DTEND:{end.strftime('%Y%m%dT%H%M%S')}",
                    f"SUMMARY:{_escape_text(entry.title)}",
                    f"DESCRIPTION:{_escape_text(entry.note or day.description)}",
                    "END:VEVENT",
                ]
            )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _combine_local(event_date: date, time_text: str) -> datetime:
    """
    合并 ISO 日期和 HH:MM 时间为无时区本地时间。

    Args:
        event_date: 行程日期。
        time_text: 时间轴中的 HH:MM 文本。

    Returns:
        可写入浮动时间 DTSTART 或 DTEND 的 datetime。
    """
    parsed_time = datetime.strptime(time_text, "%H:%M").time()
    return datetime.combine(event_date, parsed_time)


def _escape_text(value: str) -> str:
    """
    转义 iCalendar 文本字段中的保留字符和换行。

    Args:
        value: 未转义文本。

    Returns:
        可安全写入单行属性的文本。
    """
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )
