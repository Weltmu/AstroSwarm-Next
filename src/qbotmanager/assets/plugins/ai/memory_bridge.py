# -*- coding: utf-8 -*-
"""内置记忆模块的**特性检测桥**（ai 插件侧）。

与 `liqinghan/memory_bridge.py` 同一套约定：

* `qbotmanager.core.memory` 在 → 记忆的写/读/注入走内置那一份
  （与李清菡档案共用同一个库、同一套三档开关）；
* 模块不在（老装机环境）→ **回退到本插件自己的 `aichat_memory.json`**，
  行为与以前完全一样，不会崩。

身份 key 用 `平台:用户ID`（如 `qq:12345`）——与控制台「记忆时间线」、
身份绑定（`identity.py`）里的键格式一致，绑定后同一个人就是同一个 key。

档位由环境变量 `ASTROSWARM_MEMORY_LEVEL` 决定（程序侧 `core.bot.build_bot_env()`
注入），这里只读不改。
"""
import logging

log = logging.getLogger("ai.memory_bridge")

# ai 插件在**无档位**时的默认：老板要的是「无 = 不写不读」，所以这里是 off。
# （李清菡档案侧相反：那一档的「基础」就是它原来的行为，默认 basic。）
DEFAULT_LEVEL = "off"


def builtin():
    """拿内置记忆模块；拿不到返回 None。"""
    try:
        from qbotmanager.core import memory as _m
        return _m
    except Exception:  # noqa: BLE001 —— 老装机环境没有这个模块，属预期
        return None


def available() -> bool:
    return builtin() is not None


def level(default=DEFAULT_LEVEL):
    mod = builtin()
    if mod is None:
        return default
    try:
        return str(mod.level() or default)
    except Exception:  # noqa: BLE001
        return default


def level_note() -> str:
    mod = builtin()
    if mod is None:
        return "内置记忆模块不存在 → 用插件自带记忆（老逻辑）"
    return f"内置记忆模块已就位，档位 = {level()}"


def identity_key(platform, user_id) -> str:
    """`qq:12345` / `wechat:xxx@im.wechat`。缺一不可，否则不算一个身份。"""
    p = str(platform or "").strip()
    u = str(user_id or "").strip()
    if not p or not u:
        return ""
    return f"{p}:{u}"


def enabled() -> bool:
    """内置记忆是否接管（模块在 + 档位不是 off）。"""
    return available() and level() != "off"


def remember(key, fact, kind="", weight=0):
    """写一条长期记忆。返回是否写进去了。"""
    mod = builtin()
    if mod is None or not key:
        return False
    try:
        if mod.level() == "off":
            return False
        return bool(mod.remember(key, fact, kind=kind, weight=weight, source="ai"))
    except Exception:  # noqa: BLE001
        log.exception("内置记忆写入失败 key=%s", key)
        return False


def forget(key, fact=""):
    """真删。返回被删掉的内容列表。"""
    mod = builtin()
    if mod is None or not key:
        return []
    try:
        return list(mod.forget(key, fact) or [])
    except Exception:  # noqa: BLE001
        log.exception("内置记忆删除失败 key=%s", key)
        return []


def list_for(key, limit=20):
    mod = builtin()
    if mod is None or not key:
        return []
    try:
        if mod.level() == "off":
            return []
        return list(mod.list_memories(key, limit) or [])
    except Exception:  # noqa: BLE001
        return []


def session_for(key, user_key="", last_topic=""):
    """给时间线备料：上次说话多久（库里的 last_seen）。"""
    info = {}
    mod = builtin()
    if mod is not None and key:
        try:
            info["last_ts"] = int(mod.store().last_seen(key) or 0)
        except Exception:  # noqa: BLE001
            pass
    if last_topic:
        info["last_topic"] = last_topic
    return info


def inject_block(keys, session=None, person="", last_topic=""):
    """按档位拼注入块。返回空串 = 这一档不带记忆（off / 模块不在）。

    注意与插件老注入的区别：内置那套**自己带头**（「关于他的记忆：…」），
    所以调用方别再套「以下是长期记忆」那句（见 `prompt_prefix()`）。
    """
    mod = builtin()
    if mod is None:
        return ""
    try:
        if mod.level() == "off":
            return ""
    except Exception:  # noqa: BLE001
        return ""
    blocks = []
    for key in list(keys or []):
        if not key:
            continue
        try:
            block = mod.inject_block(key, session=session_for(key, last_topic=last_topic),
                                     person=person)
        except Exception:  # noqa: BLE001
            log.exception("内置记忆注入失败 key=%s", key)
            continue
        if block and block not in blocks:
            blocks.append(block)
    return "\n".join(blocks)


def prompt_prefix() -> str:
    """老注入那句前缀。内置块自带表头时不要重复加，否则会出现两层标题。"""
    mod = builtin()
    if mod is None:
        return "以下是长期记忆（如与当前对话无关可忽略）：\n"
    try:
        if mod.level() == "off":
            return ""
    except Exception:  # noqa: BLE001
        return "以下是长期记忆（如与当前对话无关可忽略）：\n"
    # 基础档的文案沿用线上那句（一字不差），智能档自带表头
    return "" if mod.level() == "smart" else "以下是长期记忆（如与当前对话无关可忽略）：\n"


def touch(key, nickname=""):
    mod = builtin()
    if mod is None or not key:
        return
    try:
        mod.touch(key, nickname)
    except Exception:  # noqa: BLE001
        pass


def stats(key=None):
    mod = builtin()
    if mod is None:
        return {}
    try:
        return dict(mod.stats(key))
    except Exception:  # noqa: BLE001
        return {}
