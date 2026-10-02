"""一个 active、一个 pending、最多一个 previous；只在工作区写入。"""
from collections.abc import Callable
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil

from core.errors import UserError
from core.paths import DataPaths, WORKSPACE_ROOT, discover_data_path

Progress = Callable[[str], None]


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def owned(path: Path) -> Path:
    root = WORKSPACE_ROOT.resolve()
    if WORKSPACE_ROOT.is_symlink() or WORKSPACE_ROOT.is_junction():
        raise UserError("workspace 不支持符号链接或目录联接。")
    absolute = path.resolve()
    if not absolute.is_relative_to(root) or absolute == root:
        raise UserError("拒绝写入工作区以外的路径。")
    if path.is_symlink() or path.is_junction():
        raise UserError("工作区路径不能是符号链接或目录联接。")
    return absolute


def _remove_slot(name: str) -> None:
    if name not in {"pending", "previous"}:
        raise UserError("无效的临时工作区。")
    target = owned(WORKSPACE_ROOT / name)
    if target.exists():
        # 删除前检查最终绝对路径；仅限这两个固定目录。
        shutil.rmtree(target)


def write_json(path: Path, data: dict) -> None:
    path = owned(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = owned(path.with_suffix(path.suffix + ".tmp"))
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_manifest(folder: Path) -> dict:
    return json.loads((folder / "metadata" / "manifest.json").read_text(encoding="utf-8"))


def save_manifest(folder: Path, manifest: dict) -> None:
    write_json(folder / "metadata" / "manifest.json", manifest)


def raw_databases(folder: Path) -> dict[str, Path]:
    result = {}
    for relative in read_manifest(folder)["databases"]:
        path = owned(folder / "raw" / relative)
        if not path.is_relative_to((folder / "raw").resolve()):
            raise UserError("工作区数据库清单无效，请重新初始化。")
        result[path.name] = path
    return result


def create_copy(source: DataPaths, version: str, progress: Progress = lambda _: None) -> Path:
    root = WORKSPACE_ROOT.resolve()
    files = source.files()
    if source.root.resolve().is_relative_to(root) or root.is_relative_to(source.root.resolve()):
        raise UserError("微信源目录与程序工作区不能相互包含。")
    if any(p.resolve().is_relative_to(root) for p in files):
        raise UserError("微信源文件不能指向程序工作区。")
    pending = owned(WORKSPACE_ROOT / "pending")
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    _remove_slot("pending")
    (pending / "decrypted").mkdir(parents=True)
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in files}
    manifest = {
        "source": str(source.root), "version": version,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "stage": "copying", "files": {},
        "databases": [p.relative_to(source.root).as_posix() for p in source.databases],
    }
    save_manifest(pending, manifest)
    for original in files:
        relative = original.relative_to(source.root)
        destination = owned(pending / "raw" / relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
        manifest["files"][relative.as_posix()] = {
            "size": destination.stat().st_size, "sha256": file_hash(destination),
        }
        progress(f"已复制 {original.name}")
    current = discover_data_path(source.root)
    if set(current.files()) != set(files) or any(
        not p.is_file() or before[p] != (p.stat().st_size, p.stat().st_mtime_ns) for p in files
    ):
        raise UserError("复制期间微信数据库发生变化，请暂停收发消息后重新创建分析副本。")
    manifest["stage"] = "copied"
    save_manifest(pending, manifest)
    return pending


def save_keys(folder: Path, keys: dict[str, bytes]) -> None:
    write_json(folder / "metadata" / "keys.json", {name: value.hex() for name, value in keys.items()})


def load_keys(folder: Path) -> dict[str, bytes]:
    try:
        values = json.loads((folder / "metadata" / "keys.json").read_text(encoding="utf-8"))
        return {name: bytes.fromhex(value) for name, value in values.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        raise UserError("工作区密钥文件缺失或无效，请重新提取数据库密钥。") from None


def validate_workspace(folder: Path) -> None:
    from core.decrypt import check_integrity
    from core.key_extract import verify_key

    manifest = read_manifest(folder)
    if manifest.get("stage") != "ready":
        raise UserError("工作区尚未完成解密。")
    keys = load_keys(folder)
    for name, raw in raw_databases(folder).items():
        relative = raw.relative_to(folder / "raw").as_posix()
        if file_hash(raw) != manifest["files"][relative]["sha256"]:
            raise UserError(f"{name} 原始副本已变化，请重新初始化。")
        if not verify_key(keys.get(name, b""), raw):
            raise UserError(f"{name} 的已保存 key 验证失败，请重新初始化。")
        plain = owned(folder / "decrypted" / name)
        if file_hash(plain) != manifest["decrypted"][name]:
            raise UserError(f"{name} 解密文件已变化，请重新初始化。")
        check_integrity(plain)


def decrypt_workspace(folder: Path, progress: Progress = lambda _: None) -> None:
    from core.decrypt import decrypt_database

    manifest = read_manifest(folder)
    if manifest.get("stage") not in {"copied", "ready"}:
        raise UserError("请先成功创建分析副本。")
    keys = load_keys(folder)
    databases = raw_databases(folder)
    missing = [name for name in databases if name not in keys]
    if missing:
        raise UserError("以下数据库尚未找到有效 key：" + "、".join(missing))
    hashes = {}
    for name, raw in databases.items():
        destination = owned(folder / "decrypted" / name)
        decrypt_database(raw, destination, keys[name], progress)
        hashes[name] = file_hash(destination)
    manifest["stage"] = "ready"
    manifest["decrypted"] = hashes
    save_manifest(folder, manifest)


def promote_pending() -> Path:
    pending = owned(WORKSPACE_ROOT / "pending")
    active = owned(WORKSPACE_ROOT / "active")
    previous = owned(WORKSPACE_ROOT / "previous")
    if read_manifest(pending).get("stage") != "ready":
        raise UserError("临时工作区未完成解密，不能替换 active。")
    _remove_slot("previous")
    if active.exists():
        active.rename(previous)
    try:
        pending.rename(active)
    except OSError:
        if previous.exists() and not active.exists():
            previous.rename(active)
        raise
    return active


def recover_interrupted_promotion() -> None:
    active = owned(WORKSPACE_ROOT / "active")
    previous = owned(WORKSPACE_ROOT / "previous")
    if not active.exists() and previous.exists():
        previous.rename(active)
