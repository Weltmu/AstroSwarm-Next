# -*- coding: utf-8 -*-
"""图片小工具：压缩、取尺寸。

为什么放在主程序侧：插件的沙箱里既没有 Pillow，也没有随便读写文件的权限，
"把这张图压小一点再发"这类活只能主程序代劳（G3）。

不依赖 nonebot，纯文件操作；Pillow 缺失时返回一句人话而不是崩。
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_MAX_SIDE = 1280
DEFAULT_QUALITY = 82


def _pillow():
    try:
        from PIL import Image  # noqa: F401

        return __import__("PIL.Image", fromlist=["Image"])
    except Exception:  # noqa: BLE001
        return None


def compress_image_file(source, max_side: int = DEFAULT_MAX_SIDE,
                        quality: int = DEFAULT_QUALITY, out_dir=None) -> dict:
    """把一张图片按最长边缩放并重新压成 JPEG。

    返回 {"ok": True, "path": ..., "bytes": ..., "width": ..., "height": ...}
    或 {"ok": False, "error": ...}（错误码是人话原因，调用方直接展示）。
    """
    Image = _pillow()
    if Image is None:
        return {"ok": False, "error": "unsupported",
                "detail": "机器人环境里没装图片处理组件（Pillow），装一下就能用"}
    src = Path(str(source or ""))
    if not src.exists():
        return {"ok": False, "error": "not_found", "path": str(src)[:200]}
    max_side = max(64, min(int(max_side or DEFAULT_MAX_SIDE), 4096))
    quality = max(30, min(int(quality or DEFAULT_QUALITY), 95))
    try:
        with Image.open(src) as img:
            img = img.convert("RGB")
            width, height = img.size
            scale = min(1.0, max_side / float(max(width, height) or 1))
            if scale < 1.0:
                img = img.resize((max(1, int(width * scale)), max(1, int(height * scale))))
            target_dir = Path(out_dir) if out_dir else src.parent
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / (src.stem + f"_s{max_side}.jpg")
            img.save(target, format="JPEG", quality=quality, optimize=True)
            new_size = target.stat().st_size
            return {"ok": True, "action": "compress_image", "path": str(target),
                    "bytes": new_size, "width": img.size[0], "height": img.size[1],
                    "source_bytes": src.stat().st_size}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "compress_failed", "detail": str(exc)[:160]}


def image_size(path) -> dict:
    """取图片尺寸（不支持的格式返回 error，不抛）。"""
    Image = _pillow()
    if Image is None:
        return {"ok": False, "error": "unsupported"}
    try:
        with Image.open(Path(str(path))) as img:
            return {"ok": True, "width": img.size[0], "height": img.size[1],
                    "format": str(img.format or "")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "read_failed", "detail": str(exc)[:160]}
