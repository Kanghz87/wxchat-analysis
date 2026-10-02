"""四项独立记忆设置；只保存偏好，不保存聊天正文或密钥。"""
from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path
from threading import Lock

from core.errors import UserError
from core.workspace import write_json

OPTIONS = ("theme", "contact", "dates", "viewports")
CHARTS = ("global", "daily", "hourly", "heatmap")


def defaults() -> dict:
    return {"remember": dict.fromkeys(OPTIONS, False), "accounts": {}}


class Preferences:
    def __init__(self, path: Path):
        self.path = path
        self.lock = Lock()

    def read(self) -> dict:
        with self.lock:
            return self._read()

    def _read(self) -> dict:
        if not self.path.exists():
            return defaults()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
                raise ValueError()
            if not isinstance(data.get("remember"), dict):
                raise ValueError()
            for name in OPTIONS:
                if not isinstance(data["remember"].get(name), bool):
                    raise ValueError()
            if data.get("theme", "light") not in ("light", "dark"):
                raise ValueError()
            if not all(isinstance(account, dict) for account in data["accounts"].values()):
                raise ValueError()
            return data
        except (OSError, ValueError, TypeError):
            return {**defaults(), "warning": "界面设置无法读取，已使用默认值；重新保存设置即可恢复。"}

    def save(self, payload: dict, account: str) -> dict:
        with self.lock:
            result = self._read()
            result.pop("warning", None)
            flags = payload.get("remember", {})
            if not isinstance(flags, dict) or any(not isinstance(flags.get(key), bool) for key in OPTIONS):
                raise UserError("四项记忆选项必须为开启或关闭。")
            result["remember"] = {key: flags[key] for key in OPTIONS}
            theme = payload.get("theme", "light")
            if theme not in ("light", "dark"):
                raise UserError("无效的主题。")
            if flags["theme"]:
                result["theme"] = theme
            else:
                result.pop("theme", None)
            for saved in result["accounts"].values():
                if not flags["contact"]:
                    saved.pop("selected_contact", None)
                views = saved.get("views", {})
                if isinstance(views, dict):
                    for view in views.values():
                        if isinstance(view, dict):
                            for key in ("dates", "viewports"):
                                if not flags[key]:
                                    view.pop(key, None)
            username = payload.get("username", "")
            if not isinstance(username, str) or len(username) > 256:
                raise UserError("联系人标识无效。")
            if account and username:
                saved = result["accounts"].setdefault(account, {})
                if flags["contact"]:
                    saved["selected_contact"] = username
                view = saved.setdefault("views", {}).setdefault(username, {})
                if flags["dates"]:
                    view["dates"] = self._dates(payload.get("dates", {}))
                if flags["viewports"]:
                    view["viewports"] = self._viewports(payload.get("viewports", {}))
            # 清除关闭选项产生的空记录，避免累积无效联系人条目。
            for key, saved in list(result["accounts"].items()):
                saved["views"] = {name: view for name, view in saved.get("views", {}).items() if view}
                if not saved["views"]:
                    saved.pop("views")
                if not saved:
                    del result["accounts"][key]
            write_json(self.path, result)
            return deepcopy(result)

    @staticmethod
    def _dates(value: dict) -> dict:
        if not isinstance(value, dict):
            raise UserError("日期设置无效。")
        result = {}
        for name, interval in value.items():
            if name not in CHARTS or not isinstance(interval, dict):
                raise UserError("日期设置无效。")
            try:
                start, end = date.fromisoformat(interval["start"]), date.fromisoformat(interval["end"])
                if start > end or start < date(1970, 1, 1) or end > date.today():
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                raise UserError("请填写有效日期，起始日期不能晚于结束日期，也不能选择未来日期。") from None
            result[name] = {"start": start.isoformat(), "end": end.isoformat()}
        return result

    @staticmethod
    def _viewports(value: dict) -> dict:
        if not isinstance(value, dict):
            raise UserError("图表缩放设置无效。")
        result = {}
        for name, bounds in value.items():
            if name not in ("daily", "hourly") or not isinstance(bounds, list) or len(bounds) != 2:
                raise UserError("图表缩放设置无效。")
            try:
                parsed = [datetime.fromisoformat(item.replace("Z", "+00:00")) for item in bounds]
                if parsed[0] >= parsed[1]:
                    raise ValueError()
            except (TypeError, ValueError, AttributeError):
                raise UserError("图表缩放设置无效。") from None
            result[name] = bounds
        return result
