from collections.abc import Sequence
from contextlib import closing
from datetime import datetime
import hashlib
from pathlib import Path
import sqlite3

import pandas as pd

from core.sqlite_utils import open_readonly
from core.text import decode_blob_text
from core.logging_utils import log_failure
from core.message_types import MESSAGE_PARSE_VERSION, REFERENCE_TYPE, message_type_label, reference_reply

FRAME_COLUMNS = [
    "timestamp", "date", "hour", "weekday", "sender", "local_type",
    "content", "char_count", "sort_seq", "local_id", "source_db", "message_type", "is_reference",
]
WANTED = ("local_id", "local_type", "real_sender_id", "create_time", "message_content", "compress_content", "sort_seq")
REQUIRED = {"local_type", "real_sender_id", "create_time"}
SENDER_RESOLUTION_VERSION = 2


def conversation_table(username: str) -> str:
    return "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()


def sender_mapping(connection: sqlite3.Connection) -> dict[int, str]:
    """real_sender_id 对应当前分片 Name2Id 的 rowid，不是固定的收发方向。"""
    columns = {row[1] for row in connection.execute('PRAGMA table_info("Name2Id")')}
    if "user_name" not in columns:
        return {}
    return {
        row[0]: row[1] for row in connection.execute('SELECT rowid, user_name FROM "Name2Id"')
        if isinstance(row[1], str) and row[1]
    }


def resolve_self_username(databases: Sequence[Path], source: str | Path) -> str | None:
    """从账号目录取候选 username，并用实际映射表核实；无法确认时不猜 ID=2。"""
    source_path = Path(source)
    account_folder = source_path.parent.name if source_path.name.lower() == "db_storage" else source_path.name
    candidates = {account_folder, account_folder.rsplit("_", 1)[0]}
    matches: set[str] = set()
    for database in databases:
        with closing(open_readonly(database)) as connection:
            matches.update(set(sender_mapping(connection).values()) & candidates)
    return next(iter(matches)) if len(matches) == 1 else None


def classify_sender(sender_id: int, local_type: int, mapping: dict[int, str], username: str, self_username: str | None) -> str:
    sender_username = mapping.get(sender_id)
    if self_username and sender_username == self_username:
        return "我"
    if sender_username == username:
        return "对方"
    if local_type == 10000:
        return "系统"
    return "未识别"


def load_messages(databases: Sequence[Path], username: str, self_username: str | None = None) -> pd.DataFrame:
    table = conversation_table(username)
    records = []
    warnings = []
    bad_times = undecoded = unknown_senders = unparsed_replies = 0
    for database in databases:
        try:
            with closing(open_readonly(database)) as connection:
                columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                if not columns:
                    continue
                missing = REQUIRED - columns
                if missing:
                    warnings.append(f"{database.name} 会话表缺少 {', '.join(sorted(missing))}，已跳过该分片，统计不完整。")
                    continue
                mapping = sender_mapping(connection)
                if not mapping:
                    warnings.append(f"{database.name} 缺少有效发送者映射，无法确认的发送者会标记为“未识别”。")
                selected = [name for name in WANTED if name in columns]
                sql = "SELECT " + ", ".join(f'"{name}"' for name in selected) + f' FROM "{table}"'
                for row in connection.execute(sql):
                    item = dict(row)
                    try:
                        timestamp = datetime.fromtimestamp(item["create_time"])
                        # 保持 pandas 纳秒时间戳可表示范围，避免极端损坏值。
                        pd.Timestamp(timestamp).as_unit("ns")
                    except (ValueError, TypeError, OSError, OverflowError):
                        bad_times += 1
                        continue
                    content = ""
                    kind = message_type_label(item["local_type"])
                    is_reference = item["local_type"] == REFERENCE_TYPE
                    if item["local_type"] == 1:
                        plain = decode_blob_text(item.get("message_content"))
                        compressed = decode_blob_text(item.get("compress_content"))
                        content = compressed if len(compressed) > len(plain) else plain
                        undecoded += not bool(content)
                    elif item["local_type"] in (REFERENCE_TYPE, 49):
                        reply = reference_reply(item.get("message_content"), item.get("compress_content"))
                        if reply is not None:
                            kind, content = reply
                            is_reference = True
                        elif is_reference:
                            kind = "其他"
                            unparsed_replies += 1
                    sender = classify_sender(item["real_sender_id"], item["local_type"], mapping, username, self_username)
                    unknown_senders += sender == "未识别"
                    records.append({
                        "timestamp": timestamp, "date": timestamp.date(),
                        "hour": timestamp.hour, "weekday": timestamp.weekday(),
                        "sender": sender,
                        "local_type": item["local_type"], "content": content,
                        "char_count": len(content), "sort_seq": item.get("sort_seq") or 0,
                        "local_id": item.get("local_id") or 0, "source_db": database.name,
                        "message_type": kind, "is_reference": is_reference,
                    })
        except sqlite3.DatabaseError as error:
            log_failure("读取消息分片", error)
            warnings.append(f"{database.name} 读取失败，统计不完整；请重新初始化。")
    frame = pd.DataFrame.from_records(records, columns=FRAME_COLUMNS)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    if not frame.empty:
        frame = frame.sort_values(["timestamp", "sort_seq", "source_db", "local_id"], kind="stable").reset_index(drop=True)
    if bad_times:
        warnings.append(f"{bad_times} 条消息时间无效，已排除；统计不完整。")
    if undecoded:
        warnings.append(f"{undecoded} 条文字消息未能解析正文；已计入消息数量，不列入最长文字消息。")
    if unknown_senders:
        warnings.append(f"{unknown_senders} 条消息未能确认发送者；标记为“未识别”，计入总消息，但不计入我或对方发送数量。")
    if unparsed_replies:
        warnings.append(f"{unparsed_replies} 条引用回复正文未能解析；已计入引用次数，消息类型归为“其他”。")
    frame.attrs["warnings"] = warnings
    frame.attrs["self_username"] = self_username
    frame.attrs["sender_resolution_version"] = SENDER_RESOLUTION_VERSION
    frame.attrs["message_parse_version"] = MESSAGE_PARSE_VERSION
    return frame
