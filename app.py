from pathlib import Path

import streamlit as st

from core.contacts import load_contacts
from core.errors import UserError
from core.key_extract import extract_keys, verify_key
from core.logging_utils import log_failure
from core.messages import SENDER_RESOLUTION_VERSION, load_messages, resolve_self_username
from core.message_types import MESSAGE_PARSE_VERSION
from core.paths import WORKSPACE_ROOT, discover_data_path, message_databases
from core.refresh import refresh_workspace
from core.version import VERIFIED_VERSION, detect_version
from core.workspace import (
    create_copy, decrypt_workspace, load_keys, promote_pending, raw_databases,
    read_manifest, recover_interrupted_promotion, save_keys, validate_workspace,
)
from ui.components import WAL_NOTICE, render_analysis
from ui.components import DATE_MIN, date_max
from ui.theme import render_theme_toggle


def show_error(operation: str, error: Exception) -> None:
    try:
        log_failure(operation, error)
    except OSError:
        pass
    if isinstance(error, UserError):
        st.error(str(error))
    elif isinstance(error, PermissionError):
        st.error(f"{operation}失败：权限不足或文件被占用，请检查目录权限与微信运行权限。")
    elif isinstance(error, OSError):
        st.error(f"{operation}失败：文件无法读写，请检查路径、剩余磁盘空间及文件是否被占用。")
    else:
        st.error(f"{operation}失败，详细调用栈已写入 workspace/logs/app.log。")


def signature(folder: Path) -> tuple:
    return tuple(
        (str(p.relative_to(folder)), p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(folder.rglob("*")) if p.is_file()
    )


def clear_analysis_cache(keep_contact: bool = False) -> None:
    for key in ("contacts", "message_cache", "start_date", "end_date", "date_contact", "chart_date_scope"):
        st.session_state.pop(key, None)
    if not keep_contact:
        st.session_state.pop("selected_contact", None)


def active_is_valid(active: Path) -> bool:
    if not active.exists():
        return False
    try:
        current = signature(active)
        if st.session_state.get("active_signature") != current:
            with st.spinner("正在验证已有工作区……"):
                validate_workspace(active)
            st.session_state["active_signature"] = current
            clear_analysis_cache()
        return True
    except Exception as error:
        show_error("验证已有工作区", error)
        return False


def render_refresh_button(version: str) -> None:
    if not st.button("更新聊天记录", key="refresh_records", type="primary", width="stretch"):
        return
    with st.status("正在更新聊天记录……", expanded=True) as status:
        try:
            active = refresh_workspace(version, st.write)
        except Exception as error:
            status.update(label="更新未完成，继续使用原有分析副本", state="error")
            show_error("更新聊天记录", error)
            return
        status.update(label="更新完成，全部数据库通过完整性检查", state="complete")
    clear_analysis_cache(keep_contact=True)
    # 新副本刚完成逐库完整性检查，直接刷新签名，避免再次全量验证。
    st.session_state["active_signature"] = signature(active)
    st.session_state["initializing"] = False
    st.session_state["refresh_notice"] = "聊天记录已更新，已重新加载当前联系人；日期范围重置为全部可用记录。"
    st.rerun()


def initialize(version: str) -> None:
    active = WORKSPACE_ROOT / "active"
    pending = WORKSPACE_ROOT / "pending"
    default_path = ""
    if active.exists():
        try:
            manifest = read_manifest(active)
            if not manifest.get("reference_test"):
                default_path = manifest["source"]
        except (OSError, ValueError, KeyError):
            pass
    value = st.text_input("微信数据路径", value=default_path, placeholder=r"D:\...\xwechat_files\<account>\db_storage")
    source = None
    if value.strip():
        try:
            source = discover_data_path(value)
            st.caption("检测到：" + "、".join(p.name for p in source.databases))
        except UserError as error:
            st.error(str(error))
    if st.button("创建分析副本", disabled=source is None, width="stretch"):
        try:
            with st.status("创建隔离副本", expanded=True) as status:
                create_copy(source, version, st.write)
                status.update(label="副本已创建", state="complete")
        except Exception as error:
            show_error("创建分析副本", error)
    if not (pending / "metadata" / "manifest.json").is_file():
        return
    try:
        manifest = read_manifest(pending)
        if manifest.get("stage") not in {"copied", "ready"}:
            st.warning("上次复制未完成，请重新创建分析副本。")
            return
        st.caption(f'原始路径：{manifest["source"]}')
        st.caption(f"工作副本：{pending}")
        with st.expander("已复制文件"):
            for relative in manifest["files"]:
                st.text(relative)
        if st.button("提取数据库密钥", width="stretch"):
            try:
                with st.status("只读提取数据库密钥", expanded=True) as status:
                    databases = raw_databases(pending)
                    try:
                        keys = {name: value for name, value in load_keys(pending).items() if name in databases and verify_key(value, databases[name])}
                    except UserError:
                        keys = {}
                    keys.update(extract_keys(databases, st.write))
                    save_keys(pending, keys)
                    status.update(label="密钥扫描完成", state="complete")
            except Exception as error:
                show_error("提取数据库密钥", error)
        verified = {}
        if (pending / "metadata" / "keys.json").is_file():
            keys = load_keys(pending)
            for name, raw in raw_databases(pending).items():
                verified[name] = verify_key(keys.get(name, b""), raw)
                st.caption(f"{'✓' if verified[name] else '❌'} {name}：{'key 已验证' if verified[name] else '未找到有效 key'}")
        all_verified = bool(verified) and all(verified.values())
        if st.button("解密数据库", disabled=not all_verified, width="stretch"):
            try:
                with st.status("解密数据库", expanded=True) as status:
                    decrypt_workspace(pending, st.write)
                    load_contacts(pending / "decrypted" / "contact.db")
                    promote_pending()
                    status.update(label="全部数据库通过 integrity_check", state="complete")
                st.session_state.pop("active_signature", None)
                st.session_state["initializing"] = False
                st.rerun()
            except Exception as error:
                show_error("解密数据库", error)
    except Exception as error:
        show_error("读取临时工作区", error)


def analyze(active: Path) -> None:
    manifest = read_manifest(active)
    copied_at = manifest["created_at"].replace("T", " ")
    st.caption(f"分析副本创建时间：{copied_at}（本机时间）。需要重新复制并解析时，请点击页面顶部“更新聊天记录”。")
    if "contacts" not in st.session_state or "label" not in st.session_state["contacts"].columns:
        st.session_state["contacts"] = load_contacts(active / "decrypted" / "contact.db")
    contacts = st.session_state["contacts"]
    if contacts.empty:
        st.info("当前副本中没有找到通讯录好友。")
        return
    labels = dict(zip(contacts["username"], contacts["label"]))
    if st.session_state.get("selected_contact") not in labels:
        st.session_state.pop("selected_contact", None)
    username = st.selectbox("联系人（搜索备注、微信昵称或微信号）", list(labels), format_func=labels.get, key="selected_contact")
    st.caption(f"仅显示通讯录好友，共 {len(contacts)} 位；显示格式：备注 - 微信昵称 - 微信号。")
    databases = message_databases(active / "decrypted")
    self_username = resolve_self_username(databases, manifest["source"])
    if self_username is None:
        st.error("无法从工作区来源与数据库映射确认本账号，不能可靠区分双方。请从实际微信账号目录重新初始化。")
        return
    cache = st.session_state.get("message_cache")
    if (
        cache is None or cache[0] != username
        or cache[1].attrs.get("self_username") != self_username
        or cache[1].attrs.get("sender_resolution_version") != SENDER_RESOLUTION_VERSION
        or cache[1].attrs.get("message_parse_version") != MESSAGE_PARSE_VERSION
    ):
        with st.spinner("正在加载该联系人的所有分片……"):
            frame = load_messages(databases, username, self_username)
        st.session_state["message_cache"] = (username, frame)
    else:
        frame = cache[1]
    for warning in frame.attrs.get("warnings", []):
        st.warning(warning)
    if frame.empty:
        st.info("没有找到该联系人的可用聊天记录。")
        return
    first, last = frame["date"].min(), frame["date"].max()
    st.caption(f"已读取 {len(databases)} 个消息分片；当前联系人的可用记录：{first:%Y-%m-%d} 至 {last:%Y-%m-%d}。")
    if st.session_state.get("date_contact") != username:
        st.session_state["start_date"], st.session_state["end_date"] = first, last
        st.session_state["date_contact"] = username
    st.subheader("统一日期范围")
    st.caption("修改这里会同步指标、消息类型统计、三个图表和最长消息；也可以在每个图表上方单独调整该图的日期。")
    left, right = st.columns(2)
    start = left.date_input("起始日期", min_value=min(DATE_MIN, first), max_value=date_max(last), key="start_date", format="YYYY-MM-DD")
    end = right.date_input("结束日期", min_value=min(DATE_MIN, first), max_value=date_max(last), key="end_date", format="YYYY-MM-DD")
    if start is None or end is None:
        st.warning("请选择完整的起止日期。")
        return
    if start > end:
        st.warning("起始日期不能晚于结束日期。")
        return
    if start < first or end > last:
        st.info(f"在当前分析副本中，所选联系人的可用消息时间为 {first:%Y-%m-%d} 至 {last:%Y-%m-%d}。这是该联系人的记录范围，不是整个副本的时间范围，也不是复制时间；超出部分没有找到该联系人的可统计消息。")
    render_analysis(frame, start, end, context=username)


def main() -> None:
    st.set_page_config(page_title="微信聊天记录分析", page_icon="💬", layout="wide")
    st.title("微信聊天记录分析")
    render_theme_toggle()
    st.caption("仅分析用户本人电脑上的微信本地数据。所有数据在本机处理，不上传聊天记录；原始微信数据库只读，分析针对 workspace 副本。")
    st.info(WAL_NOTICE)
    if notice := st.session_state.pop("refresh_notice", None):
        st.success(notice)
    active = WORKSPACE_ROOT / "active"
    if "auto_version" not in st.session_state:
        st.session_state["auto_version"] = detect_version() or ""
        try:
            recover_interrupted_promotion()
        except Exception as error:
            show_error("恢复工作区", error)
    valid = active_is_valid(active)
    with st.expander("本地数据库", expanded=not valid or st.session_state.get("initializing", False)):
        detected = st.session_state["auto_version"]
        version_column, workspace_column, action_column = st.columns([1, 2, 1])
        with version_column:
            st.caption(f"自动检测：{detected or '未能识别，请手动输入'}")
            version = st.text_input("微信版本（可手动修改）", value=detected)
            if version != VERIFIED_VERSION:
                st.warning("未验证版本：第一版仅在 4.1.13.65 上验证，其他版本可以尝试。")
        if valid:
            manifest = read_manifest(active)
            with workspace_column:
                st.success("分析副本可用")
                st.caption(f'原始路径：{manifest["source"]}')
                st.caption(f"工作副本：{active}")
                st.caption(f'复制时间：{manifest["created_at"].replace("T", " ")} · 数据版本：{manifest["version"]}')
                if manifest.get("reference_test"):
                    st.caption("当前为参考测试数据副本；更新数据时请输入实际微信账号目录。")
                with st.expander(f'数据库状态 · {len(manifest["decrypted"])} 个库检查通过'):
                    for name in manifest["decrypted"]:
                        st.caption(f"✓ {name} integrity_check = ok")
            with action_column:
                render_refresh_button(version)
                if st.button("重新初始化", width="stretch"):
                    st.session_state["initializing"] = True
                st.caption("更新会重新复制并解析。建议保持微信登录、暂停收发消息；失败时保留原副本。")
        if not valid or st.session_state.get("initializing", False):
            if valid:
                st.caption("新工作区通过解密检查后替换 active，最多保留一个 previous。")
            initialize(version)
    if valid:
        try:
            analyze(active)
        except Exception as error:
            show_error("加载聊天记录", error)
    else:
        st.write("请在页面顶部依次创建分析副本、提取数据库密钥、解密数据库。提取时请保持微信登录。")


if __name__ == "__main__":
    main()
