"""仅使用合成数据的 MVP 核心回归；不会读取真实微信或密钥。"""
from datetime import datetime, date
from contextlib import closing
import hashlib
import hmac
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import zstandard

from analysis.basic import filter_dates, summarize, daily_counts, hourly_counts, weekday_hour_counts, longest_text
from core.contacts import load_contacts
from core.decrypt import check_integrity, decrypt_database, decrypt_page
from core.errors import UserError
from core.key_extract import verify_key
from core.messages import conversation_table, load_messages, resolve_self_username
from core.paths import discover_data_path, message_databases
from core.text import decode_blob_text
from core.message_types import REFERENCE_TYPE
from analysis.basic import message_type_counts, reference_counts


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="wechat_analyzer_test_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_paths_account_and_storage_with_chinese_spaces(self) -> None:
        account = self.root / "中文 空格账号"
        storage = account / "db_storage"
        (storage / "contact").mkdir(parents=True)
        (storage / "message").mkdir()
        for relative in ("contact/contact.db", "message/message_0.db", "message/message_12.db", "message/message_12.db-wal"):
            (storage / relative).write_bytes(b"synthetic")
        first = discover_data_path(account)
        self.assertEqual(first, discover_data_path(storage))
        self.assertEqual([p.name for p in first.databases], ["contact.db", "message_0.db", "message_12.db"])
        self.assertEqual(len(first.files()), 4)
        with self.assertRaises(UserError):
            discover_data_path(self.root)

    def test_32_and_48_byte_keys_and_page_header(self) -> None:
        # 公开的合成测试值，不是真实数据库 key；48-byte salt 特意不等于文件头。
        key, salt, iv = bytes(range(32)), b"S" * 16, b"I" * 16
        plaintext = b"P" * 4000
        for length in (32, 48):
            with self.subTest(length=length):
                material = key if length == 32 else key + salt
                header = salt if length == 32 else b"H" * 16
                encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
                encrypted = encryptor.update(plaintext) + encryptor.finalize()
                mac_key = hashlib.pbkdf2_hmac("sha512", key, bytes(v ^ 0x3A for v in salt), 2, dklen=32)
                digest = hmac.new(mac_key, encrypted + iv + struct.pack("<I", 1), hashlib.sha512).digest()
                page = header + encrypted + iv + digest
                path = self.root / f"synthetic_{length}.db"
                path.write_bytes(page)
                self.assertTrue(verify_key(material, path))
                self.assertFalse(verify_key(b"wrong".ljust(32, b"!"), path))
                self.assertEqual(decrypt_page(material, page, 1), b"SQLite format 3\x00" + plaintext + b"\x00" * 80)
                destination = self.root / "existing_decrypted.db"
                destination.write_bytes(b"previous valid output sentinel")
                # 合成页 HMAC 正确但不是合法 SQLite；完整性失败必须保留旧输出。
                with self.assertRaises((sqlite3.DatabaseError, UserError)):
                    decrypt_database(path, destination, material)
                self.assertEqual(destination.read_bytes(), b"previous valid output sentinel")
                self.assertFalse(destination.with_name(destination.name + ".tmp").exists())
                if length == 48:
                    self.assertFalse(verify_key(key, path))

    def test_text_and_zstd(self) -> None:
        text = "你好，长文字😀" * 1000
        encoded = text.encode("utf-8")
        compressed = zstandard.ZstdCompressor().compress(encoded)
        self.assertEqual(decode_blob_text(text), text)
        self.assertEqual(decode_blob_text(encoded), text)
        self.assertEqual(decode_blob_text(compressed), text)
        self.assertEqual(decode_blob_text(b"\xff\xfe" + encoded + b"\x01\x00\x00"), text)
        self.assertEqual(decode_blob_text(b"\x00\x01" + encoded + b"\x01\x00\x00"), text)

    def test_contacts_name_priority_and_duplicates(self) -> None:
        path = self.root / "contact.db"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE contact (username TEXT, nick_name TEXT, remark TEXT, alias TEXT, local_type INTEGER, flag INTEGER, delete_flag INTEGER, verify_flag INTEGER)")
            connection.executemany("INSERT INTO contact VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
                ("first", "同名", "备注", "my_wechat_id", 1, 3, 0, 0),
                ("second", "同名", "", "", 1, 134219779, 0, 0),
                ("third", "", "", "", 1, 1, 0, 0),
                ("work_friend", "企业好友", "", "", 5, 65537, 0, 0),
                ("stranger", "群成员", "", "", 3, 4, 0, 0),
                ("group@chatroom", "群聊", "", "", 2, 2, 0, 0),
                ("gh_public", "公众号", "", "", 1, 3, 0, 8),
                ("filehelper", "文件传输助手", "", "", 1, 3, 0, 0),
                ("removed", "已删除", "", "", 1, 3, 1, 0),
                ("not_added", "未添加", "", "", 0, 0, 0, 0),
            ])
        connection.close()
        check_integrity(path)
        contacts = load_contacts(path).set_index("username")
        self.assertEqual(contacts.loc["first", "display_name"], "备注")
        self.assertEqual(contacts.loc["second", "display_name"], "同名")
        self.assertEqual(contacts.loc["third", "display_name"], "third")
        self.assertEqual(set(contacts.index), {"first", "second", "third", "work_friend"})
        self.assertEqual(contacts.loc["first", "label"], "备注 - 同名 - my_wechat_id")
        self.assertEqual(contacts.loc["second", "label"], "未备注 - 同名 - second")
        self.assertEqual(contacts.loc["third", "wechat_id"], "third")

    def make_messages(self) -> list[Path]:
        table = conversation_table("synthetic_user")
        first = int(datetime(2026, 9, 1, 22, 0).timestamp())
        second = int(datetime(2026, 9, 2, 0, 0).timestamp())
        compressed = zstandard.ZstdCompressor().compress(("长文字😀" * 200).encode("utf-8"))
        records = [
            [(1, 1, 3, first, "你好😀", None), (2, 3, 8, first + 1, "", None)],
            [(1, 1, 2, second, "[文本]", compressed), (2, 1, 17, second + 3600, "再见", None)],
        ]
        paths = []
        for index, rows in enumerate(records):
            path = self.root / f"message_{index}.db"
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE Name2Id (user_name TEXT PRIMARY KEY, is_session INTEGER)")
                owner_id, peer_id = (3, 8) if index == 0 else (17, 2)
                connection.executemany("INSERT INTO Name2Id (rowid, user_name, is_session) VALUES (?, ?, ?)", [
                    (owner_id, "synthetic_self", 0), (peer_id, "synthetic_user", 1),
                ])
                # 故意不包含 sort_seq，覆盖可选字段缺失。
                connection.execute(f'CREATE TABLE "{table}" (local_id INTEGER, local_type INTEGER, real_sender_id INTEGER, create_time INTEGER, message_content TEXT, compress_content BLOB)')
                connection.executemany(f'INSERT INTO "{table}" VALUES (?, ?, ?, ?, ?, ?)', rows)
            connection.close()
            paths.append(path)
        return paths

    def test_cross_shard_dataframe_and_long_text(self) -> None:
        paths = self.make_messages()
        self.assertEqual(resolve_self_username(paths, self.root / "synthetic_self_abcd" / "db_storage"), "synthetic_self")
        self.assertIsNone(resolve_self_username(paths, self.root / "another_account" / "db_storage"))
        frame = load_messages(paths, "synthetic_user", "synthetic_self")
        self.assertEqual(len(frame), 4)
        self.assertEqual(frame["source_db"].nunique(), 2)
        self.assertEqual(frame["char_count"].max(), len("长文字😀" * 200))
        self.assertEqual(len(longest_text(frame)), 3)
        self.assertEqual(int(longest_text(frame).iloc[0]["字符数"]), 800)
        self.assertEqual(frame.attrs["warnings"], [])
        self.assertEqual(frame["sender"].tolist(), ["我", "对方", "对方", "我"])

    def test_history_before_2025_in_additional_shard(self) -> None:
        self.make_messages()
        table = conversation_table("synthetic_user")
        path = self.root / "message_12.db"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE Name2Id (user_name TEXT PRIMARY KEY)")
            connection.execute("INSERT INTO Name2Id (rowid, user_name) VALUES (1, 'synthetic_self')")
            connection.execute(f'CREATE TABLE "{table}" (local_type INTEGER, real_sender_id INTEGER, create_time INTEGER, message_content TEXT)')
            connection.execute(f'INSERT INTO "{table}" VALUES (?, ?, ?, ?)', (1, 1, int(datetime(2022, 7, 2, 10).timestamp()), "历史测试"))
        connection.close()
        frame = load_messages(message_databases(self.root), "synthetic_user", "synthetic_self")
        self.assertEqual(frame["date"].min(), date(2022, 7, 2))
        older = filter_dates(frame, date(2022, 1, 1), date(2024, 12, 31))
        self.assertEqual(summarize(older)["total"], 1)
        self.assertEqual(older.iloc[0]["source_db"], "message_12.db")

    def test_date_filter_metrics_and_chart_totals(self) -> None:
        frame = load_messages(self.make_messages(), "synthetic_user", "synthetic_self")
        stats = summarize(frame)
        self.assertEqual((stats["total"], stats["mine"], stats["other"], stats["active_days"], stats["text_count"]), (4, 2, 2, 2, 3))
        self.assertEqual(stats["busiest_day"], date(2026, 9, 1))
        self.assertEqual(stats["busiest_hour"], 22)
        filtered = filter_dates(frame, date(2026, 9, 2), date(2026, 9, 2))
        self.assertEqual(summarize(filtered)["total"], 2)
        self.assertEqual(daily_counts(filtered, date(2026, 9, 2), date(2026, 9, 2))["总计"].sum(), 2)
        self.assertEqual(hourly_counts(filtered)["消息数量"].sum(), 2)
        self.assertEqual(weekday_hour_counts(filtered).to_numpy().sum(), 2)
        self.assertEqual(len(longest_text(filtered)), 2)
        empty = filter_dates(frame, date(2020, 1, 1), date(2020, 1, 1))
        self.assertEqual(summarize(empty)["total"], 0)
        self.assertEqual(weekday_hour_counts(empty).shape, (7, 24))

    def test_unmapped_and_system_senders_do_not_become_other(self) -> None:
        paths = self.make_messages()
        table = conversation_table("synthetic_user")
        timestamp = int(datetime(2026, 9, 1, 22, 30).timestamp())
        with sqlite3.connect(paths[0]) as connection:
            # 第一库的 2 并不对应任何人，不能再用固定编号猜测本人。
            connection.executemany(f'INSERT INTO "{table}" VALUES (?, ?, ?, ?, ?, ?)', [
                (3, 1, 2, timestamp, "未知发送者", None),
                (4, 10000, 999, timestamp, "", None),
            ])
        connection.close()
        frame = load_messages(paths, "synthetic_user", "synthetic_self")
        stats = summarize(frame)
        self.assertEqual((stats["total"], stats["mine"], stats["other"]), (6, 2, 2))
        self.assertEqual((frame["sender"] == "系统").sum(), 1)
        self.assertEqual((frame["sender"] == "未识别").sum(), 1)
        self.assertTrue(any("1 条消息未能确认发送者" in message for message in frame.attrs["warnings"]))
        daily = daily_counts(frame, date(2026, 9, 1), date(2026, 9, 2))
        self.assertEqual(daily["总计"].sum(), 6)
        self.assertEqual(daily["我"].sum(), 2)
        self.assertEqual(daily["对方"].sum(), 2)

    def test_reference_replies_use_own_body_not_quoted_message(self) -> None:
        paths = self.make_messages()
        table = conversation_table("synthetic_user")
        quoted = "被引用的原始长文字" * 2000
        text = f'<msg><appmsg><title>回复文字😀</title><type>57</type><refermsg><type>47</type><content>{quoted}</content></refermsg></appmsg></msg>'
        emoji = '<msg><appmsg><title><![CDATA[<msg><emoji md5="synthetic"/></msg>]]></title><type>57</type><refermsg><type>1</type><content>原文</content></refermsg></appmsg></msg>'
        plain49 = '<msg><appmsg><title>补充</title><type>57</type><refermsg><type>3</type></refermsg></appmsg></msg>'
        timestamp = int(datetime(2026, 9, 3, 12).timestamp())
        with closing(sqlite3.connect(paths[0])) as connection:
            connection.executemany(f'INSERT INTO "{table}" VALUES (?, ?, ?, ?, ?, ?)', [
                (3, REFERENCE_TYPE, 3, timestamp, "[文本]", zstandard.ZstdCompressor().compress(text.encode())),
                (4, REFERENCE_TYPE, 8, timestamp + 1, emoji, None),
                (5, REFERENCE_TYPE, 3, timestamp + 2, "invalid XML", None),
                (6, 49, 8, timestamp + 3, plain49, None),
            ])
            connection.commit()
        frame = load_messages(paths, "synthetic_user", "synthetic_self")
        replies = frame[frame["is_reference"]]
        self.assertEqual(replies["message_type"].tolist(), ["文字", "表情", "其他", "文字"])
        self.assertEqual(replies["content"].tolist(), ["回复文字😀", "", "", "补充"])
        self.assertEqual(reference_counts(frame), {"total": 4, "mine": 2, "other": 2})
        self.assertEqual(summarize(frame)["text_count"], 5)
        self.assertNotIn("引用消息", message_type_counts(frame)["消息类型"].tolist())
        self.assertEqual(longest_text(frame)["字符数"].max(), 800)
        self.assertTrue(any("引用回复正文未能解析" in warning for warning in frame.attrs["warnings"]))


if __name__ == "__main__":
    unittest.main()
