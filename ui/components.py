from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from analysis.basic import busiest_period, daily_counts, filter_dates, longest_text, message_type_counts, reference_counts, summarize, weekday_hour_counts
from ui.hourly_chart import date_bounds, recent_range, render_interactive_hourly
from ui.theme import chart_palette

WAL_NOTICE = "第一版仅分析主 DB；最近尚未写回主数据库的消息可能未包含。WAL/SHM 已复制保留，暂不合并。"
DATE_MIN = date(1970, 1, 1)
CHART_PREFIXES = ("daily", "hourly", "heatmap")


def date_max(last: date) -> date:
    return max(date.today(), last)


def sync_chart_dates(context: str, start: date, end: date) -> None:
    """顶部日期或联系人变化时同步；单独调整某张图时保留其他图的范围。"""
    scope = (context, start, end)
    if st.session_state.get("chart_date_scope") != scope:
        for prefix in CHART_PREFIXES:
            st.session_state[f"{prefix}_start_date"] = start
            st.session_state[f"{prefix}_end_date"] = end
        st.session_state["chart_date_scope"] = scope


def chart_frame(frame: pd.DataFrame, prefix: str) -> tuple[pd.DataFrame, date, date] | None:
    left, right = st.columns(2)
    first, last = frame["date"].min(), frame["date"].max()
    start = left.date_input("本图起始日期", key=f"{prefix}_start_date", min_value=min(DATE_MIN, first), max_value=date_max(last), format="YYYY-MM-DD")
    end = right.date_input("本图结束日期", key=f"{prefix}_end_date", min_value=min(DATE_MIN, first), max_value=date_max(last), format="YYYY-MM-DD")
    if start is None or end is None:
        st.warning("请选择本图的起止日期。")
        return None
    if start > end:
        st.warning("本图起始日期不能晚于结束日期。")
        return None
    selected = filter_dates(frame, start, end)
    st.caption(f"统计区间：{start:%Y-%m-%d} 至 {end:%Y-%m-%d} · {len(selected):,} 条消息")
    if selected.empty:
        st.info("本图所选日期范围内没有聊天记录。")
    # 日期输入可早于记录，但图表右端不延伸到最新可用记录之后。
    if not selected.empty:
        end = min(end, last, date.today())
    return selected, start, end


def render_metrics(frame: pd.DataFrame) -> None:
    stats = summarize(frame)
    columns = st.columns(5)
    for column, label, key in zip(columns, ["总消息", "我发送", "对方发送", "活跃天数", "文字消息"], ["total", "mine", "other", "active_days", "text_count"]):
        column.metric(label, f"{stats[key]:,}")
    left, right = st.columns(2)
    left.metric("消息最多的一天", str(stats["busiest_day"]) if stats["busiest_day"] else "—")
    left.caption(f'{stats["busiest_day_count"]:,} 条')
    hour = stats["busiest_hour"]
    right.metric("最活跃小时段", f"{hour:02d}:00–{hour + 1:02d}:00" if hour is not None else "—")
    system_count = int((frame["sender"] == "系统").sum())
    unknown_count = int((frame["sender"] == "未识别").sum())
    if system_count or unknown_count:
        st.caption(f"总消息包含 {system_count:,} 条系统消息、{unknown_count:,} 条发送者未识别消息；这些消息不计入双方发送数量。")
    if frame.empty:
        st.info("统一日期范围内没有聊天记录；下方图表仍可单独选择其他日期。")


def render_message_types(frame: pd.DataFrame) -> None:
    st.subheader("消息类型统计")
    st.caption("使用顶部的统一日期范围；系统、位置和名片消息已排除，占比按表内消息总数计算。")
    references = reference_counts(frame)
    total, mine, other = st.columns(3)
    total.metric("引用次数", f'{references["total"]:,}')
    mine.metric("我引用", f'{references["mine"]:,}')
    other.metric("对方引用", f'{references["other"]:,}')
    counts = message_type_counts(frame)
    if counts.empty:
        st.info("所选日期范围内没有可统计的消息类型。")
        return
    st.dataframe(
        counts, key="message_type_table", hide_index=True, width="stretch",
        column_config={"占比": st.column_config.NumberColumn(format="%.1f%%")},
    )
    st.caption(
        "引用次数另计，本次回复按自身内容归入文字、表情等类型，不重复增加消息总数。"
        "表情指表情包，文字中的表情符号计为文字；红包和转账按消息条数计数。"
    )


def render_daily(frame: pd.DataFrame) -> None:
    st.subheader("每日消息趋势")
    selection = chart_frame(frame, "daily")
    if selection is None:
        return
    selected, start, end = selection
    daily = daily_counts(selected, start, end)
    period = busiest_period(daily)
    colors = chart_palette()
    chart = px.line(daily, x="日期", y=["我", "对方", "总计"], render_mode="svg", labels={"value": "消息数量", "variable": "发送者"})
    trace_colors = {"我": colors["mine"], "对方": colors["other"], "总计": colors["total"]}
    for trace in chart.data:
        trace.update(mode="lines+markers", line=dict(color=trace_colors[trace.name], width=2, shape="spline", smoothing=0.5), marker=dict(size=4))
        trace.update(hovertemplate=trace.name + "<br>日期=%{x|%m/%d}<br>消息数量=%{y}<extra></extra>")
        if trace.name == "总计":
            trace.update(fill="tozeroy", fillcolor="rgba(113,131,236,0.16)")
    if period is not None:
        first, last = period["start"], period["end"]
        st.success(
            f"持续高活跃：{first:%Y-%m-%d} 至 {last:%Y-%m-%d}"
            f"（{period['days']} 天），双方共 {period['message_count']:,} 条消息。"
        )
        chart.add_vrect(
            x0=first, x1=last + timedelta(days=1),
            fillcolor="#ef4444", opacity=0.12, line_width=0, layer="below",
        )
        chart.add_annotation(
            x=first + (last - first) / 2, y=1.02, yref="paper",
            text="持续高活跃区间", showarrow=False, font=dict(color="#b91c1c"),
        )
    elif not selected.empty:
        st.info("当前范围未识别出明显的持续高活跃区间。")
    st.caption(
        "自动识别仅针对本图日期范围内的双方消息，不包含系统或发送者未识别的消息。"
        "依据七日平均趋势寻找持续高于平常水平的区间，长度不固定，至少包含三个较活跃的日期；"
        "多段中取双方消息数最多的一段，并列取较早的一段。"
    )
    first, right = recent_range(start, end)
    full_left, full_right = date_bounds(start, end)
    chart.update_xaxes(
        type="date", tickformat="%m/%d", range=list(date_bounds(first, right)),
        minallowed=full_left, maxallowed=full_right,
        rangeslider=dict(visible=True, thickness=0.1, bgcolor=colors["surface"],
                         bordercolor=colors["border"], borderwidth=1,
                         autorange=False, range=[full_left, full_right]),
        rangeselector=dict(buttons=[
            dict(count=30, label="最近一个月", step="day", stepmode="backward"),
            dict(label="全部日期", step="all"),
        ], bgcolor=colors["surface"], activecolor=colors["border"], font=dict(color=colors["text"])),
        showgrid=False,
    )
    chart.update_yaxes(fixedrange=True, gridcolor=colors["grid"], rangemode="tozero")
    chart.update_layout(
        template=colors["template"], paper_bgcolor=colors["background"], plot_bgcolor=colors["background"],
        font=dict(color=colors["text"]), legend_title_text="", legend=dict(orientation="h", x=0.5, xanchor="center", y=1.12),
        margin=dict(t=70, b=20), height=490, dragmode="pan", hovermode="x unified",
        uirevision=str(st.session_state.get("chart_date_scope")) + str((start, end)),
    )
    st.caption("默认显示本图范围内最近 30 天；滚轮缩放日期，按住左键左右拖动，底部滑条可查看全部历史。")
    st.plotly_chart(chart, key="daily_chart", width="stretch", theme=None,
                   config={"displaylogo": False, "scrollZoom": True, "displayModeBar": False})


def render_hourly(frame: pd.DataFrame) -> None:
    st.subheader("24 小时消息分布")
    selection = chart_frame(frame, "hourly")
    if selection is None:
        return
    selected, start, end = selection
    render_interactive_hourly(selected, start, end, dark=st.session_state.get("dark_mode", False))


def render_heatmap(frame: pd.DataFrame) -> None:
    st.subheader("星期 × 小时热力图")
    selection = chart_frame(frame, "heatmap")
    if selection is None:
        return
    heatmap = weekday_hour_counts(selection[0])
    chart = go.Figure(go.Heatmap(
        z=heatmap.values, x=list(range(24)), y=["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
        colorscale="Reds", colorbar=dict(title="消息数"), zmin=0,
        hovertemplate="%{y} %{x}:00<br>%{z} 条<extra></extra>",
    ))
    chart.update_xaxes(title="小时", tickmode="linear", dtick=1)
    chart.update_yaxes(autorange="reversed")
    colors = chart_palette()
    chart.update_layout(height=330, margin=dict(t=10, b=10), template=colors["template"],
                        paper_bgcolor=colors["background"], plot_bgcolor=colors["background"],
                        font=dict(color=colors["text"]))
    st.plotly_chart(chart, key="heatmap_chart", width="stretch", theme=None, config={"displaylogo": False})


def render_longest(frame: pd.DataFrame) -> None:
    st.subheader("最长文字消息")
    st.caption("使用顶部的统一日期范围。")
    longest = longest_text(frame)
    st.dataframe(longest, hide_index=True, width="stretch", column_config={
        "时间": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm:ss"),
        "内容": st.column_config.TextColumn(width="large"),
    })
    if not longest.empty:
        st.caption("下表可能折叠长内容，可在这里查看完整原文。")
        labels = {int(row["排名"]): f'第 {row["排名"]} 名 · {row["字符数"]} 字符 · {row["发送者"]}' for _, row in longest.iterrows()}
        with st.expander("查看完整文字"):
            rank = st.selectbox("消息排名", list(labels), format_func=labels.get)
            st.text(longest.loc[longest["排名"] == rank, "内容"].iloc[0])


def render_analysis(frame: pd.DataFrame, start: date, end: date, context: str = "") -> None:
    sync_chart_dates(context, start, end)
    selected = filter_dates(frame, start, end)
    render_metrics(selected)
    render_message_types(selected)
    render_daily(frame)
    render_hourly(frame)
    render_heatmap(frame)
    render_longest(selected)
