"""DataFrame 转网页数据，沿用现有统计口径。"""
from datetime import date
import json

import pandas as pd

from analysis.basic import (
    busiest_period, daily_counts, filter_dates, longest_text, message_type_counts,
    reference_counts, summarize, weekday_hour_counts,
)
from core.errors import UserError


def interval(value: dict | None, first: date, last: date) -> tuple[date, date]:
    try:
        start = date.fromisoformat(value["start"]) if value else first
        end = date.fromisoformat(value["end"]) if value else min(last, date.today())
        if start > end or start < date(1970, 1, 1) or end > date.today():
            raise ValueError()
        return start, end
    except (KeyError, TypeError, ValueError):
        raise UserError("日期无效：请填写完整起止日期，起始日期不能晚于结束日期，不能选择未来日期。") from None


def records(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))


def bounds(start: date, end: date) -> list[str]:
    return [start.isoformat(), end.isoformat() + "T23:59:59.999"]


def analysis_data(frame: pd.DataFrame, request: dict, shards: int) -> dict:
    first = frame["date"].min() if not frame.empty else date.today()
    last = frame["date"].max() if not frame.empty else date.today()
    global_start, global_end = interval(request.get("global"), first, last)
    selected = filter_dates(frame, global_start, global_end)
    metrics = summarize(selected)
    if metrics["busiest_day"] is not None:
        metrics["busiest_day"] = metrics["busiest_day"].isoformat()
    result = {
        "available": {"first": first.isoformat(), "last": last.isoformat(), "count": len(frame), "shards": shards},
        "empty": frame.empty, "warnings": frame.attrs.get("warnings", []),
        "metrics": metrics, "references": reference_counts(selected),
        "types": records(message_type_counts(selected)), "longest": records(longest_text(selected)),
        "system_count": int(selected["sender"].eq("系统").sum()),
        "unknown_count": int(selected["sender"].eq("未识别").sum()), "charts": {},
    }
    choices = request.get("charts", {})
    if not isinstance(choices, dict):
        raise UserError("图表日期参数无效。")
    for name in ("daily", "hourly", "heatmap"):
        start, end = interval(choices.get(name), global_start, global_end)
        chart_frame = filter_dates(frame, start, end)
        # 选择没有数据的历史日期允许空图，右端始终不超过最新记录及今天。
        if end > last:
            end = last
        chart = {"count": len(chart_frame), "bounds": bounds(start, end) if start <= end else None}
        if name == "heatmap":
            chart["counts"] = weekday_hour_counts(chart_frame).to_numpy().tolist()
        elif start <= end:
            daily = daily_counts(chart_frame, start, end)
            chart["dates"] = daily["日期"].astype(str).tolist()
            chart["counts"] = {sender: daily[sender].tolist() for sender in ("我", "对方", "总计")}
            if name == "daily":
                period = busiest_period(daily)
                if period:
                    period = {**period, "start": period["start"].isoformat(), "end": period["end"].isoformat()}
                chart["period"] = period
            else:
                matrix = chart_frame.groupby(["date", "hour"]).size().unstack(fill_value=0) if not chart_frame.empty else pd.DataFrame()
                chart["hours"] = matrix.reindex(index=daily["日期"], columns=range(24), fill_value=0).to_numpy().tolist()
        result["charts"][name] = chart
    return result
