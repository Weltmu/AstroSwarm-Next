# -*- coding: utf-8 -*-
"""纯标准库读 PNG 尺寸（测试里不想给 .test_venv 添 Pillow 依赖）。"""
import struct
import zlib


def png_size(data: bytes):
    """返回 (width, height, ok)。ok=False 表示签名或 IHDR 校验不过。"""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return (0, 0, False)
    if len(data) < 33 or data[12:16] != b"IHDR":
        return (0, 0, False)
    w, h = struct.unpack(">II", data[16:24])
    # IHDR 是第一个块：长度 13 + 类型 + 数据 + CRC，CRC 也校验一下
    (length,) = struct.unpack(">I", data[8:12])
    if length != 13:
        return (w, h, False)
    crc = struct.unpack(">I", data[29:33])[0]
    if zlib.crc32(data[12:29]) & 0xFFFFFFFF != crc:
        return (w, h, False)
    return (w, h, True)
