"""本地 Plotly 日期轴驱动小时统计，只向前端传递日期和聚合数量。"""
from datetime import date, timedelta
from functools import lru_cache
import json
from pathlib import Path

import pandas as pd
from plotly.offline import get_plotlyjs
import streamlit.components.v1 as components

from analysis.basic import daily_counts, filter_dates, hourly_counts
from ui.theme import palette


def recent_range(start: date, end: date) -> tuple[date, date]:
    return max(start, end - timedelta(days=29)), end


def date_bounds(start: date, end: date) -> tuple[str, str]:
    """保留结束日全部统计，同时不露出下一天的日期。"""
    return start.isoformat(), end.isoformat() + "T23:59:59.999"


def hourly_payload(frame: pd.DataFrame, start: date, end: date, dark: bool = False) -> dict:
    colors = palette(dark)
    daily = daily_counts(frame, start, end)
    dates = daily["日期"].astype(str).tolist()
    matrix = frame.groupby(["date", "hour"]).size().unstack(fill_value=0).reindex(
        index=daily["日期"], columns=range(24), fill_value=0,
    ) if not frame.empty else pd.DataFrame(0, index=daily["日期"], columns=range(24))
    first, right = recent_range(start, end)
    hours = hourly_counts(filter_dates(frame, first, end))["消息数量"].tolist()
    return {
        "dates": dates, "hours": matrix.to_numpy().tolist(),
        "recent": list(date_bounds(first, right)),
        "full": list(date_bounds(start, end)), "colors": colors,
        "figure": {
            "data": [
                {"type": "bar", "x": list(range(24)), "y": hours, "marker": {"color": colors["mine"]},
                 "hovertemplate": "%{x}:00<br>%{y} 条<extra></extra>"},
                {"type": "scatter", "x": dates, "y": daily["总计"].tolist(), "xaxis": "x2", "yaxis": "y2",
                 "mode": "lines", "line": {"color": colors["total"], "width": 1.5}, "fill": "tozeroy",
                 "fillcolor": "rgba(113,131,236,0.18)", "hovertemplate": "%{x|%Y-%m-%d}<br>%{y} 条<extra></extra>"},
            ],
            "layout": {
                "height": 510, "paper_bgcolor": colors["background"], "plot_bgcolor": colors["background"],
                "font": {"color": colors["text"], "family": "Microsoft YaHei, sans-serif"},
                "showlegend": False, "dragmode": "pan",
                "margin": {"l": 58, "r": 25, "t": 15, "b": 28},
                "xaxis": {"title": {"text": "小时"}, "dtick": 1, "fixedrange": True, "anchor": "y",
                          "showgrid": False},
                "yaxis": {"title": {"text": "消息数量"}, "domain": [0.43, 1], "fixedrange": True,
                          "gridcolor": colors["grid"], "rangemode": "tozero"},
                "xaxis2": {"type": "date", "anchor": "y2", "tickformat": "%m/%d",
                           "range": list(date_bounds(first, right)),
                           "minallowed": date_bounds(start, end)[0], "maxallowed": date_bounds(start, end)[1],
                           "rangeslider": {"visible": True, "thickness": 0.09, "bgcolor": colors["surface"],
                                           "bordercolor": colors["border"], "borderwidth": 1, "autorange": False,
                                           "range": list(date_bounds(start, end))}},
                "yaxis2": {"domain": [0.09, 0.24], "fixedrange": True, "showticklabels": False,
                           "showgrid": False, "zeroline": False},
            },
        },
    }


@lru_cache(maxsize=1)
def local_plotly_js() -> str:
    return get_plotlyjs().replace("</script", r"<\/script")


def hourly_html(frame: pd.DataFrame, start: date, end: date, dark: bool = False) -> str:
    template = Path(__file__).with_name("hourly_chart.html").read_text(encoding="utf-8")
    data = json.dumps(hourly_payload(frame, start, end, dark), ensure_ascii=False).replace("<", r"\u003c")
    return template.replace("__DATA__", data).replace("__PLOTLY_JS__", local_plotly_js())


def render_interactive_hourly(frame: pd.DataFrame, start: date, end: date, dark: bool = False) -> None:
    components.html(hourly_html(frame, start, end, dark), height=590, scrolling=False)
