"""仅使用合成消息验证类型分类、总数守恒和日期筛选。"""
from datetime import date
import unittest

import pandas as pd

from analysis.basic import filter_dates, message_type_counts, reference_counts
from core.message_types import message_type_label


class MessageTypeTests(unittest.TestCase):
    def make_frame(self) -> pd.DataFrame:
        kinds = [
            1, 1, 3, 47, (8 << 32) | 49, (33 << 32) | 49,
            (36 << 32) | 49, (57 << 32) | 49, 10000,
            49, 9876, None, (999 << 32) | 49,
            (2000 << 32) | 49, (2001 << 32) | 49, (62 << 32) | 49,
            48, 42, 66, (17 << 32) | 11000, (17 << 32) | 49,
        ]
        labels = [message_type_label(kind) for kind in kinds]
        labels[7] = "文字"  # 引用回复自己的文字，与被引用类型无关。
        senders = ["我" if index % 2 == 0 else "对方" for index in range(len(kinds))]
        senders[8], senders[10], senders[11] = "系统", "未识别", "未识别"
        return pd.DataFrame({
            "local_type": pd.Series(kinds, dtype="Int64"),
            "message_type": labels, "is_reference": [index == 7 for index in range(len(kinds))],
            "sender": senders,
            "date": [date(2024, 1, 1)] * 9 + [date(2024, 1, 2)] * (len(kinds) - 9),
            "hour": 12,
        })

    def test_composite_types_and_count_conservation(self) -> None:
        frame = self.make_frame()
        result = message_type_counts(frame).set_index("消息类型")
        self.assertEqual(result.loc["表情", "消息数量"], 2)
        self.assertEqual(result.loc["小程序", "消息数量"], 2)
        self.assertEqual(result.loc["文字", "消息数量"], 3)
        self.assertEqual(result.loc["其他", "消息数量"], 2)
        self.assertNotIn("引用消息", result.index)
        for excluded in ("系统消息", "系统通知", "位置", "位置共享", "名片", "企业微信名片"):
            self.assertNotIn(excluded, result.index)
        self.assertEqual(reference_counts(frame), {"total": 1, "mine": 0, "other": 1})
        self.assertEqual(result.loc["其他应用消息", "消息数量"], 1)
        for label in ("转账消息", "红包消息", "拍一拍", "应用消息（未细分）"):
            self.assertEqual(result.loc[label, "消息数量"], 1)
        self.assertEqual(message_type_label((17 << 32) | 11000), "系统通知")
        self.assertEqual(message_type_label((6 << 32) | 3), "其他 / 未识别")
        self.assertEqual(result["消息数量"].sum(), 15)
        self.assertEqual(result[["我发送", "对方发送"]].to_numpy().sum(), 13)
        self.assertEqual(list(result.columns), ["消息数量", "占比", "我发送", "对方发送"])
        self.assertAlmostEqual(result["占比"].sum(), 100.0)
        self.assertAlmostEqual(result.loc["文字", "占比"], 20.0)

    def test_date_filter_and_empty_result(self) -> None:
        frame = self.make_frame()
        filtered = filter_dates(frame, date(2024, 1, 2), date(2024, 1, 2))
        result = message_type_counts(filtered)
        self.assertEqual(result["消息数量"].sum(), 7)
        self.assertNotIn("文字", result["消息类型"].tolist())
        self.assertAlmostEqual(result["占比"].sum(), 100.0)
        empty = message_type_counts(filter_dates(frame, date(2020, 1, 1), date(2020, 1, 2)))
        self.assertTrue(empty.empty)
        self.assertEqual(list(empty.columns), list(result.columns))


if __name__ == "__main__":
    unittest.main()
