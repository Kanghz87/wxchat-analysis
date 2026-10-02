"""复用 longest_messages.py 的 UTF-8、zstd 和容器解码。"""
import re

import zstandard as zstd


def decode_blob_text(data: str | bytes | None) -> str:
    if data is None:
        return ""

    if isinstance(data, str):
        return data.strip()

    if not isinstance(data, bytes):
        return str(data).strip()

    # 1. 普通 UTF-8
    try:
        text = data.decode("utf-8")
        if text.strip() and not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
            return text.strip()
    except UnicodeDecodeError:
        pass

    # 2. 微信 4.x 长文本：ZSTD
    if data[:4] == b"\x28\xb5\x2f\xfd":
        try:
            dctx = zstd.ZstdDecompressor()
            text = dctx.decompress(
                data,
                max_output_size=2_000_000
            ).decode("utf-8", errors="ignore")

            if text.strip():
                return text.strip()
        except Exception:
            pass

    # 3. 某些文字是“容器头 + UTF-8 正文 + 填充”
    for offset in range(0, min(16, len(data))):
        chunk = data[offset:]

        if b"\x01\x00" in chunk:
            chunk = chunk.split(b"\x01\x00", 1)[0]

        try:
            text = chunk.decode("utf-8")

            text = re.sub(
                r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]+",
                "",
                text
            ).strip()

            if text:
                return text
        except UnicodeDecodeError:
            continue

    return ""
