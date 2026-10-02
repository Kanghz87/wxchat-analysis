from pathlib import Path
import sqlite3


def _text_factory(value: bytes) -> str | bytes:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value


def open_readonly(path: Path) -> sqlite3.Connection:
    """仅用于已经独立解密、不会再变化的 workspace 主数据库。"""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    connection.text_factory = _text_factory
    return connection
