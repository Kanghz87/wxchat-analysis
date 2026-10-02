"""持续高活跃区间的合成数据回归。"""
from datetime import date, timedelta
import unittest

import pandas as pd

from analysis.basic import busiest_period


def daily_frame(values: list[int]) -> pd.DataFrame:
    counts = pd.Series(values, dtype="int64")
    return pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=len(values)).date,
        "我": counts // 2, "对方": counts - counts // 2, "总计": counts,
    })


class ActivityTests(unittest.TestCase):
    def assert_period(self, values: list[int], first: int, last: int, total: int) -> None:
        result = busiest_period(daily_frame(values))
        self.assertIsNotNone(result)
        self.assertEqual(result, {
            "start": date(2024, 1, 1) + timedelta(days=first),
            "end": date(2024, 1, 1) + timedelta(days=last),
            "days": last - first + 1, "message_count": total,
        })

    def test_variable_length_and_short_quiet_gap(self) -> None:
        for length in (3, 5, 9, 15):
            with self.subTest(length=length):
                self.assert_period([0] * 20 + [20] * length + [0] * 20, 20, 19 + length, 20 * length)
        self.assert_period([0] * 10 + [20] * 4 + [0] + [20] * 4 + [0] * 10, 10, 18, 160)

    def test_choose_total_not_height_and_ignore_system_spikes(self) -> None:
        values = [0] * 10 + [20] * 9 + [0] * 15 + [30] * 5 + [0] * 10
        self.assert_period(values, 10, 18, 180)
        daily = daily_frame(values)
        daily.loc[25, "总计"] = 10000
        self.assertEqual(busiest_period(daily), busiest_period(daily_frame(values)))
        self.assert_period([0] * 10 + [20] * 9 + [0] * 15 + [20] * 9 + [0] * 10, 10, 18, 180)
        self.assert_period([0] * 10 + [1000] + [0] * 15 + [20] * 9 + [0] * 10, 26, 34, 180)

    def test_no_forced_peak_for_empty_flat_or_isolated_spike(self) -> None:
        for values in ([], [0] * 30, [5] * 30, [0] * 10 + [1000] + [0] * 10,
                       [1] * 10 + [1000] + [1] * 10, [0] * 10 + [30] * 2 + [0] * 10):
            with self.subTest(values=values):
                self.assertIsNone(busiest_period(daily_frame(values)))


if __name__ == "__main__":
    unittest.main()
