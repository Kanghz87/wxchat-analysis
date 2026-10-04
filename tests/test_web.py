"""HTML 接口与偏好的最小回归，仅使用合成数据库。"""
from datetime import date
import json
from pathlib import Path
import shutil
import sqlite3
from threading import Event
import time
import unittest
from unittest.mock import patch

from tests import test_core as fixtures
from core.errors import UserError
from web.preferences import Preferences
from web.server import create_app
from web.service import Service
from core.workspace import file_hash, raw_databases, read_manifest, save_manifest
from core.decrypt import check_integrity


class WebTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.CoreTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root = fixture.root / "workspace"
        self.enterContext(patch("core.workspace.WORKSPACE_ROOT", self.root))
        plain = self.root / "active" / "decrypted"
        plain.mkdir(parents=True)
        for path in fixture.make_messages():
            shutil.copyfile(path, plain / path.name)
        with sqlite3.connect(plain / "contact.db") as connection:
            connection.execute("CREATE TABLE contact(username TEXT, nick_name TEXT, remark TEXT, local_type INTEGER)")
            connection.execute("INSERT INTO contact VALUES ('synthetic_user','测试名字','测试备注',1)")
        connection.close()
        manifest = {"source": str(fixture.root / "synthetic_self_abcd" / "db_storage"), "version": "4.1.13.65",
                    "created_at": "2026-09-20T12:00:00", "decrypted": {p.name: "fixture" for p in plain.iterdir()}}
        metadata = self.root / "active" / "metadata"
        metadata.mkdir()
        (metadata / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with patch("web.service.detect_version", return_value="4.1.13.65"):
            self.service = Service(self.root)
        self.service._activate(verify=False)
        self.app = create_app(self.service, initialize=False)
        self.client = self.app.test_client()

    def test_analysis_and_local_interfaces(self):
        contacts = self.client.get("/api/contacts").get_json()["contacts"]
        self.assertEqual(contacts[0]["label"], "测试备注 - 测试名字 - synthetic_user")
        self.assertEqual((contacts[0]["display_name"], contacts[0]["nick_name"], contacts[0]["wechat_id"]),
                         ("测试备注", "测试名字", "synthetic_user"))
        response = self.client.post("/api/analysis", json={"username": "synthetic_user"})
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual((result["metrics"]["total"], result["metrics"]["mine"], result["metrics"]["other"]), (4, 2, 2))
        self.assertEqual(result["longest"][0]["字符数"], 800)
        self.assertEqual(sum(result["charts"]["daily"]["counts"]["总计"]), 4)
        self.assertEqual(sum(map(sum, result["charts"]["hourly"]["hours"])), 4)
        self.assertEqual(result["charts"]["daily"]["bounds"][1], "2026-09-02T23:59:59.999")
        response = self.client.post("/api/analysis", json={"username": "synthetic_user", "global": {"start": "2026-09-02", "end": "2026-09-02"}})
        self.assertEqual(response.get_json()["metrics"]["total"], 2)
        empty = self.client.post("/api/analysis", json={"username": "synthetic_user", "global": {"start": "2020-01-01", "end": "2020-01-02"}}).get_json()
        self.assertEqual(empty["metrics"]["total"], 0)
        self.assertEqual(self.client.get("/api/status", headers={"Host": "external.example"}).status_code, 403)
        self.assertEqual(self.client.put("/api/preferences", json={}, headers={"Origin": "https://external.example"}).status_code, 403)
        self.assertNotIn("keys", self.client.get("/api/status").get_json()["active"])
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_four_independent_preferences_and_restart(self):
        payload = {"remember": dict.fromkeys(("theme", "contact", "dates", "viewports"), True),
                   "theme": "dark", "username": "synthetic_user",
                   "dates": {"global": {"start": "2026-09-01", "end": "2026-09-02"}},
                   "viewports": {"daily": ["2026-09-01", "2026-09-02T23:59:59.999"]}}
        self.assertEqual(self.client.put("/api/preferences", json=payload).status_code, 200)
        saved = Preferences(self.root / "preferences.json").read()
        account = saved["accounts"][self.service.active_info["account"]]
        self.assertEqual(saved["theme"], "dark")
        self.assertEqual(account["selected_contact"], "synthetic_user")
        self.assertEqual(account["views"]["synthetic_user"]["dates"], payload["dates"])
        payload["remember"].update(theme=False, dates=False)
        self.client.put("/api/preferences", json=payload)
        saved = self.service.preferences.read()
        self.assertNotIn("theme", saved)
        view = saved["accounts"][self.service.active_info["account"]]["views"]["synthetic_user"]
        self.assertNotIn("dates", view)
        self.assertIn("viewports", view)
        (self.root / "preferences.json").write_text("{broken", encoding="utf-8")
        self.assertIn("warning", self.service.preferences.read())

    def test_single_task_and_failed_refresh_keeps_active(self):
        entered, release = Event(), Event()
        def fail(version, progress):
            entered.set()
            release.wait(5)
            raise UserError("合成更新失败")
        with patch("web.service.refresh_workspace", side_effect=fail):
            response = self.client.post("/api/tasks", json={"action": "refresh"})
            self.assertEqual(response.status_code, 202)
            self.assertTrue(entered.wait(2))
            try:
                self.assertEqual(self.client.post("/api/tasks", json={"action": "refresh"}).status_code, 409)
                self.assertEqual(self.client.post("/api/analysis", json={"username": "synthetic_user"}).status_code, 409)
            finally:
                release.set()
            for _ in range(100):
                if not self.service.tasks.busy:
                    break
                time.sleep(.01)
        self.assertEqual(self.service.tasks.snapshot()["state"], "failed")
        self.assertTrue(self.service.active_info["valid"])
        self.assertEqual(self.client.post("/api/analysis", json={"username": "synthetic_user"}).get_json()["metrics"]["total"], 4)

    def test_copy_extract_decrypt_end_to_end_preserves_preferences_and_source(self):
        source = Path(self.service.active_info["source"])
        (source / "contact").mkdir(parents=True)
        (source / "message").mkdir()
        for plain in (self.root / "active" / "decrypted").glob("*.db"):
            folder = "contact" if plain.name == "contact.db" else "message"
            shutil.copyfile(plain, source / folder / plain.name)
        before = {str(path.relative_to(source)): file_hash(path) for path in source.rglob("*.db")}
        self.service.preferences.save({"remember": {"theme": True, "contact": False, "dates": False, "viewports": False}, "theme": "dark"}, "")
        keys = {name: b"M" * 32 for name in ("contact.db", "message_0.db", "message_1.db")}
        def decrypt_fixture(pending, progress):
            manifest = read_manifest(pending)
            hashes = {}
            for name, raw in raw_databases(pending).items():
                plain = pending / "decrypted" / name
                shutil.copyfile(raw, plain)
                check_integrity(plain)
                hashes[name] = file_hash(plain)
            manifest.update(stage="ready", decrypted=hashes)
            save_manifest(pending, manifest)
        def run(action):
            response = self.client.post("/api/tasks", json={"action": action, "source": str(source), "version": "4.1.13.65"})
            self.assertEqual(response.status_code, 202)
            for _ in range(200):
                if not self.service.tasks.busy:
                    break
                time.sleep(.01)
            self.assertEqual(self.service.tasks.snapshot()["state"], "complete")
        with patch("web.service.verify_key", side_effect=lambda key, _: len(key) == 32), patch("web.service.extract_keys", return_value=keys), patch("web.service.decrypt_workspace", side_effect=decrypt_fixture):
            run("copy")
            self.assertFalse(any(self.service.pending_info["keys"].values()))
            run("extract")
            self.assertTrue(all(self.service.pending_info["keys"].values()))
            run("decrypt")
        self.assertEqual(self.service.preferences.read()["theme"], "dark")
        self.assertTrue((self.root / "previous").exists())
        self.assertEqual(self.client.post("/api/analysis", json={"username": "synthetic_user"}).get_json()["metrics"]["total"], 4)
        self.assertEqual(before, {str(path.relative_to(source)): file_hash(path) for path in source.rglob("*.db")})
