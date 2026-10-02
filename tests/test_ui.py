"""轻量日期交互与高活跃区间回归，全部使用合成消息。"""
import base64
from datetime import date
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from streamlit.testing.v1 import AppTest


APP = """
from datetime import date
import pandas as pd
import streamlit as st
from ui.components import render_analysis
from ui.theme import render_theme_toggle
render_theme_toggle()
times = pd.to_datetime(["2022-07-02 09:00", "2022-07-03 10:00", "2024-01-01 11:00", "2024-01-01 12:00", "2026-09-20 13:00"])
frame = pd.DataFrame({"timestamp": times, "sender": ["我", "对方", "我", "对方", "系统"], "local_type": [1, 3, 34, (6 << 32) | 49, 10000], "content": "测试", "char_count": 2, "sort_seq": 0, "source_db": "synthetic.db", "local_id": range(5)})
frame["date"], frame["hour"], frame["weekday"] = frame["timestamp"].dt.date, frame["timestamp"].dt.hour, frame["timestamp"].dt.weekday
start = st.date_input("统一开始", date(2022, 7, 2), key="global_start", min_value=date(1970, 1, 1), max_value=date(2026, 12, 31))
end = st.date_input("统一结束", date(2026, 9, 20), key="global_end", min_value=date(1970, 1, 1), max_value=date(2026, 12, 31))
render_analysis(frame, start, end, context="synthetic")
"""


def array_sum(value: list | dict) -> int:
    if isinstance(value, dict):
        return int(np.frombuffer(base64.b64decode(value["bdata"]), dtype=value["dtype"]).sum())
    return int(np.asarray(value).sum())


class DateControlsTest(unittest.TestCase):
    def charts(self, app: AppTest) -> list[dict]:
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        result = [json.loads(element.proto.spec) for element in app.get("plotly_chart")]
        self.assertEqual(len(result), 2)
        hourly = [element for element in app.get("iframe") if "const payload =" in element.proto.srcdoc]
        self.assertEqual(len(hourly), 1)
        payload = json.loads(re.search(r"^const payload = (.+);$", hourly[0].proto.srcdoc, re.MULTILINE).group(1))
        return [result[0], payload["figure"], result[1]]

    def totals(self, app: AppTest) -> tuple[int, int, int]:
        daily, hourly, heatmap = self.charts(app)
        daily_total = next(trace for trace in daily["data"] if trace["name"] == "总计")
        return array_sum(daily_total["y"]), array_sum(hourly["data"][0]["y"]), array_sum(heatmap["data"][0]["z"])

    def set_range(self, app: AppTest, prefix: str, start: date, end: date) -> None:
        app.date_input(key=f"{prefix}_start_date").set_value(start)
        app.date_input(key=f"{prefix}_end_date").set_value(end)
        app.run()

    def test_independent_ranges_and_global_sync(self) -> None:
        app = AppTest.from_string(APP, default_timeout=20).run()
        self.assertEqual(self.totals(app), (5, 1, 5))
        types = app.dataframe[0].value
        self.assertEqual(set(types["消息类型"]), {"文字", "图片", "语音", "文件"})
        self.assertEqual(types["消息数量"].sum(), 4)
        self.assertEqual(list(types.columns), ["消息类型", "消息数量", "占比", "我发送", "对方发送"])
        self.assertTrue(types["占比"].eq(25.0).all())
        daily_chart = self.charts(app)[0]
        self.assertEqual(daily_chart["layout"]["dragmode"], "pan")
        axis = daily_chart["layout"]["xaxis"]
        self.assertEqual(axis["range"], ["2026-08-22", "2026-09-20T23:59:59.999"])
        self.assertEqual(axis["minallowed"], "2022-07-02")
        self.assertEqual(axis["maxallowed"], "2026-09-20T23:59:59.999")
        self.assertEqual(axis["rangeslider"]["range"][-1], axis["maxallowed"])
        self.assertEqual(daily_chart["layout"]["paper_bgcolor"], "#ffffff")
        app.toggle(key="dark_mode").set_value(True).run()
        for chart in self.charts(app):
            self.assertEqual(chart["layout"]["paper_bgcolor"], "#0e1117")
        self.assertEqual(self.totals(app), (5, 1, 5))
        app.toggle(key="dark_mode").set_value(False).run()
        config = json.loads(app.get("plotly_chart")[0].proto.config)
        self.assertTrue(config["scrollZoom"])
        self.assertEqual(self.charts(app)[2]["data"][0]["colorscale"][-1][1], "rgb(103,0,13)")
        self.set_range(app, "hourly", date(2022, 7, 2), date(2022, 7, 3))
        self.assertEqual(self.totals(app), (5, 2, 5))
        self.set_range(app, "heatmap", date(2024, 1, 1), date(2024, 1, 1))
        self.assertEqual(self.totals(app), (5, 2, 2))
        self.set_range(app, "daily", date(2026, 9, 20), date(2026, 9, 20))
        self.assertEqual(self.totals(app), (1, 2, 2))
        self.assertEqual(app.metric[0].value, "5")
        self.assertEqual(app.dataframe[0].value["消息数量"].sum(), 4)
        app.date_input(key="global_start").set_value(date(2024, 1, 1))
        app.date_input(key="global_end").set_value(date(2024, 1, 1))
        app.run()
        self.assertEqual(self.totals(app), (2, 2, 2))
        self.assertEqual(app.metric[0].value, "2")
        types = app.dataframe[0].value
        self.assertEqual(set(types["消息类型"]), {"语音", "文件"})
        self.assertTrue(types["占比"].eq(50.0).all())
        app.date_input(key="global_start").set_value(date(2020, 1, 1))
        app.date_input(key="global_end").set_value(date(2020, 1, 2))
        app.run()
        self.assertEqual(self.totals(app), (0, 0, 0))
        self.set_range(app, "hourly", date(2022, 7, 2), date(2022, 7, 3))
        self.assertEqual(self.totals(app), (0, 2, 0))
        self.assertEqual(app.metric[0].value, "0")
        self.assertTrue(any("没有可统计的消息类型" in message.value for message in app.info))
        self.assertFalse(any("消息类型" in table.value.columns for table in app.dataframe))

    def test_activity_highlight_recomputes_with_chart_dates(self) -> None:
        app = AppTest.from_string("""
from datetime import date
import pandas as pd
import streamlit as st
from ui.components import render_daily
counts = [0] * 10 + [20] * 9 + [0] * 15 + [30] * 5 + [0] * 10
days = pd.date_range("2024-01-01", periods=len(counts)).date
frame = pd.DataFrame([(day, "我" if i % 2 else "对方") for day, count in zip(days, counts) for i in range(count)], columns=["date", "sender"])
st.session_state.setdefault("daily_start_date", days[0])
st.session_state.setdefault("daily_end_date", days[-1])
render_daily(frame)
""", default_timeout=20).run()

        def shape() -> dict:
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            return json.loads(app.get("plotly_chart")[0].proto.spec)["layout"]

        self.assertIn("9 天", app.success[0].value)
        self.assertIn("180 条", app.success[0].value)
        self.assertEqual(shape()["shapes"][0]["x0"], "2024-01-11")
        self.assertEqual(shape()["shapes"][0]["x1"], "2024-01-20")
        self.set_range(app, "daily", date(2024, 1, 30), date(2024, 2, 28))
        self.assertIn("5 天", app.success[0].value)
        self.assertIn("150 条", app.success[0].value)
        self.assertEqual(shape()["shapes"][0]["x0"], "2024-02-04")
        self.set_range(app, "daily", date(2024, 1, 17), date(2024, 2, 3))
        self.assertIn("3 天", app.success[0].value)
        self.assertIn("60 条", app.success[0].value)
        self.assertEqual(shape()["shapes"][0]["x0"], "2024-01-17")
        self.set_range(app, "daily", date(2024, 1, 11), date(2024, 1, 11))
        self.assertFalse(app.success)
        self.assertFalse(shape().get("shapes"))
        self.assertTrue(any("未识别出明显" in message.value for message in app.info))
        self.set_range(app, "daily", date(2024, 1, 1), date(2024, 1, 2))
        self.assertFalse(app.success)
        self.assertFalse(shape().get("shapes"))

    def test_refresh_button_clears_cached_analysis_after_success(self) -> None:
        source = """
import streamlit as st
from app import render_refresh_button
if "seeded" not in st.session_state:
    st.session_state["seeded"] = True
    st.session_state["selected_contact"] = "synthetic_peer"
    for key in ("contacts", "message_cache", "date_contact", "start_date", "end_date", "chart_date_scope"):
        st.session_state[key] = "old"
render_refresh_button("4.1.13.65")
"""
        with patch("app.refresh_workspace", return_value=Path("synthetic-active")) as refresh, patch("app.signature", return_value=("fresh",)):
            app = AppTest.from_string(source, default_timeout=20).run()
            refresh.assert_not_called()
            app.button(key="refresh_records").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            refresh.assert_called_once()
            self.assertEqual(app.session_state["selected_contact"], "synthetic_peer")
            self.assertEqual(app.session_state["active_signature"], ("fresh",))
            for key in ("contacts", "message_cache", "date_contact", "start_date", "end_date", "chart_date_scope"):
                self.assertNotIn(key, app.session_state)
            self.assertIn("聊天记录已更新", app.session_state["refresh_notice"])
        with patch("app.refresh_workspace", side_effect=RuntimeError("synthetic failure")), patch("app.log_failure"):
            app = AppTest.from_string(source, default_timeout=20).run()
            app.button(key="refresh_records").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.error)
            self.assertEqual(app.session_state["message_cache"], "old")
            self.assertNotIn("refresh_notice", app.session_state)


if __name__ == "__main__":
    unittest.main()
