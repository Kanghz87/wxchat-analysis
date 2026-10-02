"""迁移自 D:/wxchat/extract_keys.py；保留内存偏移、扫描和 HMAC 算法。"""
from __future__ import annotations
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

from core.errors import UserError
from core.version import weixin_processes

import ctypes
import hashlib
import hmac
import re
import struct
from ctypes import wintypes

import psutil


# =========================
# WeChat 4.x / SQLCipher 参数
# =========================

PAGE_SIZE = 4096
RESERVE_SIZE = 80

CONFIG_NAME = b"com.Tencent.WCDB.Config.Cipher"

CONFIG_XOR_MASK = bytes.fromhex(
    "d2c7442458020000004889442450488b"
    "450048844c2448488944254048584c24"
)

HEX_RE = re.compile(rb"[xX]'([0-9a-fA-F]{64,192})'")


# =========================
# Windows API
# =========================

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("_alignment1", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("_alignment2", wintypes.DWORD),
    ]


k32 = ctypes.WinDLL("kernel32", use_last_error=True)

k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
]

k32.ReadProcessMemory.restype = wintypes.BOOL
k32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]

k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(MBI),
    ctypes.c_size_t,
]

k32.CloseHandle.argtypes = [wintypes.HANDLE]


def read_memory(handle: int, address: int, size: int) -> bytes | None:
    if address <= 0 or size <= 0:
        return None

    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)

    ok = k32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        buf,
        size,
        ctypes.byref(read),
    )

    if not ok or read.value != size:
        return None

    return buf.raw[:read.value]


def readable_regions(handle: int) -> Iterator[tuple[int, int]]:
    addr = 0

    while True:
        mbi = MBI()

        r = k32.VirtualQueryEx(
            handle,
            ctypes.c_void_p(addr),
            ctypes.byref(mbi),
            ctypes.sizeof(mbi),
        )

        if r == 0:
            break

        base = mbi.BaseAddress or 0
        size = mbi.RegionSize

        readable = (
            mbi.State == MEM_COMMIT
            and ((mbi.Protect & 0xFF) & 0xE6)
            and not (mbi.Protect & PAGE_GUARD)
            and size > 0
        )

        if readable:
            yield base, size

        next_addr = base + size

        if next_addr <= addr:
            break

        addr = next_addr


def find_bytes(handle: int, needle: bytes) -> list[int]:
    """
    在进程的可读内存中查找字节串。
    分块读取，避免一次申请很大的内存。
    """

    hits = []
    chunk_size = 8 * 1024 * 1024

    for base, region_size in readable_regions(handle):

        offset = 0
        tail = b""

        while offset < region_size:
            size = min(chunk_size, region_size - offset)

            data = read_memory(handle, base + offset, size)

            if data is None:
                break

            combined = tail + data
            combined_base = base + offset - len(tail)

            pos = 0

            while True:
                pos = combined.find(needle, pos)

                if pos < 0:
                    break

                hits.append(combined_base + pos)
                pos += 1

            if len(needle) > 1:
                tail = combined[-(len(needle) - 1):]

            offset += size

    return list(set(hits))


# =========================
# SQLCipher key 验证
# =========================

def verify_key(key_material: bytes, db_path: Path) -> bool:
    """
    key_material:
        32 bytes -> 普通 raw key
        48 bytes -> raw key + 显式 salt
    """

    try:
        with open(db_path, "rb") as f:
            page = f.read(PAGE_SIZE)
    except OSError:
        return False

    if len(page) < PAGE_SIZE:
        return False

    if len(key_material) == 48:
        enc_key = key_material[:32]
        salt = key_material[32:48]
    elif len(key_material) == 32:
        enc_key = key_material
        salt = page[:16]
    else:
        return False

    mac_salt = bytes(x ^ 0x3A for x in salt)

    mac_key = hashlib.pbkdf2_hmac(
        "sha512",
        enc_key,
        mac_salt,
        2,
        dklen=32,
    )

    # SQLCipher page 1:
    # [salt/header][encrypted page + IV][HMAC]
    hmac_data = page[16: PAGE_SIZE - RESERVE_SIZE + 16]
    stored_hmac = page[PAGE_SIZE - 64:]

    calculated = hmac.new(
        mac_key,
        hmac_data,
        hashlib.sha512,
    )

    calculated.update(struct.pack("<I", 1))

    return hmac.compare_digest(
        calculated.digest(),
        stored_hmac,
    )


# =========================
# 从 Config.Cipher 中找候选 key
# =========================

def get_candidates(handle: int) -> set[bytes]:
    candidates = set()

    config_string_addresses = find_bytes(handle, CONFIG_NAME)



    for string_addr in config_string_addresses:

        # 内存中会保存：
        # [字符串地址][字符串长度]
        pair = (
            struct.pack("<Q", string_addr)
            + struct.pack("<Q", len(CONFIG_NAME))
        )

        references = find_bytes(handle, pair)

        for ref in references:

            node = read_memory(handle, ref - 0x10, 0x50)

            if not node or len(node) < 0x40:
                continue

            config_ptr = struct.unpack_from(
                "<Q", node, 0x28
            )[0]

            if not (
                0x10000
                <= config_ptr
                < 0x800000000000
            ):
                continue

            obj = read_memory(
                handle,
                config_ptr + 0x88,
                0x28,
            )

            if not obj or len(obj) < 0x18:
                continue

            data_ptr = struct.unpack_from(
                "<Q", obj, 0x08
            )[0]

            data_len = struct.unpack_from(
                "<Q", obj, 0x10
            )[0]

            if not (
                0 < data_len <= 1024
                and
                0x10000 <= data_ptr < 0x800000000000
            ):
                continue

            blob = read_memory(
                handle,
                data_ptr,
                int(data_len),
            )

            if not blob:
                continue

            # 微信对 Config.Cipher 的内容做了一层 XOR
            decoded = bytes(
                b ^ CONFIG_XOR_MASK[
                    i % len(CONFIG_XOR_MASK)
                ]
                for i, b in enumerate(blob)
            )

            for match in HEX_RE.finditer(decoded):

                run = match.group(1).decode("ascii")

                offsets = {0}

                # 某些配置字符串中可能连接了多个值
                if len(run) > 96:
                    offsets.update(
                        range(
                            0,
                            len(run) - 63,
                            32,
                        )
                    )

                for start in offsets:

                    if start + 64 > len(run):
                        continue

                    try:
                        raw_key = bytes.fromhex(
                            run[start:start + 64]
                        )

                        candidates.add(raw_key)

                        # 可能包含显式 16-byte salt
                        if start + 96 <= len(run):
                            key_with_salt = bytes.fromhex(
                                run[start:start + 96]
                            )
                            candidates.add(key_with_salt)

                    except ValueError:
                        pass

    return candidates


def extract_keys(
    databases: Mapping[str, Path],
    progress: Callable[[str], None] = lambda _: None,
) -> dict[str, bytes]:
    """候选只留在内存中；对每个副本独立验证，不输出密钥。"""
    processes = weixin_processes()
    if not processes:
        raise UserError("未检测到 Weixin.exe，请先登录电脑版微信。")
    found: dict[str, bytes] = {}
    opened = 0
    for process in processes:
        if len(found) == len(databases):
            break
        progress(f"正在只读扫描微信进程 {process.pid}，这可能需要几分钟……")
        handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, process.pid)
        if not handle:
            continue
        opened += 1
        try:
            candidates = get_candidates(handle)
            for candidate in sorted(candidates, key=len, reverse=True):
                for name, database in databases.items():
                    if name not in found and verify_key(candidate, database):
                        found[name] = candidate
                        progress(f"✓ {name} 密钥验证成功（{len(candidate)} bytes）")
        finally:
            k32.CloseHandle(handle)
    if not opened:
        raise UserError("无法读取 Weixin.exe：权限不足或进程已退出。请确认微信与本程序的运行权限一致。")
    if not found:
        raise UserError("未找到有效数据库 key。请确认微信已登录、所选目录属于当前账号；当前版本结构也可能不兼容。")
    return found
