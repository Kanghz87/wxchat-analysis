"""迁移自 D:/wxchat/decrypt_wechat_db.py；分页解密算法保持原样。"""
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
import os

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from core.errors import UserError
from core.key_extract import verify_key
from core.sqlite_utils import open_readonly

PAGE_SIZE = 4096
RESERVE_SIZE = 80


def check_integrity(path: Path) -> None:
    with path.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\x00":
            raise UserError(f"{path.name} 不是标准 SQLite 数据库。")
    with closing(open_readonly(path)) as connection:
        results = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    if results != ["ok"]:
        raise UserError(f"{path.name} integrity_check 未通过，请重新创建副本并解密。")


def decrypt_database(
    src: Path, dst: Path, key_material: bytes,
    progress: Callable[[str], None] = lambda _: None,
) -> None:
    if src.resolve() == dst.resolve():
        raise UserError("解密输出不能覆盖原始副本。")
    size = src.stat().st_size
    if size == 0 or size % PAGE_SIZE:
        raise UserError(f"{src.name} 文件大小不是有效的 4096 字节页，请重新复制。")
    if not verify_key(key_material, src):
        raise UserError(f"{src.name} 未找到有效 key。")
    dst.parent.mkdir(parents=True, exist_ok=True)
    temporary = dst.with_name(dst.name + ".tmp")
    progress(f"正在解密 {src.name}……")
    try:
        with src.open("rb") as source, temporary.open("wb") as target:
            for page_no in range(1, size // PAGE_SIZE + 1):
                page = source.read(PAGE_SIZE)
                if len(page) != PAGE_SIZE:
                    raise UserError(f"{src.name} 副本读取不完整。")
                target.write(decrypt_page(key_material, page, page_no))
            target.flush()
            os.fsync(target.fileno())
        check_integrity(temporary)
        temporary.replace(dst)
    finally:
        temporary.unlink(missing_ok=True)
    progress(f"✓ {dst.name} integrity_check = ok")


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    decryptor = Cipher(
        algorithms.AES(key),
        modes.CBC(iv)
    ).decryptor()

    return decryptor.update(data) + decryptor.finalize()


def decrypt_page(key_material: bytes, page: bytes, page_no: int) -> bytes:
    iv = page[
        PAGE_SIZE - RESERVE_SIZE:
        PAGE_SIZE - RESERVE_SIZE + 16
    ]

    if len(key_material) == 48:
        key = key_material[:32]

        if page_no == 1:
            encrypted = page[16:PAGE_SIZE - RESERVE_SIZE]

            decrypted = aes_cbc_decrypt(
                key,
                iv,
                encrypted
            )

            # 有些库前 16 字节本身就是 SQLite 明文头；
            # 有些虽然 key 带 explicit salt，
            # 但文件头不能直接照搬。
            if page[:16] == b"SQLite format 3\x00":
                header = page[:16]
            else:
                header = b"SQLite format 3\x00"

            return (
                header
                + decrypted
                + b"\x00" * RESERVE_SIZE
            )

    elif len(key_material) == 32:
        key = key_material

        if page_no == 1:
            encrypted = page[16:PAGE_SIZE - RESERVE_SIZE]

            return (
                b"SQLite format 3\x00"
                + aes_cbc_decrypt(
                    key,
                    iv,
                    encrypted
                )
                + b"\x00" * RESERVE_SIZE
            )

    else:
        raise ValueError(
            f"不支持的 key 长度：{len(key_material)}"
        )

    # 第 2 页以及以后
    encrypted = page[:PAGE_SIZE - RESERVE_SIZE]

    return (
        aes_cbc_decrypt(
            key,
            iv,
            encrypted
        )
        + b"\x00" * RESERVE_SIZE
    )
