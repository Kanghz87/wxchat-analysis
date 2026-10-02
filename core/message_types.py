"""微信 4.1 消息类型标签及引用回复正文；不解析媒体或支付内容。"""
import re
import xml.etree.ElementTree as ET

from core.text import decode_blob_text

REFERENCE_TYPE = (57 << 32) | 49
MESSAGE_PARSE_VERSION = 3

BASE_TYPES = {
    1: "文字", 3: "图片", 34: "语音", 42: "名片", 43: "视频",
    47: "表情", 48: "位置", 49: "应用消息（未细分）",
    50: "语音 / 视频通话", 66: "企业微信名片",
    10000: "系统消息", 11000: "系统通知",
}
APP_TYPES = {
    3: "音乐分享", 5: "链接", 6: "文件", 8: "表情",
    17: "位置共享", 19: "聊天记录分享", 24: "收藏笔记",
    33: "小程序", 36: "小程序", 51: "视频号", 57: "引用消息",
    62: "拍一拍", 63: "视频号", 76: "音乐分享",
    2000: "转账消息", 2001: "红包消息",
}


def message_type_label(local_type: int | None) -> str:
    try:
        value = int(local_type)
    except (TypeError, ValueError, OverflowError):
        return "其他 / 未识别"
    if value in BASE_TYPES:
        return BASE_TYPES[value]
    if value < 0:
        return "其他 / 未识别"
    # 在现有副本中核实：应用消息低 32 位为 49，高 32 位对应 XML 的 type。
    base, subtype = value & 0xFFFFFFFF, value >> 32
    if base == 49:
        return APP_TYPES.get(subtype, "其他应用消息")
    if base in (10000, 11000):
        return BASE_TYPES[base]
    return "其他 / 未识别"


def reference_reply(message_content: str | bytes | None, compress_content: str | bytes | None) -> tuple[str, str] | None:
    """只读取 appmsg 的本次回复，绝不以 refermsg 的原文或类型代替。"""
    texts = sorted(
        (decode_blob_text(value) for value in (message_content, compress_content)),
        key=len, reverse=True,
    )
    for text in texts:
        start = re.search(r"<(?:msg|appmsg)(?:\s|>)", text)
        if start is None:
            continue
        try:
            root = ET.fromstring(text[start.start():].strip("\x00 \r\n"))
        except ET.ParseError:
            continue
        appmsg = root if root.tag == "appmsg" else root.find("appmsg")
        if appmsg is None or appmsg.findtext("type", "").strip() != "57":
            continue
        title = appmsg.find("title")
        if title is None:
            title = appmsg.find("content")
        if title is None:
            return "其他", ""
        # 少数容器将本次回复的表情结构放入 title 子节点或 CDATA。
        if title.find(".//emoji") is not None:
            return "表情", ""
        reply = "".join(title.itertext()).strip()
        if reply.startswith("<"):
            try:
                own = ET.fromstring(reply)
            except ET.ParseError:
                own = None
            if own is not None and (own.tag == "emoji" or own.find(".//emoji") is not None):
                return "表情", ""
        return ("文字", reply) if reply else ("其他", "")
    return None
