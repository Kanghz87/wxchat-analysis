from dataclasses import dataclass
from pathlib import Path
import re

from core.errors import UserError

PROJECT_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = PROJECT_DIR / "workspace"
MESSAGE_NAME = re.compile(r"message_(\d+)\.db\Z", re.IGNORECASE)


@dataclass(frozen=True)
class DataPaths:
    root: Path
    databases: tuple[Path, ...]

    def files(self) -> tuple[Path, ...]:
        files: list[Path] = []
        for db in self.databases:
            files.append(db)
            for suffix in ("-wal", "-shm"):
                sidecar = db.with_name(db.name + suffix)
                if sidecar.is_file():
                    files.append(sidecar)
        return tuple(files)


def message_databases(folder: Path) -> list[Path]:
    return sorted(
        (p for p in folder.glob("message_*.db") if p.is_file() and MESSAGE_NAME.fullmatch(p.name)),
        key=lambda p: int(MESSAGE_NAME.fullmatch(p.name).group(1)),
    )


def discover_data_path(value: str | Path) -> DataPaths:
    path = Path(str(value).strip().strip('"')).expanduser().resolve()
    if not path.is_dir():
        raise UserError("微信数据目录不存在，请检查路径。")
    root = path / "db_storage" if (path / "db_storage").is_dir() else path
    contact = root / "contact" / "contact.db"
    missing: list[str] = []
    if not contact.is_file():
        missing.append("未找到 contact/contact.db")
    messages = message_databases(root / "message")
    if not messages:
        missing.append("当前目录中没有 message/message_*.db")
    if missing:
        raise UserError("；".join(missing) + "。请输入账号目录或 db_storage 目录。")
    return DataPaths(root, (contact, *messages))
