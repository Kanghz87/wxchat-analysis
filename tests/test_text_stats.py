"""全历史文字统计的最小回归；仅使用合成消息和数据库。"""
from datetime import datetime
import sqlite3
import unittest
from unittest.mock import patch

import pandas as pd

from analysis.text_stats import text_statistics
from core.message_types import REFERENCE_TYPE
from core.messages import FRAME_COLUMNS, conversation_table, load_messages
from tests import test_core as fixtures


def frame_of(rows: list[tuple[str, int, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["sender", "local_type", "content"])


class TextStatisticsTests(unittest.TestCase):
    def test_dual_character_counts_and_message_exclusions(self) -> None:
        mine = "你好，Ab1 😀 \U00020000"
        other = "晚安 哈哈"
        mention = "今天拍一拍"
        frame = frame_of([
            ("我", 1, mine), ("对方", 1, other), ("我", 1, mention),
            ("我", (62 << 32) | 49, "自定义拍一拍文字"),
            ("我", 10000, "系统通知"), ("系统", 1, "系统正文"),
            ("未识别", 1, "未知正文"), ("对方", 47, "表情描述"),
        ])
        result = text_statistics(frame)
        self.assertEqual(result["me"], {"chinese_chars": 8, "all_chars": len(mine) + len(mention)})
        self.assertEqual(result["other"], {"chinese_chars": 4, "all_chars": len(other)})
        self.assertEqual(result["total"], {"chinese_chars": 12, "all_chars": len(mine) + len(other) + len(mention)})

    def test_word_frequency_filters_and_only_top_ten(self) -> None:
        frame = frame_of([
            ("我", 1, "我 你 的 了 是 晚安 晚安 哈哈 好 Python PYTHON 123 https://example.com/secret"),
            ("对方", 1, "晚安 哈哈 好"),
        ])
        result = text_statistics(frame)
        self.assertEqual(result["warning"], "")
        words = {row["词语"]: row for row in result["words"]}
        self.assertEqual(words["晚安"], {"词语": "晚安", "总次数": 3, "我发送": 2, "对方发送": 1})
        self.assertEqual(words["哈哈"]["总次数"], 2)
        self.assertNotIn("好", words)
        self.assertTrue(all(len(word) >= 2 for word in words))
        self.assertEqual(words["python"]["总次数"], 2)
        self.assertTrue(set(words).isdisjoint({"我", "你", "的", "了", "是", "123", "https", "example", "secret"}))
        vocabulary = ["word" + chr(ord("a") + i) for i in range(15)]
        many = text_statistics(frame_of([("我", 1, " ".join(word for i, word in enumerate(vocabulary) for _ in range(15 - i)))]))
        self.assertEqual([row["词语"] for row in many["words"]], vocabulary[:10])
        self.assertEqual([row["总次数"] for row in many["words"]], list(range(15, 5, -1)))

    def test_cross_shard_zstd_and_quoted_reply(self) -> None:
        fixture = fixtures.CoreTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        paths = fixture.make_messages()
        quoted = "<msg><appmsg><title>晚安</title><type>57</type><refermsg><content>引用原文不计入</content></refermsg></appmsg></msg>"
        with sqlite3.connect(paths[0]) as connection:
            connection.executemany(f'INSERT INTO "{conversation_table("synthetic_user")}" VALUES (?, ?, ?, ?, ?, ?)', [
                (3, REFERENCE_TYPE, 3, int(datetime(2026, 9, 1, 22, 30).timestamp()), quoted, None),
                (4, (62 << 32) | 49, 8, int(datetime(2026, 9, 1, 22, 31).timestamp()), "拍一拍自定义通知", None),
            ])
        frame = load_messages(paths, "synthetic_user", "synthetic_self")
        result = text_statistics(frame)
        self.assertEqual(result["total"]["all_chars"], len("你好😀再见晚安") + len("长文字😀" * 200))
        self.assertEqual(result["total"]["chinese_chars"], 6 + 3 * 200)
        self.assertEqual(result["me"]["all_chars"], len("你好😀再见晚安"))

    def test_empty_and_unavailable_tokenizer_keep_counts(self) -> None:
        empty = text_statistics(pd.DataFrame(columns=FRAME_COLUMNS))
        self.assertEqual(empty["total"], {"chinese_chars": 0, "all_chars": 0})
        self.assertEqual(empty["words"], [])
        with patch("analysis.text_stats._tokenizer", side_effect=ModuleNotFoundError), patch("analysis.text_stats.log_failure") as log:
            result = text_statistics(frame_of([("我", 1, "晚安😀")]))
        self.assertEqual(result["total"], {"chinese_chars": 2, "all_chars": 3})
        self.assertTrue(result["warning"])
        self.assertEqual(result["words"], [])
        log.assert_called_once()
