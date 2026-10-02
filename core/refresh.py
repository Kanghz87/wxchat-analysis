"""沿用已验证的复制、密钥验证和解密流程，一次完成工作区更新。"""
from pathlib import Path

from core.contacts import load_contacts
from core.errors import UserError
from core.key_extract import extract_keys, verify_key
from core.messages import resolve_self_username
from core.paths import WORKSPACE_ROOT, discover_data_path, message_databases
from core.workspace import (
    Progress, create_copy, decrypt_workspace, load_keys, owned, promote_pending,
    raw_databases, read_manifest, save_keys,
)


def refresh_workspace(version: str, progress: Progress = lambda _: None) -> Path:
    active = owned(WORKSPACE_ROOT / "active")
    manifest = read_manifest(active)
    if manifest.get("reference_test") or not manifest.get("source"):
        raise UserError("当前副本没有可更新的微信账号路径，请通过“重新初始化”选择实际微信目录。")
    source = discover_data_path(manifest["source"])
    try:
        saved = load_keys(active)
    except UserError:
        saved = {}

    progress("1/4 重新扫描微信目录，复制联系人、全部消息分片及 WAL/SHM……")
    pending = create_copy(source, version.strip() or manifest.get("version", ""), progress)
    databases = raw_databases(pending)
    progress("2/4 对新副本逐库验证已保存的密钥……")
    keys = {}
    for name, database in databases.items():
        key = saved.get(name, b"")
        if verify_key(key, database):
            keys[name] = key
            progress(f"✓ {name} 已保存密钥仍有效")
    save_keys(pending, keys)
    missing = {name: path for name, path in databases.items() if name not in keys}
    if missing:
        progress("检测到新增分片或密钥变化，正在自动提取；请保持对应账号的电脑版微信登录。")
        extracted = extract_keys(missing, progress)
        for name, database in missing.items():
            key = extracted.get(name, b"")
            if verify_key(key, database):
                keys[name] = key
        save_keys(pending, keys)
    unresolved = [name for name in databases if name not in keys]
    if unresolved:
        raise UserError("以下数据库未找到有效 key：" + "、".join(unresolved) + "。请确认对应账号已登录后重试。")

    progress("3/4 解密新副本并逐库执行 integrity_check……")
    decrypt_workspace(pending, progress)
    load_contacts(pending / "decrypted" / "contact.db")
    if resolve_self_username(message_databases(pending / "decrypted"), source.root) is None:
        raise UserError("新副本无法确认本账号的发送者映射，未替换当前数据。请检查微信账号目录。")
    progress("4/4 检查通过，替换当前副本并重新加载分析……")
    return promote_pending()
