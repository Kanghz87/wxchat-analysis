"""更新流程回归：使用临时 SQLite；替代加密/进程边界，不访问真实微信。"""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from core.decrypt import check_integrity
from core.errors import UserError
from core.messages import conversation_table, load_messages
from core.paths import discover_data_path, message_databases
from core.refresh import refresh_workspace
from core.workspace import (
    create_copy, file_hash, load_keys, promote_pending, raw_databases,
    read_manifest, save_keys, save_manifest,
)


class RefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="wechat_refresh_test_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.source = self.root / "中文 账号" / "synthetic_self_abcd" / "db_storage"
        self.enterContext(patch("core.workspace.WORKSPACE_ROOT", self.workspace))
        self.enterContext(patch("core.refresh.WORKSPACE_ROOT", self.workspace))
        (self.source / "contact").mkdir(parents=True)
        (self.source / "message").mkdir()
        with closing(sqlite3.connect(self.source / "contact" / "contact.db")) as connection:
            connection.execute("CREATE TABLE contact (username TEXT, local_type INTEGER, nick_name TEXT, remark TEXT)")
            connection.execute("INSERT INTO contact VALUES ('synthetic_peer', 1, '测试好友', '备注')")
            connection.commit()
        self.add_message_database("message_0.db")
        (self.source / "contact" / "contact.db-wal").write_bytes(b"synthetic sidecar")
        self.current_keys = {"contact.db": b"C" * 32 + b"S" * 16, "message_0.db": b"M" * 32}
        pending = create_copy(discover_data_path(self.source), "4.1.13.65")
        save_keys(pending, self.current_keys)
        self.decrypt_sqlite_fixture(pending)
        self.active = promote_pending()
        self.verify = self.enterContext(patch("core.refresh.verify_key", side_effect=self.verify_copy))
        self.decrypt = self.enterContext(patch("core.refresh.decrypt_workspace", side_effect=self.decrypt_sqlite_fixture))
        self.extract = self.enterContext(patch("core.refresh.extract_keys", return_value={}))

    def add_message_database(self, name: str) -> None:
        with closing(sqlite3.connect(self.source / "message" / name)) as connection:
            connection.execute("CREATE TABLE Name2Id (user_name TEXT PRIMARY KEY)")
            connection.executemany("INSERT INTO Name2Id (rowid, user_name) VALUES (?, ?)", [(3, "synthetic_self"), (8, "synthetic_peer")])
            table = conversation_table("synthetic_peer")
            connection.execute(f'CREATE TABLE "{table}" (local_id INTEGER, local_type INTEGER, real_sender_id INTEGER, create_time INTEGER, message_content TEXT)')
            connection.execute(f'INSERT INTO "{table}" VALUES (1, 1, 3, ?, ?)', (int(datetime(2026, 9, 20).timestamp()), "合成文字"))
            connection.commit()

    def append_message(self) -> None:
        with closing(sqlite3.connect(self.source / "message" / "message_0.db")) as connection:
            table = conversation_table("synthetic_peer")
            connection.execute(f'INSERT INTO "{table}" VALUES (2, 1, 8, ?, ?)', (int(datetime(2026, 9, 30).timestamp()), "新增合成文字"))
            connection.commit()

    def decrypt_sqlite_fixture(self, folder: Path, progress=lambda _: None) -> None:
        # 这里测试编排和替换；32/48-byte 实际解密算法由 test_core 覆盖。
        manifest = read_manifest(folder)
        hashes = {}
        for name, raw in raw_databases(folder).items():
            plain = folder / "decrypted" / name
            shutil.copyfile(raw, plain)
            check_integrity(plain)
            hashes[name] = file_hash(plain)
        manifest.update(stage="ready", decrypted=hashes)
        save_manifest(folder, manifest)

    def verify_copy(self, key: bytes, path: Path) -> bool:
        self.assertTrue(path.resolve().is_relative_to(self.workspace / "pending" / "raw"))
        return key == self.current_keys[path.name]

    def fingerprint(self, folder: Path) -> dict:
        return {str(path.relative_to(folder)): (file_hash(path), path.stat().st_mtime_ns)
                for path in folder.rglob("*") if path.is_file()}

    def message_count(self, folder: Path) -> int:
        return len(load_messages(message_databases(folder / "decrypted"), "synthetic_peer", "synthetic_self"))

    def test_refresh_reuses_keys_and_reloads_new_messages(self) -> None:
        self.append_message()
        original = self.fingerprint(self.source)
        old = self.fingerprint(self.active)
        result = refresh_workspace("")
        self.assertEqual(result, self.active)
        self.extract.assert_not_called()
        self.assertEqual(self.message_count(self.active), 2)
        self.assertEqual(self.fingerprint(self.source), original)
        self.assertEqual(self.fingerprint(self.workspace / "previous"), old)
        self.assertEqual(read_manifest(self.active)["version"], "4.1.13.65")
        self.assertEqual((self.active / "raw/contact/contact.db-wal").read_bytes(), b"synthetic sidecar")
        self.append_message()
        refresh_workspace("4.1.13.65")
        self.assertEqual(self.message_count(self.active), 3)
        self.assertEqual(self.message_count(self.workspace / "previous"), 2)
        self.assertFalse((self.workspace / "pending").exists())
        self.assertEqual({path.name for path in self.workspace.iterdir()}, {"active", "previous"})

    def test_new_shard_and_rotated_key_are_extracted_automatically(self) -> None:
        self.add_message_database("message_12.db")
        self.current_keys.update({"message_0.db": b"R" * 32 + b"T" * 16, "message_12.db": b"N" * 32})
        missing = {"message_0.db", "message_12.db"}
        self.extract.return_value = {name: self.current_keys[name] for name in missing}
        refresh_workspace("4.1.13.65")
        self.assertEqual(set(self.extract.call_args.args[0]), missing)
        self.assertEqual(set(raw_databases(self.active)), set(self.current_keys))
        self.assertEqual(load_keys(self.active), self.current_keys)
        self.assertEqual(self.message_count(self.active), 2)

    def test_failed_update_keeps_active_and_previous(self) -> None:
        refresh_workspace("4.1.13.65")
        active_before = self.fingerprint(self.active)
        previous_before = self.fingerprint(self.workspace / "previous")
        self.add_message_database("message_12.db")
        self.current_keys["message_12.db"] = b"N" * 32
        # 提取未获得全部密钥时不能解密或替换。
        self.decrypt.reset_mock()
        with self.assertRaisesRegex(UserError, "message_12.db"):
            refresh_workspace("4.1.13.65")
        self.decrypt.assert_not_called()
        self.assertEqual(self.fingerprint(self.active), active_before)
        self.assertEqual(self.fingerprint(self.workspace / "previous"), previous_before)
        # 完整性检查失败也必须继续保留原来的两个工作区。
        self.extract.return_value = {"message_12.db": self.current_keys["message_12.db"]}
        self.decrypt.side_effect = UserError("message_12.db integrity_check 未通过")
        with self.assertRaisesRegex(UserError, "integrity_check"):
            refresh_workspace("4.1.13.65")
        self.assertEqual(self.fingerprint(self.active), active_before)
        self.assertEqual(self.fingerprint(self.workspace / "previous"), previous_before)


if __name__ == "__main__":
    unittest.main()
