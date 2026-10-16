# -*- coding: utf-8 -*-
"""跨平台身份绑定：同一人在不同平台（QQ / 微信 ClawBot / ...）的 user key 映射。

key 格式：<platform>:<user_id>，如 qq:12345、wechat:o9cq...@im.wechat。
绑定后，任意一个 key 都能解析出整组身份，用于共享用户记忆。
"""
import json
import os
from typing import Dict, List

import nonebot_plugin_localstore as store
from nonebot import logger

IDENTITY_FILE = store.get_plugin_config_file("aichat_identity.json")

_groups: Dict[str, List[str]] = {}  # canonical -> [keys]
_index: Dict[str, str] = {}         # key -> canonical

def load_identity() -> None:
    global _groups, _index
    if IDENTITY_FILE.exists():
        try:
            raw = json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                _groups = {k: list(v) for k, v in raw.items() if isinstance(v, list)}
                _index = {}
                for canonical, keys in _groups.items():
                    for k in keys:
                        _index[k] = canonical
                return
        except Exception as e:
            # 2026-10-16（审计 extra-5）：解析失败**不清空内存态** ——
            # 以前这里直接落到下面的 `_groups = {}`，一次断电留下的半截 JSON
            # 就会让所有绑定静默消失，而且下一次 bind/unbind 还会把空表写回去，
            # 把磁盘上可能还救得回来的数据盖掉。现在：坏文件改名留档、保留旧内存态。
            logger.warning(f"加载身份绑定失败（保留内存里的旧绑定）: {e}")
            try:
                bad = IDENTITY_FILE.with_name(IDENTITY_FILE.name + ".bad")
                os.replace(IDENTITY_FILE, bad)
                logger.warning(f"坏文件已改名留档: {bad.name}")
            except Exception as _e:  # noqa: BLE001 —— 改名失败也不能中断加载
                logger.warning(f"坏文件改名失败: {_e}")
            return
    _groups = {}
    _index = {}


def save_identity() -> None:
    """原子落盘（临时文件 + fsync + os.replace），与 core/ai_memory 同一套写法。

    2026-10-16（审计 extra-5）：以前直接 `write_text` —— 写到一半断电/强杀会留下
    半截 JSON，下次启动解析失败就把绑定全丢。现在要么写完整份、要么旧文件不动。
    """
    try:
        IDENTITY_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(_groups, ensure_ascii=False, indent=2)
        tmp = IDENTITY_FILE.with_name(IDENTITY_FILE.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, IDENTITY_FILE)
    except Exception as e:
        logger.error(f"保存身份绑定失败: {e}")


def canonical(key: str) -> str:
    return _index.get(key, key)


def resolve(key: str) -> List[str]:
    """返回与 key 同属一组的全部身份 key（含自身）。"""
    c = canonical(key)
    return list(_groups.get(c, [key]))


def bind(key_a: str, key_b: str) -> bool:
    if not key_a or not key_b or key_a == key_b:
        return False
    ca, cb = canonical(key_a), canonical(key_b)
    if ca == cb:
        return False
    group_a = _groups.get(ca, [ca])
    group_b = _groups.get(cb, [cb])
    merged = group_a + group_b
    new_canon = group_a[0]
    _groups.pop(ca, None)
    _groups.pop(cb, None)
    _groups[new_canon] = merged
    for k in merged:
        _index[k] = new_canon
    save_identity()
    return True


def unbind(key: str) -> bool:
    c = canonical(key)
    group = _groups.pop(c, None)
    if group is None:
        return False
    for k in group:
        _index.pop(k, None)
    save_identity()
    return True


def all_bindings() -> List[List[str]]:
    return [list(v) for v in _groups.values() if len(v) > 1]


load_identity()
