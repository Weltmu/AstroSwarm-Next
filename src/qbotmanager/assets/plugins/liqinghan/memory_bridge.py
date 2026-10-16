# -*- coding: utf-8 -*-
"""内置记忆模块的**特性检测桥**（liqinghan 插件侧）。

为什么要有这一层：内置记忆在 `qbotmanager.core.memory`（程序侧），但老装机环境
里那个模块可能不存在（用户只更新了插件、没更新程序）。所以：

* 模块在 → 记忆的写、读、注入、时间线、整理全走内置那一份（两个插件共用一份数据）；
* 模块不在 → **回退到本插件原来的逻辑**（`memory.Store` 的 memories/diary 表），
  一行都不会崩，只是没有智能档的加成。

任何一处异常都不许往外冒：记忆是加分项，不能拖垮回话。
"""
import logging

log = logging.getLogger("liqinghan.memory_bridge")

# 内置记忆的三档：off 不写不读、basic = 本插件原来的行为、smart 才加料。
_LEVELS = ("off", "basic", "smart")


def builtin():
    """拿内置记忆模块；拿不到返回 None（调用方按 None 走老逻辑）。"""
    try:
        from qbotmanager.core import memory as _m
        return _m
    except Exception:  # noqa: BLE001 —— 老装机环境没有这个模块，属预期
        return None


def available() -> bool:
    return builtin() is not None


def level(default="basic"):
    mod = builtin()
    if mod is None:
        return default
    try:
        return str(mod.level() or default)
    except Exception:  # noqa: BLE001
        return default


def level_note() -> str:
    """日志用的一句话：现在到底是哪一档、走的是内置还是老逻辑。"""
    mod = builtin()
    if mod is None:
        return "内置记忆模块不存在 → 用插件自带记忆（老逻辑）"
    return f"内置记忆模块已就位，档位 = {level()}"


def _qq_of(sender_qq) -> str:
    return str(sender_qq or "").strip()


def _user_source(store, sender_qq) -> str:
    """这个人最近一次说话来自哪条通道（插件自己记的 `users.source`）。

    取不到（老库 / 没记过）返回空串 —— 调用方按老规矩当 QQ 号处理。
    """
    if store is None:
        return ""
    getter = getattr(store, "user_source", None)
    if not callable(getter):
        return ""
    try:
        return str(getter(str(sender_qq or "").strip()) or "")
    except Exception:  # noqa: BLE001 —— 查不到通道不能影响记忆读写
        return ""


def _builtin_key(sender_qq, source="", store=None) -> str:
    """内置库统一的 key 形态：`平台:ID`（与 ai 插件 / 身份绑定同一套）。

    2026-10-13（老板：记忆要互通）：李清菡框架原来拿**裸 QQ 号**当 key，
    和 ai 插件（`qq:数字`）在同一个库里是两套 key —— 同一个人互相看不见。
    这里补上平台前缀；已经是 `平台:ID` 的原样返回。

    2026-10-15 补（同一件事的另一半）：官方通道给的是 **openid**、微信给的是
    **微信 id**，以前一律写成 `qq:<那串 id>` —— 和 ai 插件的
    `qq_official:<openid>` / `wechat:<id>` 对不上，同一个人的记忆在两个框架里
    各存一份（绑定身份也串不起来）。现在补前缀前先问"这个人来自哪条通道"：
    显式传的 `source` 优先，其次查 `users.source`（每条消息都会记），
    都拿不到就按老规矩当 QQ 号 —— 老数据、老环境的行为一字不变。
    """
    s = str(sender_qq or "").strip()
    if not s:
        return ""
    if ":" in s:
        return s
    src = str(source or "").strip().lower() or _user_source(store, s).lower()
    if src.startswith("wechat"):
        return "wechat:" + s
    if src.startswith("qq"):
        # 李清菡**只接官方 QQ 事件**（__init__.py 里按 `nonebot.adapters.qq` 过滤），
        # 拿到的是 openid —— 而 ai 插件那边同一个人的 key 是 `qq_official:<openid>`。
        # 所以这里的 `qq:*` 要映射到 `qq_official:`（以前落到 `qq:<openid>`，
        # 同一人在内置库里是两个人：李清菡记的事 ai 侧看不到，绑定也串不起来）。
        # 保险起见：**纯数字**按老规矩当 QQ 号（万一有人拿它接 OneBot）。
        return ("qq:" + s) if s.isdigit() else ("qq_official:" + s)
    if s.isdigit():
        return "qq:" + s
    return "qq_official:" + s


def _ai_identity():
    """拿 ai 插件的身份绑定模块（同一仓库的兄弟插件）；拿不到返回 None。

    只认**已经加载进 sys.modules** 的那份：没加载就不强行 import（避免把 ai 插件
    二次导入、重复注册处理器）。
    """
    import importlib
    import sys

    pkg = sys.modules.get("src.plugins.ai")
    if pkg is None:
        for name, mod in list(sys.modules.items()):
            if name.endswith(".plugins.ai") or name == "ai":
                pkg = mod
                break
    if pkg is None:
        return None
    ident = getattr(pkg, "identity", None)
    if ident is None:
        try:
            ident = importlib.import_module(getattr(pkg, "__name__", "src.plugins.ai")
                                            + ".identity")
        except Exception:  # noqa: BLE001
            return None
    return ident


def _bound_keys(key: str) -> list:
    """这个身份绑定的全部 key（跨平台绑定）；拿不到绑定就只返回自己。"""
    if not key:
        return []
    try:
        ident = _ai_identity()
        if ident is not None:
            keys = [str(k) for k in (ident.resolve(key) or []) if str(k).strip()]
            if keys:
                return keys
    except Exception:  # noqa: BLE001 —— 绑定信息读不到不能影响记忆功能
        pass
    return [key]


def remember(store, qq, fact, kind="", weight=0):
    """写一条记忆。内置可用就走内置；否则回退插件的 `remember_if_new`。"""
    mod = builtin()
    qq = _qq_of(qq)
    if not qq:
        return False
    if mod is not None:
        try:
            if mod.level() == "off":
                return False
            return bool(mod.remember(_builtin_key(qq, store=store), fact, kind=kind,
                                     weight=weight, source="liqinghan"))
        except Exception:  # noqa: BLE001
            log.exception("内置记忆写入失败，回退插件记忆")
    try:
        return bool(store.remember_if_new(qq, fact))
    except Exception:  # noqa: BLE001
        log.exception("插件记忆写入失败")
        return False


def recent(store, qq, limit=10):
    """`recall` 工具用：返回 [{'fact':..., 'ts':..., 'kind':...}, ...]，新→旧。"""
    mod = builtin()
    qq = _qq_of(qq)
    if mod is not None:
        try:
            if mod.level() == "off":
                return []
            merged = []
            for k in _bound_keys(_builtin_key(qq, store=store)):
                merged.extend(mod.recall(k, limit) or [])
            if merged:
                seen = set()
                out = []
                for r in sorted(merged, key=lambda x: x.get("ts") or 0, reverse=True):
                    f = str(r.get("fact") or "")
                    if f and f not in seen:
                        seen.add(f)
                        out.append({"fact": f, "ts": r.get("ts") or 0,
                                    "kind": r.get("kind") or "event"})
                return out[:limit]
            # 内置库里还没有这个人的东西：继续往下看插件老库（迁移期的兼容）
        except Exception:  # noqa: BLE001
            log.exception("内置记忆读取失败，回退插件记忆")
    try:
        return [{"fact": r["fact"], "ts": r["ts"], "kind": "event"}
                for r in store.memories_for(qq, limit)]
    except Exception:  # noqa: BLE001
        log.exception("插件记忆读取失败")
        return []


def list_for_user(store, qq, limit=20):
    """「你记得我什么」：内置优先，空了再看插件老库。"""
    return recent(store, qq, limit)


def forget(store, qq, fact=""):
    """「忘掉这件事」：内置档位真删；老环境退回插件的删除（只删该条）。"""
    mod = builtin()
    qq = _qq_of(qq)
    if mod is not None:
        try:
            out = []
            for k in _bound_keys(_builtin_key(qq, store=store)):
                out.extend(mod.forget(k, fact) or [])
            return out
        except Exception:  # noqa: BLE001
            log.exception("内置记忆删除失败")
    try:
        if not fact:
            return []
        db = getattr(store, "db", None)
        if db is None:
            return []
        cur = db.execute("DELETE FROM memories WHERE qq=? AND fact=?", (qq, fact))
        db.commit()
        return [fact] if cur.rowcount else []
    except Exception:  # noqa: BLE001
        log.exception("插件记忆删除失败")
        return []


def touch(qq, nickname="", store=None):
    """记一次「他来过」（内置时间线用；老环境是空操作）。"""
    mod = builtin()
    if mod is None or not _qq_of(qq):
        return
    try:
        mod.touch(_builtin_key(qq, store=store), nickname)
    except Exception:  # noqa: BLE001
        pass


def save_diary(store, day, content):
    """写日记：内置可用写内置（老库同名表，能被旧界面直接读到）。"""
    mod = builtin()
    if mod is not None:
        try:
            if mod.level() == "off":
                return
            mod.save_diary(day, content)
            return
        except Exception:  # noqa: BLE001
            log.exception("内置日记写入失败，回退插件日记")
    try:
        store.save_diary(day, content)
    except Exception:  # noqa: BLE001
        log.exception("插件日记写入失败")


def diary_recent(store, limit=2):
    """读日记：内置优先，空了再看插件老库。"""
    mod = builtin()
    if mod is not None:
        try:
            if mod.level() == "off":
                return []
            rows = mod.diary_recent(limit) or []
            if rows:
                return [{"day": r.get("day"), "content": r.get("content")} for r in rows]
        except Exception:  # noqa: BLE001
            log.exception("内置日记读取失败，回退插件日记")
    try:
        return [{"day": r["day"], "content": r["content"]}
                for r in store.diary_recent(limit)]
    except Exception:  # noqa: BLE001
        return []


def _session_info(sender_qq, group_id="", session=None):
    """时间线要的「今天第几次 / 距上次多久」——调用方给了就用，没给就只靠库里 last_seen。"""
    return dict(session or {})


def inject_extra(store, cfg, sender_qq="", group_id="", session=None):
    """智能档才返回额外注入块（基础档返回空串 = 保持线上原样）。

    * 内置模块不在 → 空串（老逻辑照旧）；
    * 档位 off → 空串，并且调用方要连老注入一起跳过（见 `legacy_inject_enabled()`）；
    * 档位 basic → 空串（老注入就是基础档的行为，一字不改）；
    * 档位 smart → 时间线 + 权重/承诺注入 + 印象档。
    """
    mod = builtin()
    if mod is None or not _qq_of(sender_qq):
        return ""
    try:
        if mod.level() != "smart":
            return ""
        tz = getattr(cfg, "tz", None)
        if isinstance(tz, str):
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(tz)
        now = None
        try:
            now = cfg.now().timestamp()
        except Exception:  # noqa: BLE001
            now = None
        # 2026-10-13：注入**整组绑定身份**（写走哪边都看得见）+ 统一的 key 形态
        blocks = []
        for k in _bound_keys(_builtin_key(sender_qq, store=store)):
            block = mod.inject_block(k, session=_session_info(
                sender_qq, group_id, session), now=now, tz=tz)
            if block and block not in blocks:
                blocks.append(block)
        return "\n".join(blocks)
    except Exception:  # noqa: BLE001
        log.exception("内置记忆注入失败（本次不带记忆）")
        return ""


def legacy_inject_enabled() -> bool:
    """插件原来的「最近 3 条记忆 + 2 天日记」注入还要不要跑。

    只有两种情况要跑：内置模块不在（老环境），或者档位是 basic（那一档就是它）。
    off 必须彻底不注入，smart 由内置那份负责 —— 两边同时注入会重复。
    """
    mod = builtin()
    if mod is None:
        return True
    try:
        return mod.level() == "basic"
    except Exception:  # noqa: BLE001
        return True


def extract_enabled() -> bool:
    """事实提炼要不要跑（off 档连提炼都不做，避免任何写入）。"""
    mod = builtin()
    if mod is None:
        return True
    try:
        return mod.level() != "off"
    except Exception:  # noqa: BLE001
        return True


def merge_after_scan():
    """每日整理（只有智能档真做）：把零散旧事实并成印象档。"""
    mod = builtin()
    if mod is None:
        return None
    try:
        return mod.merge_daily()
    except Exception:  # noqa: BLE001
        log.exception("记忆整理失败")
        return None


def migrate_if_needed():
    """第一次以智能档启动 → 导入老库（只读旧库）。"""
    mod = builtin()
    if mod is None:
        return None
    try:
        return mod.migrate_if_needed()
    except Exception:  # noqa: BLE001
        log.exception("老记忆库导入失败")
        return None
