"""网页调用的工作区与分析服务；不依赖 Streamlit。"""
import hashlib
import os
from datetime import date
from pathlib import Path
from threading import RLock

from core.contacts import load_contacts
from core.errors import UserError
from core.key_extract import extract_keys, verify_key
from core.messages import load_messages, resolve_self_username
from core.paths import WORKSPACE_ROOT, discover_data_path, message_databases
from core.refresh import refresh_workspace
from core.version import VERIFIED_VERSION, detect_version
from core.workspace import (
    create_copy, decrypt_workspace, load_keys, promote_pending, raw_databases,
    read_manifest, recover_interrupted_promotion, save_keys, validate_workspace,
)
from web.data import analysis_data, records
from web.preferences import Preferences
from web.tasks import BusyError, Tasks, error_message


class Service:
    def __init__(self, root: Path = WORKSPACE_ROOT):
        self.root = root
        self.lock = RLock()
        self.tasks = Tasks()
        self.preferences = Preferences(root / "preferences.json")
        self.active_info = {"valid": False, "error": "", "account": ""}
        self.pending_info = None
        self.contacts_cache = None
        self.message_cache = None
        self.active_signature = None
        self.version = detect_version() or ""

    def startup(self) -> None:
        self.tasks.submit("validate", self._startup)

    def _startup(self, progress) -> None:
        with self.lock:
            recover_interrupted_promotion()
            progress("正在检查已有分析副本……")
            if (self.root / "active").exists():
                try:
                    self._activate(verify=True)
                except Exception as error:
                    self.active_info["error"] = error_message("验证已有工作区", error)
                    raise
            self._pending()
            progress("本地数据库检查完成。")

    def _signature(self) -> tuple:
        active = self.root / "active"
        return tuple((str(path.relative_to(active)), path.stat().st_size, path.stat().st_mtime_ns)
                     for path in sorted(active.rglob("*")) if path.is_file())

    def _activate(self, verify: bool) -> None:
        active = self.root / "active"
        if verify:
            validate_workspace(active)
        manifest = read_manifest(active)
        normalized = os.path.normcase(str(Path(manifest["source"]).resolve()))
        self.active_info = {
            "valid": True, "error": "", "source": manifest["source"], "path": str(active),
            "copied_at": manifest["created_at"], "version": manifest["version"],
            "databases": list(manifest["decrypted"]), "reference_test": manifest.get("reference_test", False),
            "account": hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24],
        }
        self.active_signature = self._signature()
        self.contacts_cache = self.message_cache = None

    def _pending(self) -> None:
        pending = self.root / "pending"
        self.pending_info = None
        if not (pending / "metadata" / "manifest.json").is_file():
            return
        manifest = read_manifest(pending)
        verified = {}
        if manifest["stage"] in ("copied", "ready"):
            try:
                keys = load_keys(pending)
            except UserError:
                keys = {}
            verified = {name: verify_key(keys.get(name, b""), raw) for name, raw in raw_databases(pending).items()}
        self.pending_info = {
            "stage": manifest["stage"], "source": manifest["source"], "path": str(pending),
            "files": list(manifest["files"]), "keys": verified,
        }

    def status(self) -> dict:
        return {"version": self.version, "verified_version": VERIFIED_VERSION, "active": dict(self.active_info),
                "pending": self.pending_info, "task": self.tasks.snapshot(), "today": date.today().isoformat()}

    def _ready(self) -> None:
        if self.tasks.busy:
            raise BusyError("正在处理本地数据库，请等待完成。")
        if not self.active_info["valid"]:
            raise UserError("请先完成分析副本的创建、密钥提取和解密。")
        if self._signature() != self.active_signature:
            self.active_info["valid"] = False
            try:
                self._activate(verify=True)
            except Exception as error:
                self.active_info["error"] = error_message("验证工作区变化", error)
                raise UserError(self.active_info["error"]) from None

    def contacts(self) -> list[dict]:
        if self.tasks.busy:
            raise BusyError("正在处理本地数据库，请等待完成。")
        with self.lock:
            self._ready()
            if self.contacts_cache is None:
                self.contacts_cache = load_contacts(self.root / "active" / "decrypted" / "contact.db")
            return records(self.contacts_cache[["username", "label", "display_name", "nick_name", "wechat_id"]])

    def analyze(self, payload: dict) -> dict:
        if self.tasks.busy:
            raise BusyError("正在处理本地数据库，请等待完成。")
        with self.lock:
            available = self.contacts()
            username = payload.get("username")
            if username not in {item["username"] for item in available}:
                raise UserError("所选联系人不在当前通讯录中，请重新选择。")
            active = self.root / "active"
            databases = message_databases(active / "decrypted")
            if self.message_cache is None or self.message_cache[0] != username:
                self_username = resolve_self_username(databases, self.active_info["source"])
                if self_username is None:
                    raise UserError("无法确认本账号发送者映射，请从实际微信账号目录重新初始化。")
                self.message_cache = (username, load_messages(databases, username, self_username))
            return analysis_data(self.message_cache[1], payload, len(databases))

    def submit(self, payload: dict) -> dict:
        if self.tasks.busy:
            raise BusyError("已有任务正在运行，请等待完成。")
        action = payload.get("action")
        if action not in ("copy", "extract", "decrypt", "refresh"):
            raise UserError("未知的数据库操作。")
        version = payload.get("version") or self.version or VERIFIED_VERSION
        if not isinstance(version, str) or len(version) > 50:
            raise UserError("微信版本无效。")
        source = discover_data_path(payload.get("source", "")) if action == "copy" else None

        def operation(progress) -> None:
            with self.lock:
                try:
                    pending = self.root / "pending"
                    if action == "copy":
                        create_copy(source, version, progress)
                    elif action == "extract":
                        databases = raw_databases(pending)
                        try:
                            keys = {name: value for name, value in load_keys(pending).items()
                                    if name in databases and verify_key(value, databases[name])}
                        except UserError:
                            keys = {}
                        missing = {name: raw for name, raw in databases.items() if name not in keys}
                        keys.update(extract_keys(missing, progress) if missing else {})
                        save_keys(pending, keys)
                        unresolved = [name for name, raw in databases.items() if not verify_key(keys.get(name, b""), raw)]
                        if unresolved:
                            raise UserError("以下数据库未找到有效 key：" + "、".join(unresolved))
                    elif action == "decrypt":
                        decrypt_workspace(pending, progress)
                        load_contacts(pending / "decrypted" / "contact.db")
                        if resolve_self_username(message_databases(pending / "decrypted"), read_manifest(pending)["source"]) is None:
                            raise UserError("无法确认新副本的本账号发送者映射，未替换 active。")
                        promote_pending()
                        self._activate(verify=False)
                    else:
                        refresh_workspace(version, progress)
                        self._activate(verify=False)
                finally:
                    self._pending()
        return self.tasks.submit(action, operation)
