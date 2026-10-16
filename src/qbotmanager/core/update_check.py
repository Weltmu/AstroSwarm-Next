# -*- coding: utf-8 -*-
"""版本检测：读官网下载清单，启动时 + 每 24 小时自动检查（结果落盘缓存）。

数据源是网站那份「一份文件驱动全站下载」的 manifest.json，加产品改版本只动它；
老接口 /license/version 仍作为兜底。返回结构对上层保持兼容
（ok/current/latest/url/notes/newer/cached/checked_at/error），
新增 sha256/size/filename 三个字段给「站内直接更新」用（先校验再装）。
"""
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

from ..constants import APP_NAME, APP_VERSION
from .license import SERVER_URL, SERVER_URL_FALLBACKS, SERVER_WEB

# 主数据源：官网下载清单；同一份文件也驱动下载页，改版本只改一处
MANIFEST_URLS = (SERVER_WEB.rstrip("/") + "/downloads/manifest.json",)
# 兜底：老的签发站版本接口（没有 sha256，只能提示「前往下载」）
UPDATE_URLS = tuple(u.rstrip("/") + "/version" for u in (SERVER_URL,) + tuple(SERVER_URL_FALLBACKS))
UPDATE_URL = UPDATE_URLS[0]          # 兼容旧引用
PRODUCT_ID = "astroswarm"
CHECK_INTERVAL = 1800   # 30 分钟一次（原来 24 小时，发了新版用户半天看不到）
# 启动那次由界面强制查（force=True），不吃缓存 —— 见 main_window._start_update_check
TIMEOUT = 8
_EMPTY = {
    "ok": False, "current": APP_VERSION, "latest": "",
    "url": "", "notes": "", "newer": False, "cached": False,
    "checked_at": 0, "error": "",
    "sha256": "", "size": 0, "filename": "", "source": "",
}

_state_cache = None


def _state_file() -> Path:
    base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / APP_NAME
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return base / "update_check.json"


def read_state() -> dict:
    """mtime 缓存读取检查状态（设置页 2 秒刷新热路径）。"""
    global _state_cache
    p = _state_file()
    try:
        st = p.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _state_cache is not None and _state_cache[0] == key:
        return _state_cache[1]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        data = {}
    _state_cache = (key, data)
    return data


def _write_state(data: dict):
    try:
        _state_file().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _parse(v) -> tuple:
    return tuple(int(x) for x in re.split(r"[^0-9]+", str(v or "")) if x.isdigit()) or (0,)


def newer_available(current: str, latest: str) -> bool:
    return bool(latest) and _parse(latest) > _parse(current)


def _os_key() -> str:
    """当前系统在清单里用的前缀：windows-* / linux-* / macos-*。"""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform.startswith("darwin"):
        return "macos"
    return ""


def pick_platform(product: dict) -> dict:
    """从产品条目里挑出本机该下的那个包：优先安装包，其次第一个同系统的。"""
    key = _os_key()
    fallback = None
    for plat in (product.get("platforms") or []):
        if not isinstance(plat, dict):
            continue
        pid = str(plat.get("id") or "")
        if key and not pid.startswith(key):
            continue
        if str(plat.get("kind") or "") == "installer" or "installer" in pid:
            return plat
        if fallback is None:
            fallback = plat
    return fallback or {}


def _absolute(url: str) -> str:
    """清单里写的是站内相对路径（/downloads/xxx.exe），补成完整地址。"""
    url = str(url or "").strip()
    if url.startswith("/"):
        return SERVER_WEB.rstrip("/") + url
    return url


def _notes_for(data: dict, latest: str) -> str:
    """更新说明：先找提到这个版本号的记录，找不到就退到最近的星群记录。"""
    ups = [u for u in (data.get("updates") or []) if isinstance(u, dict)]
    rows = [u for u in ups if latest and latest in str(u.get("text") or "")]
    if not rows:
        rows = [u for u in ups if "星群" in str(u.get("product") or "")]
    out = []
    for u in rows[-4:]:
        head = " ".join(x for x in (str(u.get("date") or ""), str(u.get("product") or "")) if x).strip()
        text = str(u.get("text") or "").strip()
        out.append(("· " + head + "：" + text) if head else ("· " + text))
    return "\n".join(out)


def _from_manifest(data: dict) -> dict:
    """清单 → 更新信息；没有星群产品时返回空 dict（调用方接着试兜底接口）。"""
    product = None
    for p in (data.get("products") or []):
        if isinstance(p, dict) and str(p.get("id") or "") == PRODUCT_ID:
            product = p
            break
    if not product:
        return {}
    plat = pick_platform(product)
    latest = str(plat.get("version") or "").strip()
    url = _absolute(plat.get("url") or "")
    if not latest or not url:
        return {}
    return {
        "latest": latest, "url": url, "notes": _notes_for(data, latest),
        "sha256": str(plat.get("sha256") or "").strip().lower(),
        "size": int(plat.get("size") or 0),
        "filename": str(plat.get("filename") or "").strip(),
        "source": "manifest",
    }


def _from_legacy(data: dict) -> dict:
    latest = str(data.get("version") or "").strip()
    if not latest:
        return {}
    return {
        "latest": latest, "url": _absolute(data.get("url") or ""),
        "notes": str(data.get("notes") or ""),
        "sha256": "", "size": 0, "filename": "", "source": "version",
    }


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": f"AstroSwarm/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    return data if isinstance(data, dict) else {}


def _cached_out(state: dict, now: float, force: bool) -> dict | None:
    """命中缓存时直接返回；不命中（或强制）返回 None 让调用方去联网。"""
    if force or not state.get("last_check"):
        return None
    if (now - float(state["last_check"])) >= CHECK_INTERVAL:
        return None
    latest = state.get("latest", "")
    out = dict(_EMPTY)
    out.update({
        "ok": True, "cached": True, "latest": latest,
        "url": state.get("url", ""), "notes": state.get("notes", ""),
        "sha256": state.get("sha256", ""), "size": int(state.get("size") or 0),
        "filename": state.get("filename", ""), "source": state.get("source", ""),
        "checked_at": float(state.get("checked_at") or 0),
        "newer": newer_available(APP_VERSION, latest),
    })
    return out


def check(force: bool = False) -> dict:
    """检查更新。结果写入状态文件；未到间隔且非强制时直接返回缓存。"""
    now = time.time()
    state = read_state()
    out = dict(_EMPTY)
    if state.get("checked_at"):
        out["checked_at"] = float(state["checked_at"])
    cached = _cached_out(state, now, force)
    if cached is not None:
        return cached

    info = {}
    err = ""
    for url in MANIFEST_URLS:                 # 主：官网下载清单（带 sha256）
        try:
            info = _from_manifest(_fetch(url))
        except Exception as e:  # noqa: BLE001
            err = str(e)
        if info:
            break
    if not info:                              # 兜底：老的签发站版本接口
        for url in UPDATE_URLS:
            try:
                info = _from_legacy(_fetch(url))
            except Exception as e:  # noqa: BLE001
                err = str(e)
            if info:
                break
    if not info:
        out["error"] = err or "更新接口无响应"
        return out

    new_state = {"last_check": now, "checked_at": now, **info}
    _write_state(new_state)
    out.update(info)
    out.update({
        "ok": True, "checked_at": now,
        "newer": newer_available(APP_VERSION, info["latest"]),
    })
    return out
