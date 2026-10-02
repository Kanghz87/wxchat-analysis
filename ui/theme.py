"""页面与图表共用的浅色 / 黑夜配色。"""
import json

import streamlit as st
import streamlit.components.v1 as components


def palette(dark: bool = False) -> dict[str, str]:
    if dark:
        return {
            "background": "#0e1117", "surface": "#262730", "text": "#fafafa",
            "muted": "#b0b5c0", "grid": "#343946", "border": "#515b78",
            "mine": "#8193ff", "other": "#c4dd58", "total": "#a4aecb",
            "template": "plotly_dark",
        }
    return {
        "background": "#ffffff", "surface": "#f0f2f6", "text": "#31333f",
        "muted": "#6b7280", "grid": "#e5e7eb", "border": "#d5dae3",
        "mine": "#5266ce", "other": "#7b9424", "total": "#68738f",
        "template": "plotly_white",
    }


def chart_palette() -> dict[str, str]:
    return palette(st.session_state.get("dark_mode", False))


def render_theme_toggle() -> None:
    dark = st.toggle("黑夜模式", key="dark_mode", value=False)
    colors = palette(dark)
    # 使用当前 Streamlit 内置主题消息，让输入框、弹出日历、表格一起切换。
    # config.toml 的 allowedOrigins 只允许本机来源，消息不经过外部服务。
    theme = {
        "base": int(dark), "primaryColor": "#16866a",
        "backgroundColor": colors["background"],
        "secondaryBackgroundColor": colors["surface"], "textColor": colors["text"],
    }
    data = json.dumps(theme)
    components.html(
        "<script>window.parent.postMessage({stCommVersion:1,"
        'type:"SET_CUSTOM_THEME_CONFIG",themeInfo:' + data
        + "}, window.parent.location.origin);</script>",
        height=0,
    )
