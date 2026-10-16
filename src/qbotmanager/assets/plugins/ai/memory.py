# -*- coding: utf-8 -*-
"""记忆分层：全局记忆（全平台共享）+ 用户记忆（跨平台，按身份 key）。

- 全局记忆：所有平台、所有用户共享的长期记忆；
- 用户记忆：按身份 key（如 qq:12345 / wechat:xxx@im.wechat）存储；
  绑定身份后（identity.py），同一人的多个平台 key 共享一份用户记忆。
"""
import json
import os
import time
from typing import Dict, List

import nonebot_plugin_localstore as store
from nonebot import logger

MEMORY_FILE = store.get_plugin_data_file("aichat_memory.json")
MAX_GLOBAL = 100
MAX_USER = 50
MAX_MEMORY_TEXT = 1200

_data: Dict = {"global": [], "users": {}}
# 上次读/写盘时文件的指纹（mtime_ns + size），用来发现**外部改动**
# （控制台 / 桌面端也在改同一个文件，见 core/ai_memory.py）。
_file_key = None


def _fp():
    try:
        st = os.stat(MEMORY_FILE)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def load_memory() -> None:
    """读盘。

    2026-10-15（审计 C）：
      · 解析失败**不再把 `_data` 清成空** —— 那样下一次保存会把整库覆盖成空
        （断一次电 = 记忆全没）。现在保留内存里的旧值，并把手上的坏文件改名留档。
      · 记下指纹，供 `_reload_if_changed` 判断是否有外部改动。
    """
    global _data, _file_key
    _file_key = _fp()
    if not MEMORY_FILE.exists():
        return
    try:
        raw = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("global", [])
            raw.setdefault("users", {})
            _data = raw
    except Exception as e:
        logger.warning(f"加载记忆文件失败（保留内存里的旧记忆，不覆盖）: {e}")
        try:
            MEMORY_FILE.replace(MEMORY_FILE.with_suffix(".json.bad"))
            logger.warning("坏掉的记忆文件已改名为 aichat_memory.json.bad，可人工检查")
        except OSError:
            pass


def reload_if_changed() -> bool:
    """文件被**别人**改过（控制台/桌面端）就重新读一遍。

    2026-10-15（审计 C）：以前 `_data` 只在 import 时读一次，控制台删掉的记忆
    会在机器人下一次保存时"复活"，控制台新增的反而丢失 —— 两边各写各的整份文件。
    """
    global _file_key
    key = _fp()
    if key is None or key == _file_key:
        return False
    logger.info("记忆文件被外部改动过，重新加载（避免互相覆盖）")
    load_memory()
    return True


def save_memory() -> None:
    """原子落盘（临时文件 + fsync + os.replace）。

    2026-10-15（审计 C）：以前直接 `write_text` —— 写到一半断电/强杀会留下半截
    JSON，下次启动解析失败。现在要么写完整份、要么保持旧文件不变。
    """
    global _file_key
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(_data, ensure_ascii=False, indent=2)
        tmp = MEMORY_FILE.with_name(MEMORY_FILE.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, MEMORY_FILE)
        _file_key = _fp()
    except Exception as e:
        logger.error(f"保存记忆文件失败: {e}")


def _entry(text: str) -> dict:
    """一条记忆的存储形态。

    2026-10-16（对抗性复核 E）：**写入侧折叠空白** —— 记忆是原样拼进系统提示的，
    内嵌换行会让它看起来像"新的系统指令"（可持久化的提示注入）。这是老装机的
    回退路径（内置记忆模块缺失时才走），以前没有内置库那道 clean_fact，这里补上。
    """
    import re as _re
    return {"content": _re.sub(r"\s+", " ", str(text or "")).strip(), "ts": time.time()}


def get_global_memory() -> List[dict]:
    return _data.get("global", [])


def add_global_memory(text: str) -> None:
    reload_if_changed()
    text = text.strip()
    if not text:
        return
    items = _data.setdefault("global", [])
    items.append(_entry(text))
    if len(items) > MAX_GLOBAL:
        del items[: len(items) - MAX_GLOBAL]
    save_memory()


def clear_global_memory() -> None:
    _data["global"] = []
    save_memory()


def get_user_memory(identity_key: str) -> List[dict]:
    return _data.setdefault("users", {}).get(identity_key, [])


def add_user_memory(identity_key: str, text: str) -> None:
    # 写之前先看有没有外部改动：控制台刚删掉的记忆别被这次保存"复活"
    reload_if_changed()
    text = text.strip()
    if not text or not identity_key:
        return
    users = _data.setdefault("users", {})
    items = users.setdefault(identity_key, [])
    items.append(_entry(text))
    if len(items) > MAX_USER:
        del items[: len(items) - MAX_USER]
    save_memory()


def clear_user_memory(identity_key: str) -> None:
    users = _data.setdefault("users", {})
    if identity_key in users:
        del users[identity_key]
        save_memory()


def format_memory_text(identity_keys: List[str]) -> str:
    """把全局记忆 + 绑定身份的用户记忆拼成一段提示文本（去重、限长）。"""
    lines: List[str] = []
    seen = set()
    for entry in get_global_memory():
        content = str(entry.get("content", "")).strip()
        if content and content not in seen:
            seen.add(content)
            lines.append(content)
    for key in identity_keys:
        for entry in get_user_memory(key):
            content = str(entry.get("content", "")).strip()
            if content and content not in seen:
                seen.add(content)
                lines.append(content)
    if not lines:
        return ""
    text = "\n".join(f"- {line}" for line in lines)
    if len(text) > MAX_MEMORY_TEXT:
        text = text[:MAX_MEMORY_TEXT] + "..."
    return text


load_memory()
