from contextlib import closing
from pathlib import Path

import pandas as pd

from core.errors import UserError
from core.sqlite_utils import open_readonly

SYSTEM_CONTACTS = {
    "filehelper", "weixin", "fmessage", "newsapp", "medianote", "floatbottle",
    "qqmail", "tmessage", "qmessage", "masssendapp", "feedsapp", "weibo",
    "facebookapp", "notification_messages", "brandsessionholder",
}


def load_contacts(database: Path) -> pd.DataFrame:
    with closing(open_readonly(database)) as connection:
        columns = {row[1] for row in connection.execute('PRAGMA table_info("contact")')}
        if "username" not in columns:
            raise UserError("contact.db 缺少 contact 表或 username 字段，当前数据库结构可能不兼容。")
        if "local_type" not in columns:
            raise UserError("contact.db 缺少联系人类型字段，无法区分通讯录好友与非好友，请使用已验证版本的数据。")
        names = ("username", "nick_name", "remark", "alias")
        selected = [f'"{name}"' if name in columns else f"NULL AS {name}" for name in names]
        # 4.1.13.65：1 为普通已添加联系人，5 为已添加的企业微信联系人。
        # 3 是群内非好友；不能把整个 contact 缓存表作为通讯录。
        conditions = ["local_type IN (1, 5)", "username NOT LIKE '%@chatroom'", "username NOT GLOB 'gh_*'"]
        if "flag" in columns:
            conditions.append("(COALESCE(flag, 0) & 1) = 1")
        if "delete_flag" in columns:
            conditions.append("COALESCE(delete_flag, 0) = 0")
        if "verify_flag" in columns:
            conditions.append("COALESCE(verify_flag, 0) = 0")
        rows = connection.execute(
            "SELECT " + ", ".join(selected) + ' FROM "contact" WHERE ' + " AND ".join(conditions),
        ).fetchall()
    frame = pd.DataFrame.from_records([tuple(row) for row in rows], columns=names)
    frame = frame.fillna("")
    frame = frame[frame["username"].map(lambda value: isinstance(value, str) and bool(value.strip()))].copy()
    frame = frame[~frame["username"].str.lower().isin(SYSTEM_CONTACTS)].copy()
    for name in ("remark", "nick_name", "alias"):
        frame[name] = frame[name].map(lambda value: value.strip() if isinstance(value, str) else "")
    frame["display_name"] = frame["remark"].where(frame["remark"] != "", frame["nick_name"])
    frame["display_name"] = frame["display_name"].where(frame["display_name"] != "", frame["username"])
    frame["wechat_id"] = frame["alias"].where(frame["alias"] != "", frame["username"])
    frame["label"] = (
        frame["remark"].where(frame["remark"] != "", "未备注") + " - "
        + frame["nick_name"].where(frame["nick_name"] != "", "未设置昵称") + " - "
        + frame["wechat_id"]
    )
    return frame.drop_duplicates("username").sort_values(["display_name", "username"]).reset_index(drop=True)
