from datetime import date
from typing import Any

import pandas as pd

from core.message_types import REFERENCE_TYPE, message_type_label


def message_labels(frame: pd.DataFrame) -> pd.Series:
    if "message_type" in frame:
        result = frame["message_type"].fillna("其他")
    else:
        labels = {kind: message_type_label(kind) for kind in frame["local_type"].dropna().unique()}
        result = frame["local_type"].map(labels).fillna("其他")
    return result.replace({"引用消息": "其他", "其他 / 未识别": "其他"})


def reference_counts(frame: pd.DataFrame) -> dict[str, int]:
    mask = frame["is_reference"].fillna(False) if "is_reference" in frame else frame["local_type"].eq(REFERENCE_TYPE).fillna(False)
    return {
        "total": int(mask.sum()),
        "mine": int((mask & frame["sender"].eq("我")).sum()),
        "other": int((mask & frame["sender"].eq("对方")).sum()),
    }


def filter_dates(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if start > end:
        raise ValueError("起始日期不能晚于结束日期。")
    return frame.loc[(frame["date"] >= start) & (frame["date"] <= end)].copy()


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    result = {
        "total": len(frame), "mine": int((frame["sender"] == "我").sum()),
        "other": int((frame["sender"] == "对方").sum()),
        "active_days": int(frame["date"].nunique()),
        "text_count": int(message_labels(frame).eq("文字").sum()),
        "busiest_day": None, "busiest_day_count": 0, "busiest_hour": None,
    }
    if not frame.empty:
        days = frame.groupby("date", sort=True).size()
        result["busiest_day"] = days.idxmax()
        result["busiest_day_count"] = int(days.max())
        result["busiest_hour"] = int(frame.groupby("hour", sort=True).size().idxmax())
    return result


def message_type_counts(frame: pd.DataFrame) -> pd.DataFrame:
    """排除系统、位置和名片；引用次数单独统计，回复归入正文类型。"""
    columns = ["消息类型", "消息数量", "占比", "我发送", "对方发送"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    typed = frame[["sender"]].copy()
    typed["消息类型"] = message_labels(frame)
    excluded = {"系统消息", "系统通知", "位置", "位置共享", "名片", "企业微信名片"}
    typed = typed.loc[~typed["消息类型"].isin(excluded) & typed["sender"].ne("系统")]
    if typed.empty:
        return pd.DataFrame(columns=columns)
    totals = typed.groupby("消息类型").size()
    counts = typed.groupby(["消息类型", "sender"]).size().unstack(fill_value=0)
    counts = counts.reindex(columns=["我", "对方"], fill_value=0)
    counts["消息数量"] = totals
    counts["占比"] = counts["消息数量"] / len(typed) * 100
    counts = counts.rename(columns={"我": "我发送", "对方": "对方发送"}).reset_index()
    counts.columns.name = None
    return counts[columns].sort_values(["消息数量", "消息类型"], ascending=[False, True], kind="stable").reset_index(drop=True)


def daily_counts(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    index = pd.Index(pd.date_range(start, end, freq="D").date, name="日期")
    if frame.empty:
        counts = pd.DataFrame(0, index=index, columns=["我", "对方"])
    else:
        counts = frame.groupby(["date", "sender"]).size().unstack(fill_value=0)
        counts = counts.reindex(index=index, columns=["我", "对方"], fill_value=0)
    # 系统消息及未识别的发送者也属于总消息，不能丢失或误归到“对方”。
    counts["总计"] = frame.groupby("date").size().reindex(index, fill_value=0)
    return counts.reset_index()


def busiest_period(daily: pd.DataFrame) -> dict[str, Any] | None:
    """在补齐日历日期的每日统计中，找持续高活跃且双方消息总量最多的一段。"""
    counts = (daily["我"] + daily["对方"]).reset_index(drop=True)
    if counts.empty:
        return None
    # 七日均线仅用于识别趋势，最终区间可以短于或长于七天。
    trend = counts.rolling(7, center=True, min_periods=1).mean()
    support = counts.gt(0).rolling(7, center=True, min_periods=1).sum().ge(3)
    baseline = float(trend.median())
    elevated = trend[support & trend.gt(baseline)]
    if elevated.empty:
        return None
    high = float(elevated.quantile(0.75))
    seeds = support & trend.ge(high) & trend.gt(baseline)
    threshold = max(high * 0.5, baseline)
    active = support & trend.ge(threshold) & trend.gt(baseline)
    groups = active.ne(active.shift(fill_value=False)).cumsum()
    best = None
    for _, block in counts[active].groupby(groups[active]):
        if not seeds.loc[block.index].any():
            continue
        # 至少三个日期真正有较高消息量，避免单日突增带动均线。
        if (block.ge(threshold) & block.gt(baseline)).sum() < 3:
            continue
        nonzero = block[block.gt(0)]
        first, last = int(nonzero.index[0]), int(nonzero.index[-1])
        total = int(counts.loc[first:last].sum())
        # 按时间顺序遍历，消息数相同时保留较早的一段。
        if best is None or total > best["message_count"]:
            best = {
                "start": daily.iloc[first]["日期"],
                "end": daily.iloc[last]["日期"],
                "days": last - first + 1,
                "message_count": total,
            }
    return best


def hourly_counts(frame: pd.DataFrame) -> pd.DataFrame:
    counts = frame.groupby("hour").size().reindex(range(24), fill_value=0)
    return counts.rename_axis("小时").rename("消息数量").reset_index()


def weekday_hour_counts(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(0, index=range(7), columns=range(24))
    return frame.groupby(["weekday", "hour"]).size().unstack(fill_value=0).reindex(
        index=range(7), columns=range(24), fill_value=0,
    )


def longest_text(frame: pd.DataFrame) -> pd.DataFrame:
    selected = frame[message_labels(frame).eq("文字") & (frame["char_count"] > 0)]
    result = selected.sort_values(
        ["char_count", "timestamp", "source_db", "local_id"],
        ascending=[False, True, True, True], kind="stable",
    ).head(20)[["char_count", "sender", "timestamp", "content"]].copy()
    result.columns = ["字符数", "发送者", "时间", "内容"]
    result.insert(0, "排名", range(1, len(result) + 1))
    return result.reset_index(drop=True)
