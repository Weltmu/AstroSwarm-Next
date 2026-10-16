# -*- coding: utf-8 -*-
"""AI 大脑 ↔ 工具运行时桥（2026-09-27 插件工坊地基）。

解决的两个短板（第三个"查重/版本/回滚"在 core/workshop 里）：
1. 工具执行以前只传权限集合 —— 禁言/撤回/提醒/记忆类工具实际是空操作；
   现在把当前通道、发送回调、记忆、提醒、数据、联网都接进 ToolContext。
2. 工具以前不按通道裁剪 —— 微信里也会看到 QQ 专属工具；现在
   runtime.schemas(platform) 按 manifest.adapters 裁剪，动作执行时再验一次。

工坊生成的插件跑在沙箱里（core/agent/sandbox.py），它们做事的唯一出口是
本文件的 ``do_action``：动作白名单 + 权限 + 通道能力三重校验，失败把回执
写回结果，模型据此如实告诉用户 —— 不允许"工具说成功、其实没做成"。
"""
import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from nonebot import get_bots, logger

from qbotmanager.core.agent.tool import ToolContext
from qbotmanager.core.agent_profile import channel_capabilities

from . import media_guard
from . import memory as memory_store
from . import memory_bridge
from . import tool_marker
from . import web_fetch
from .identity import canonical as identity_canonical, resolve as identity_resolve

# 管理器单例。**故意留成模块级可变名字**：单测用 `tools_bridge.chat_manager = 替身`
# 打桩（见 tests/test_media_actions.py::test_send_to_requires_whitelist）。
#
# 2026-10-15 修：这个模块以前**从来没导入过 chat_manager**，可 `_act_send_to` 里
# 却写着 `chat_manager.get_send_targets()` —— NameError 被下面的 `except Exception`
# 吞掉，于是「主动发到白名单目标」在生产环境 **100% 返回 config_unavailable**。
# 而单测因为注入了替身，一路全绿 —— 典型的"测试把生产必挂在眼皮底下藏住了"。
# 按需导入（而不是在文件顶部 import）是为了避开与 manager.py 的循环导入。
chat_manager = None


def _manager():
    """拿到 chat_manager 单例；测试替换过模块级名字时直接用替换后的。"""
    global chat_manager
    if chat_manager is None:
        from .manager import chat_manager as _cm
        chat_manager = _cm
    return chat_manager

# ---- 权限：按通道给最小集合（旧调用方不传 platform 时 registry 不裁剪）----


def source_of(msg) -> str:
    platform = str(getattr(msg, "platform", "") or "")
    scene = str(getattr(msg, "scene", "") or "")
    if platform == "qq":
        return f"qq:{'group' if scene == 'group' else 'c2c'}"
    return f"{platform}:{scene or 'private'}"


def user_key_of(msg) -> str:
    platform = str(getattr(msg, "platform", "") or "")
    uid = str(getattr(msg, "user_id", "") or "")
    return f"{platform}:{uid}" if platform and uid else ""


def permissions_for(msg) -> set:
    """按通道能力表给权限：微信只有聊天/记忆/定时，群管理只在 QQ。"""
    caps = channel_capabilities(source_of(msg))
    perms = {"send_message", "memory", "network", "timer"}
    if "group_admin" in caps:
        perms.add("group_admin")
    if "image" in caps or "voice" in caps:
        perms.add("media")
    return perms


# ---- 提醒：落盘 + 后台循环投递（发消息由适配器负责）----

_loop_task = None
_seq = 0


def _reminder_file() -> Path:
    return Path.cwd() / "data" / "ai" / "aichat_reminders.json"


def _load_reminders() -> list:
    try:
        data = json.loads(_reminder_file().read_text(encoding="utf-8"))
        return list(data) if isinstance(data, list) else []
    except Exception:  # noqa: BLE001 —— 文件不存在/损坏都当空
        return []


def _save_reminders(items: list) -> None:
    path = _reminder_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[tools] 保存提醒失败: {exc}")


def _next_seq(key: str) -> int:
    global _seq
    _seq = (_seq + 1) % 100000
    return _seq


def add_reminder(minutes: int, text: str, target: str, ctx) -> str:
    """写一条提醒（tool 侧同步调用），返回提醒 id。"""
    minutes = max(1, min(int(minutes), 43200))
    rid = uuid.uuid4().hex[:12]
    item = {
        "id": rid,
        "due": time.time() + minutes * 60,
        "text": str(text or "").strip()[:500],
        "target": str(target or "")[:80],
        "platform": str(getattr(ctx, "platform", "") or ""),
        "scene": str(getattr(ctx, "scene", "") or ""),
        "room_id": str(getattr(ctx, "extra", {}).get("room_id")
                       or getattr(ctx, "group_id", "") or ""),
        "user_id": str(getattr(ctx, "user_id", "") or ""),
        "nickname": str(getattr(ctx, "nickname", "") or ""),
        "created": time.time(),
        "attempts": 0,
    }
    items = _load_reminders()
    items.append(item)
    _save_reminders(items[-200:])
    logger.info(f"[tools] 已登记提醒 {rid}（{minutes} 分钟后，{item['platform']}:{item['scene']}）")
    return rid


def _qq_bot():
    try:
        from nonebot.adapters.qq import Bot as QQBot

        for bot in get_bots().values():
            if isinstance(bot, QQBot):
                return bot
    except Exception:  # noqa: BLE001
        return None
    return None


async def _send_wechat(user_id: str, text: str) -> tuple:
    """微信主动消息：token 失效/太旧就如实失败（iLink 的硬限制）。"""
    base = Path.cwd() / "data" / "nonebot_adapter_ilink"
    try:
        tokens = json.loads((base / "ilink_tokens.json").read_text(encoding="utf-8"))
        state = json.loads((base / "ilink_state.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False, "wechat_state_missing"
    info = (tokens or {}).get(user_id) or {}
    token = str(info.get("context_token") or "")
    if not token:
        return False, "wechat_no_session_token"
    if time.time() - float(info.get("ts") or 0) > 1800:
        return False, "wechat_token_expired"
    mod = None
    for name in ("src.plugins.nonebot_adapter_ilink.client",
                 "nonebot_adapter_ilink.client"):
        try:
            mod = __import__(name, fromlist=["IlinkClient"])
            break
        except Exception:  # noqa: BLE001
            mod = None
    if mod is None:
        return False, "wechat_client_missing"
    client = mod.IlinkClient(
        base_url=str((state or {}).get("baseurl") or "https://ilinkai.weixin.qq.com"),
        state_file=str(base / "ilink_state.json"),
        bot_type="3",
    )
    client.token = str((state or {}).get("token") or "")
    client.bot_id = str((state or {}).get("bot_id") or "")
    client.user_id = str((state or {}).get("user_id") or "")
    try:
        ok = await client.send_text(user_id, token, text)
        return bool(ok), "" if ok else "wechat_send_failed"
    except Exception as exc:  # noqa: BLE001
        return False, f"wechat_error:{exc}"[:120]
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass


async def _deliver(item: dict) -> tuple:
    platform = str(item.get("platform") or "")
    scene = str(item.get("scene") or "")
    nickname = str(item.get("nickname") or "")
    text = str(item.get("text") or "")
    if scene == "group" and nickname:
        text = f"{nickname}，[提醒] {text}"
    else:
        text = f"[提醒] {text}"
    if platform == "qq_official":
        bot = _qq_bot()
        if bot is None:
            return False, "no_bot"
        if scene == "group" and item.get("room_id"):
            await bot.send_to_group(group_openid=str(item["room_id"]),
                                    message=text, msg_seq=_next_seq("g"))
            return True, ""
        openid = str(item.get("user_id") or item.get("room_id") or "")
        if not openid:
            return False, "missing_openid"
        await bot.send_to_c2c(openid=openid, message=text,
                              msg_seq=_next_seq("c"))
        return True, ""
    if platform == "wechat":
        return await _send_wechat(str(item.get("user_id") or ""), text)
    if platform == "qq":  # 旧 OneBot 通道
        try:
            from nonebot.adapters.onebot.v11 import Bot as OB11
        except Exception:  # noqa: BLE001
            OB11 = None
        for bot in get_bots().values():
            if OB11 is not None and isinstance(bot, OB11):
                if scene == "group" and item.get("room_id"):
                    await bot.send_group_msg(group_id=int(item["room_id"]),
                                             message=text)
                else:
                    await bot.send_private_msg(user_id=int(item["user_id"]),
                                               message=text)
                return True, ""
        return False, "no_bot"
    return False, "unsupported_platform"


async def _reminder_loop() -> None:
    logger.info("[tools] 提醒投递循环已启动")
    while True:
        try:
            await asyncio.sleep(20)
            now = time.time()
            items = _load_reminders()
            if not items:
                continue
            keep = []
            changed = False
            for item in items:
                if float(item.get("due") or 0) > now:
                    keep.append(item)
                    continue
                changed = True
                try:
                    ok, err = await _deliver(item)
                except Exception as exc:  # noqa: BLE001
                    ok, err = False, str(exc)[:160]
                if ok:
                    logger.info(f"[tools] 提醒已送达 {item.get('id')}")
                    continue
                item["attempts"] = int(item.get("attempts") or 0) + 1
                if item["attempts"] < 3:
                    item["due"] = now + 60
                    keep.append(item)
                logger.warning(
                    f"[tools] 提醒 {item.get('id')} 投递失败（{err}），"
                    f"第 {item['attempts']} 次")
            if changed:
                _save_reminders(keep)
        except asyncio.CancelledError:
            logger.info("[tools] 提醒投递循环已停止")
            break
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[tools] 提醒循环异常: {exc}")


def ensure_reminder_loop() -> None:
    """第一次用到工具时挂上提醒循环（拿不到事件循环就跳过）。"""
    global _loop_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _loop_task is None or _loop_task.done():
        _loop_task = loop.create_task(_reminder_loop())


# ---- 记忆：读写真实 aichat_memory.json（按身份组）----


def memory_write(fact: str, ctx) -> dict:
    fact = str(fact or "").strip()[:500]
    if not fact:
        return {"ok": False, "error": "empty_fact"}
    key = f"{getattr(ctx, 'platform', '')}:{getattr(ctx, 'user_id', '')}"
    if not key or key == ":":
        return {"ok": False, "error": "no_identity"}
    target = identity_canonical(key)
    # 内置记忆（程序侧三档）优先：与李清菡档案共用同一份长期记忆；
    # 模块不在（老装机）→ 照旧写插件自己的 aichat_memory.json。
    if memory_bridge.available():
        if not memory_bridge.remember(target, fact):
            # 档位是「无」：如实说没记，别骗模型说记住了
            return {"ok": False, "error": "memory_level_off",
                    "hint": "记忆档位是「无」，这条没有写入"}
        return {"ok": True, "action": "memory_write", "user": target, "store": "builtin"}
    memory_store.add_user_memory(target, fact)
    return {"ok": True, "action": "memory_write", "user": target, "store": "plugin"}


def memory_read(user: str, ctx) -> dict:
    key = f"{getattr(ctx, 'platform', '')}:{getattr(ctx, 'user_id', '')}"
    keys = identity_resolve(key) if key != ":" else []
    text = ""
    if memory_bridge.available():
        target = identity_canonical(key) if key != ":" else ""
        # 2026-10-13：读也看**整组绑定身份** —— 写走 canonical、老数据在各自的
        # raw key 下，只看其中一个都会漏（微信↔QQ 记忆互通就是断在这）。
        block = memory_bridge.inject_block(keys or ([target] if target else []))
        text = block
    else:
        text = memory_store.format_memory_text(keys) if keys else ""
    requested = str(user or "").strip()
    nick = str(getattr(ctx, "nickname", "") or "")
    same = (not requested) or requested == nick or requested == str(
        getattr(ctx, "user_id", "") or "")
    return {
        "ok": True,
        "memory": text,
        "scope": "current_user" if same else "current_user_only",
        "requested": requested,
    }


# ---- 通用动作执行（工具与沙箱插件唯一出口）----

_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _data_dir(ctx) -> Path:
    pack = _NAME_RE.sub("_", str(getattr(ctx, "extra", {}).get("pack_id")
                                 or "shared"))[:40] or "shared"
    return Path.cwd() / "data" / "workshop_data" / pack


def _is_private_host(host: str) -> bool:
    """内网主机判定 —— **统一走 web_fetch**（2026-10-15 合并）。

    以前这里是**第二份**前缀黑名单实现，和 `web_fetch` 那份各漏各的：
      · 本函数漏 `::ffff:127.0.0.1`（IPv4-mapped IPv6）与 `localhost.`（尾点）；
      · `web_fetch` 那份漏"域名解析到内网"（`127.0.0.1.nip.io` 之类）。
    两份合一之后，修一处就是修全部；取不到 web_fetch 时按 **fail-closed**（不能碰）处理。
    """
    try:
        from .web_fetch import is_private_host as _check
        return _check(host)
    except Exception:  # noqa: BLE001 —— 判定器本身出问题，宁可不发请求
        return True


async def _act_send_message(params, ctx) -> dict:
    text = str(params.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "empty_text"}
    # 出站清洗：这是"模型自己写的话"，同样要把工具调用标记剥掉再发（与 brain 同口径）。
    # 剥完只剩标记 → 不把标记发出去，改成一句人话兜底。
    text, used_fallback, removed = tool_marker.outbound_text(text)
    if removed:
        logger.warning("[tools] send_message 正文里剥掉了工具调用标记，已拦下不发")
    if used_fallback:
        logger.warning("[tools] send_message 正文只剩工具调用标记，已改成兜底话")
    channel = getattr(ctx, "channel", None)
    if channel is None:
        return {"ok": False, "error": "channel_unavailable"}
    # 2026-10-15 修（"工具说成功、其实没发出去"）：通道的 send_text 是**有返回值**的 ——
    # 微信那条在窗口过期/发送失败时返回 False（不抛异常）。以前返回值被丢掉、恒报
    # ok=True，模型据此对用户说"已经发了"，对方其实什么都没收到。
    # ⚠ 判定必须写 `is False`：OneBot / 飞书 / 官方那条 send_text 返回 None（已发出），
    #   写成 `if not ok` 会把正常发送全判成失败。
    if params.get("at_user") and str(getattr(ctx, "scene", "")) == "group":
        ok = await channel.send_text_at(text)
    else:
        ok = await channel.send_text(text)
    if ok is False:
        return {"ok": False, "error": "send_failed", "detail": "通道返回发送失败"}
    return {"ok": True, "action": "send_message"}


def _pick_connection(ctx):
    """按**当前通道**挑一条合适的机器人连接。返回 `(bot, is_onebot)`；挑不到 `(None, False)`。

    2026-10-12 踩坑：别直接判 `"onebot" not in 模块名` —— 包名前缀 `nonebot` 本身
    就含 onebot，官方那条永远匹配不上。先剥掉 `nonebot.` 前缀再比。
    当晚模拟验收再补一坑：`get_bots()` 的顺序不保证 —— **优先挑"本次消息那条连接"**
    （self_id 对上），否则多协议端并存时动作会落到另一条线上。

    （这段逻辑原来长在 `_act_mute` 里，2026-10-15 抽出来给撤回等动作共用，
    免得多出一个动作就多抄一份、抄漏一坑。）
    """
    platform = str(getattr(ctx, "platform", "") or "")
    try:
        from nonebot.adapters.onebot.v11 import Bot as _OneBot
    except Exception:  # noqa: BLE001
        _OneBot = None
    bot = None
    try:
        want_self = str((getattr(ctx, "extra", None) or {}).get("self_id") or "")
        matches = []
        for b in get_bots().values():
            mod = (type(b).__module__ or "").lower()
            tail = mod.split("nonebot.adapters.", 1)[-1]
            if platform == "qq_official":
                if tail.startswith("qq") and not tail.startswith("onebot"):
                    matches.append(b)
            else:
                if (_OneBot is not None and isinstance(b, _OneBot)) or \
                        tail.startswith("onebot"):
                    matches.append(b)
        bot = next((b for b in matches
                    if want_self and str(getattr(b, "self_id", "")) == want_self), None)
        if bot is None and matches:
            bot = matches[0]
    except Exception:  # noqa: BLE001
        bot = None
    if bot is None:
        bot = _qq_bot() if platform == "qq_official" else None
    is_onebot = bool(bot is not None) and (
        (_OneBot is not None and isinstance(bot, _OneBot))
        or "onebot.v11" in (type(bot).__module__ or "").lower())
    return bot, is_onebot


async def _act_recall_message(params, ctx) -> dict:
    """撤回一条消息。

    2026-10-15 修（症状：任何通道都撤不了）：以前只认 **QQ 官方** 那条连接
    （`_qq_bot()`），而且把 QQ 群号当 `group_openid` 传：
      · 纯 NapCat/OneBot 部署 → `_qq_bot()` 返回 None，永远回 `unsupported_by_channel`；
      · 混布下拿到了官方连接 → openid ≠ 群号，`delete_group_message` 必然失败。
    于是模型工具表里挂着「撤回一条群消息」，**任何通道都调不通**，而模型往往已经
    先向用户承诺"好，我撤了"。现在按当前通道挑连接：OneBot 走 `delete_msg`。
    """
    message_id = str(params.get("message_id") or "").strip()
    if not message_id:
        return {"ok": False, "error": "missing_message_id"}
    bot, is_onebot = _pick_connection(ctx)
    if bot is None:
        return {"ok": False, "error": "unsupported_by_channel",
                "channel": str(getattr(ctx, "platform", "")),
                "detail": "现在没有可用的机器人连接（第三方 OneBot / 官方 QQ 都行）"}
    group = str(params.get("group_id") or "").strip()
    try:
        if is_onebot:
            # OneBot v11 / NapCat：群聊私聊统一 delete_msg
            await bot.call_api("delete_msg", message_id=int(message_id))
        elif group:
            fn = getattr(bot, "delete_group_message", None)
            if fn is None:
                return {"ok": False, "error": "unsupported_by_channel"}
            await fn(group_openid=group, message_id=message_id)
        else:
            fn = getattr(bot, "delete_c2c_message", None)
            if fn is None:
                return {"ok": False, "error": "unsupported_by_channel"}
            await fn(openid=str(getattr(ctx, "user_id", "")), message_id=message_id)
    except (TypeError, ValueError):
        # int(message_id) 失败：OneBot 的 message_id 必须是数字
        return {"ok": False, "error": "invalid_message_id", "detail": message_id[:60]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "recall_failed", "detail": str(exc)[:160]}
    return {"ok": True, "action": "recall_message"}


async def _act_mute(params, ctx, all_users: bool) -> dict:
    # 2026-10-12 修（老板实测：群里让机器人禁言，它一律回「这个端不支持」）：
    # 老代码只认 **QQ 官方** 的 `set_group_mute_all` / `set_group_member_mute`，
    # 而 NapCat/OneBot 的方法叫 `set_group_whole_ban` / `set_group_ban`，
    # 且 `_qq_bot()` 只会返回官方那条连接 → 在 OneBot 上必然判"不支持"。
    # 现在按**当前通道**挑连接、按**各自的 API** 调。
    # 2026-10-15：挑连接那段抽成了 `_pick_connection()`（撤回等动作共用同一份，
    # 少抄一份就少漏一坑）。"优先挑本次消息那条连接"的保证在那边，见其 docstring。
    platform = str(getattr(ctx, "platform", "") or "")
    bot, is_onebot = _pick_connection(ctx)
    if bot is None:
        return {"ok": False, "error": "unsupported_by_channel",
                "detail": "现在没有支持群管理的机器人连接（第三方 OneBot / 官方 QQ 都行）"}

    # 2026-10-12 修（老板实测：说「解除禁言」，它回"解开了"、其实又给禁上了）：
    # 老代码 `max(1, int(minutes or 1))` 把 **0 变成 1 分钟**，而且只会 enable=True，
    # 根本没有"解除"这条路。现在：minutes <= 0（或 enable=false）→ 解除。
    try:
        raw_min = params.get("minutes")
        minutes = int(raw_min) if raw_min is not None else 1
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid_minutes"}
    unmute = minutes <= 0
    if str(params.get("enable", "")).strip().lower() in ("false", "0", "no"):
        unmute = True
    minutes = max(1, min(abs(minutes) if minutes else 1, 43200))
    group = str(params.get("group_id") or getattr(ctx, "group_id", "") or "")
    if not group:
        return {"ok": False, "error": "missing_group_id"}

    # ---- 2026-10-12 安全闸：**先看"喊话的人"有没有权限** ----
    # 老板实测：群里另一个号 @ 它喊「全员禁言」，它照做了 —— 只看"我自己是不是管理员"，
    # 不看"让我动手的是谁"，等于谁都能借它的手把全群摁哑。解除禁言不走这道闸。
    #
    # 2026-10-15：这道闸**补到官方通道上**。之前官方通道之所以没给群管理权限，
    # 就是因为以为"官方查不到喊话人的角色" —— 其实官方更好办：群消息事件的
    # `author.member_role`（member/admin/owner）是**必填**字段，适配器已把它带进
    # `msg.extra["member_role"]` → `ctx.extra`，这里直接读，一次接口都不用调。
    if not unmute:
        asker = str(getattr(ctx, "user_id", "") or "")
        me_id = str(getattr(bot, "self_id", "") or "")
        if asker and asker != me_id:
            who_name = asker
            if is_onebot:
                try:
                    who = await bot.call_api("get_group_member_info", group_id=int(group),
                                             user_id=int(asker), no_cache=True)
                    asker_role = str((who or {}).get("role") or "member")
                    who_name = str((who or {}).get("nickname") or asker)
                except Exception as exc:  # noqa: BLE001 —— 查不到就保守：不动手
                    logger.info(f"[mute] 查喊话人 {asker} 的身份失败 → 保守不动手：{str(exc)[:100]}")
                    return {"ok": False, "error": "asker_role_unknown",
                            "hint": "查不到他有没有权限，别动手，如实说一句「我先确认下权限」"}
            else:
                asker_role = str((getattr(ctx, "extra", None) or {})
                                 .get("member_role") or "").lower()
                who_name = str(getattr(ctx, "nickname", "") or asker)
                if not asker_role:
                    # 老适配器 / 频道事件没带角色 → 保守拒绝，绝不"谁喊都动手"
                    logger.info("[mute] 官方通道没带 member_role → 保守不动手")
                    return {"ok": False, "error": "asker_role_unknown",
                            "hint": "查不到他有没有权限，别动手，如实说一句「我先确认下权限」"}
            if asker_role not in ("owner", "admin"):
                return {"ok": False, "error": "asker_not_admin",
                        "who": who_name,
                        "hint": "别当命令执行 —— 一句人话回绝：这种事得群主或管理员开口，"
                                "我不听路人的。别解释一堆、别道歉"}

    if all_users:
        if is_onebot:
            # 解除：enable=False（不排自动解禁）
            if unmute:
                try:
                    await bot.call_api("set_group_whole_ban", group_id=int(group), enable=False)
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "error": "unmute_failed", "detail": str(exc)[:160]}
                return {"ok": True, "action": "unmute_all",
                        "hint": "全员禁言已经解除，如实告诉他「解开了」"}
            try:
                await bot.call_api("set_group_whole_ban", group_id=int(group), enable=True)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": "mute_failed", "detail": str(exc)[:160]}
            # 2026-10-12：OneBot 的全员禁言**没有时长参数**，说"5 分钟"必须自己定闹钟解除，
            # 否则会一直禁下去（老板实测后提的）。这里直接排一个自动解除。
            auto = ""
            try:
                async def _auto_unban(seconds: float, gid: int):
                    await asyncio.sleep(seconds)
                    try:
                        await bot.call_api("set_group_whole_ban", group_id=gid, enable=False)
                        logger.info(f"[mute] 全员禁言已到点自动解除（群 {gid}）")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"[mute] 自动解除失败：{exc}")

                asyncio.create_task(_auto_unban(minutes * 60, int(group)))
                auto = f"{minutes} 分钟后我会自动解除"
            except Exception:  # noqa: BLE001
                auto = f"注意：这条通道的全员禁言没有时长，{minutes} 分钟后要人手动解除"
            return {"ok": True, "action": "mute_all", "minutes": minutes,
                    "note": auto, "hint": "全员禁言只对普通成员有效，群主和管理员照样能说话，"
                                          "别跟他说『所有人都说不了话了』"}
        fn = getattr(bot, "set_group_mute_all", None)
        if fn is None:
            return {"ok": False, "error": "unsupported_by_channel",
                    "detail": "这条通道没有全员禁言能力"}
        try:
            await fn(group_openid=group, mute_seconds=minutes * 60)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "mute_failed", "detail": str(exc)[:160]}
        return {"ok": True, "action": "mute_all", "minutes": minutes}

    # 参数名兼容：给模型看的 schema 写的是 `target`（"要禁言的人"），
    # 模型照 schema 传 target 时早期实现只认 user_id，会判成"没给谁"。
    user = str(params.get("user_id") or params.get("target")
               or params.get("qq") or "").strip()
    if not user:
        return {"ok": False, "error": "missing_target", "hint": "要禁言谁？给我他的 QQ 号"}
    # ---- 2026-10-12 护栏：先查他要禁言的是谁（群主/管理员禁不了，别白试也别乱来）----
    # 注意：**解除禁言**不需要这道护栏（给群主解禁是好事，别拦）。
    if is_onebot and not unmute:
        try:
            info = await bot.call_api("get_group_member_info", group_id=int(group),
                                      user_id=int(user), no_cache=True)
            role = str((info or {}).get("role") or "member")
            nick = str((info or {}).get("card") or (info or {}).get("nickname") or "")
            if role == "owner":
                return {"ok": False, "error": "target_is_owner",
                        "who": nick or user,
                        "hint": f"{nick or user} 是**群主**，连管理员也禁言不了他 —— "
                                f"如实告诉他『群主我动不了』，别硬试"}
            if role == "admin":
                return {"ok": False, "error": "target_is_admin",
                        "who": nick or user,
                        "hint": f"{nick or user} 也是**管理员**，管理员之间互相禁不了 —— "
                                f"如实说清，别再调一次"}
        except Exception as exc:  # noqa: BLE001 —— 查不到身份就照旧试（有些协议端没这接口）
            logger.info(f"[mute] 查 {user} 的群身份失败（照旧尝试禁言）：{str(exc)[:100]}")
    if is_onebot:
        try:
            # OneBot V11：duration 是**秒**；0 = 解除
            await bot.call_api("set_group_ban", group_id=int(group), user_id=int(user),
                               duration=0 if unmute else minutes * 60)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "mute_failed", "detail": str(exc)[:160]}
        if unmute:
            return {"ok": True, "action": "unmute_user", "user_id": user,
                    "hint": "他的禁言已经解除，如实说一句就行"}
        return {"ok": True, "action": "mute_user", "user_id": user, "minutes": minutes}
    # 2026-10-15 修（审计 C）：官方适配器**有**群成员禁言接口，名字是
    # `set_group_members_mute(group_id=…, members=[SetMemberMuteState(…)])`。
    # 以前这里找的是 `set_group_member_mute`（这个不存在）→ 官方通道的
    # mute_user 永远回 unsupported_by_channel。今天刚给官方通道开了 group_admin
    # 权限，等于给了个永远用不了的能力，这里补上真正的调用。
    fn = getattr(bot, "set_group_members_mute", None)
    if fn is None:
        return {"ok": False, "error": "unsupported_by_channel",
                "detail": "这条通道没有群成员禁言能力（适配器版本太老）"}
    try:
        state_cls = _qq_mute_state_cls()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "unsupported_by_channel",
                "detail": f"取不到官方禁言数据类：{exc}"}
    ops = ["del"] if unmute else ["update", "add"]   # 先按"已禁言"更新，失败再试新增
    errors = []
    for op in ops:
        try:
            member = state_cls(op=op, member_openid=user,
                               mute_expire_at=_rfc3339(time.time() + minutes * 60))
        except Exception as exc:  # noqa: BLE001 —— 构造失败就没必要重试了
            return {"ok": False, "error": "mute_failed", "detail": str(exc)[:160]}
        try:
            await fn(group_id=group, members=[member])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{op}: {str(exc)[:80]}")
            continue
        if unmute:
            return {"ok": True, "action": "unmute_user", "user_id": user,
                    "hint": "他的禁言已经解除，如实说一句就行"}
        return {"ok": True, "action": "mute_user", "user_id": user, "minutes": minutes}
    return {"ok": False, "error": "mute_failed", "detail": "；".join(errors)[:160]}


# ---- G3 重活动作的公共部件（短链展开 / 抓正文 / 下载小文件）----
_UA = "AstroSwarm-Plugin/1.0"
_FETCH_TIMEOUT = 20
_DOWNLOAD_MAX_BYTES = 2 * 1024 * 1024      # 下载小文件上限：2MB（防止把磁盘写满）
_TEXT_LIMIT = 20000
_TAG_RE = re.compile(r"<[^>]+>")


def _safe_file_name(name: str) -> str:
    """把文件名洗成安全形式（不许有路径分隔符/点点）。"""
    base = _NAME_RE.sub("_", str(name or "file").rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
    base = base.strip("._") or "file"
    return base[:80]


def _downloads_dir() -> Path:
    """插件自己的下载目录（bot 目录下 data/downloads）。"""
    return Path.cwd() / "data" / "downloads"


def _plain_text(html: str) -> str:
    """粗暴去标签取正文（够 AI 读；不追求完美排版）。"""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = _TAG_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
    text = text.replace("&gt;", ">").replace("&quot;", '"')
    return re.sub(r"[ \t\r\f\v]+", " ", text)


async def _fetch_url(url: str, want: str = "text", limit: int = _TEXT_LIMIT) -> dict:
    """统一的取网页：实现放在插件自带的 web_fetch.py 里（AI 聊天与插件动作共用一套）。

    这里保留异步壳：真正的网络请求丢到线程池，别卡住机器人的事件循环。
    """
    try:
        info = await asyncio.get_running_loop().run_in_executor(
            None, lambda: web_fetch.fetch(url, want=want, limit=limit,
                                          timeout=_FETCH_TIMEOUT))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "fetch_failed", "detail": str(exc)[:200]}
    if info.get("ok") and want == "text":
        info["action"] = "fetch_text"
    elif info.get("ok"):
        info["action"] = "expand_link"
    return info
async def _act_http_request(params, ctx) -> dict:
    url = str(params.get("url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {"ok": False, "error": "bad_url"}
    if _is_private_host(parsed.hostname):
        return {"ok": False, "error": "blocked_host", "host": parsed.hostname}
    method = str(params.get("method") or "GET").upper()
    if method not in ("GET", "POST"):
        return {"ok": False, "error": "bad_method"}
    body = params.get("body")
    data = body.encode("utf-8") if isinstance(body, str) and body else None
    headers = {"User-Agent": "AstroSwarm-Plugin/1.0"}
    raw_headers = params.get("headers") or {}
    if isinstance(raw_headers, dict):
        for key in ("Content-Type", "Accept", "Authorization", "X-API-Key"):
            value = raw_headers.get(key)
            if isinstance(value, str) and value:
                headers[key] = value[:300]
    try:
        timeout = max(1, min(int(params.get("timeout") or 10), 20))
    except (TypeError, ValueError):
        timeout = 10
    if not url.lower().startswith("https://") and "Authorization" in headers:
        headers.pop("Authorization")   # 明文传输不带凭证

    def _do():
        # 2026-10-15 修（安全）：这里以前是裸 `urlopen` —— 它**自动跟随跳转且不复查**，
        # 一个公网地址 302 到 169.254.169.254 / 192.168.x.x 就把上面那道内网拦截整个
        # 绕过去了（同一个 bug 在"下载小文件""跨平台取字节"两条路上早修了，这条漏网）。
        # 现在统一走 web_fetch.open_checked：每一跳都复查，跨域跳转还会丢掉 Authorization。
        with web_fetch.open_checked(url, timeout, method=method, data=data,
                                    headers=headers) as resp:
            raw = resp.read(400_000)
            return int(getattr(resp, "status", 200)), raw.decode("utf-8", "replace")

    try:
        status, text = await asyncio.get_running_loop().run_in_executor(None, _do)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "http_failed", "detail": str(exc)[:200]}
    truncated = len(text) > 20000
    return {"ok": True, "status": status, "text": text[:20000], "truncated": truncated}


async def _act_data_write(params, ctx) -> dict:
    name = _NAME_RE.sub("_", str(params.get("name") or "data"))[:40] or "data"
    text = str(params.get("text") or "")
    if len(text) > 200_000:
        return {"ok": False, "error": "too_large"}
    path = _data_dir(ctx) / f"{name}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "write_failed", "detail": str(exc)[:160]}
    return {"ok": True, "name": name, "bytes": len(text.encode("utf-8"))}


async def _act_data_read(params, ctx) -> dict:
    name = _NAME_RE.sub("_", str(params.get("name") or "data"))[:40] or "data"
    path = _data_dir(ctx) / f"{name}.json"
    try:
        if not path.exists():
            return {"ok": False, "error": "not_found", "name": name}
        return {"ok": True, "name": name,
                "text": path.read_text(encoding="utf-8")[:200_000]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "read_failed", "detail": str(exc)[:160]}


async def _act_reminder_add(params, ctx) -> dict:
    text = str(params.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "empty_text"}
    try:
        minutes = max(1, int(params.get("minutes") or 1))
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid_minutes"}
    target = str(params.get("target") or "")
    try:
        rid = add_reminder(minutes, text, target, ctx)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "store_error", "detail": str(exc)[:160]}
    return {"ok": True, "reminder_id": rid, "minutes": minutes,
            "remind_at": time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(time.time() + minutes * 60))}


async def _act_memory_write(params, ctx) -> dict:
    return memory_write(str(params.get("fact") or ""), ctx)


async def _act_memory_read(params, ctx) -> dict:
    return memory_read(str(params.get("user") or ""), ctx)


def _qq_mute_state_cls():
    """官方 QQ 的 `SetMemberMuteState`（不同版本放的层级不一样，两处都试）。"""
    try:
        from nonebot.adapters.qq.models import SetMemberMuteState
        return SetMemberMuteState
    except Exception:  # noqa: BLE001
        from nonebot.adapters.qq import SetMemberMuteState
        return SetMemberMuteState


def _rfc3339(ts: float) -> str:
    """官方禁言接口要 RFC3339 时间串（`mute_expire_at`）。"""
    import datetime as _dt

    return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).isoformat(
        timespec="seconds")


def _drop_bad_card_urls(extra: dict) -> list:
    """音乐卡片里的 image / audio 走同一道闸：不合法的丢掉（卡片自动降级成文字）。

    这两个地址也会被协议端取回去渲染，所以内网地址同样不能收；丢掉而不是整条失败，
    是因为适配器本来就支持"缺字段 → 发歌名 + 链接"，宁可降级也别让用户什么都收不到。
    """
    dropped = []
    for key in ("image", "audio"):
        val = str(extra.get(key) or "").strip()
        if not val:
            continue
        _target, why, _hint = media_guard.check_source(val)
        if why:
            dropped.append(key)
            extra.pop(key)
    return dropped


async def _act_send_media(params, ctx, kind: str) -> dict:
    """G1：发图片 / 文件 / 语音 / 音乐卡片。

    插件只给一个地址（公网 URL）或本地路径，真正的发送由主程序按通道实现：
    - 通道不支持 → `unsupported_by_channel`（不假装成功，AI 可以据此改用文字）；
    - 参数缺失 → `missing_*`，让 AI 知道该补什么。
    """
    channel = getattr(ctx, "channel", None)
    if channel is None:
        return {"ok": False, "error": "channel_unavailable"}
    target = str(params.get("url") or params.get("path") or "").strip()
    if not target:
        return {"ok": False, "error": "missing_url"}
    # 2026-10-15 安全闸（**这块以前是高危面**）：模型填什么、这里就发什么 ——
    # 群友一句"帮我发个 data/ai/xxx.json"式的提示注入，就能把本机任意文件发进群
    # （OneBot 的 upload_group_file 认本地路径）。现在统一过 media_guard：
    # 本地只认 data 下自己产出的目录，网址只认公网或协议端刚给过的地址。
    target, why, hint = await asyncio.to_thread(
        media_guard.check_source, target, bool(getattr(ctx, "is_superuser", False)))
    if why:
        return {"ok": False, "error": why, "detail": hint}
    if not channel.supports(kind):
        return {"ok": False, "error": "unsupported_by_channel",
                "channel": str(getattr(channel, "platform", ""))}
    if kind == "image":
        ok = await channel.send_media("image", target, str(params.get("caption") or "")[:200])
    elif kind == "file":
        ok = await channel.send_media("file", target, str(params.get("name") or "")[:80])
    elif kind == "voice":
        ok = await channel.send_media("voice", target)
    else:
        title = str(params.get("title") or "")[:60]
        singer = str(params.get("singer") or params.get("content") or "")[:60]
        # 音乐卡片（2026-10-12 网易云点歌能力包上架时补）：把音频/封面一起透传给
        # 通道，QQ(OneBot) 那边才会拼成能点开播放的 [CQ:music,type=custom]；
        # 不透传就只能退回"歌名 + 链接"文本（老表现）。
        extra = {}
        for key in ("song_id", "image", "audio"):
            val = str(params.get(key) or "").strip()
            if val:
                extra[key] = val[:600]
        dropped = await asyncio.to_thread(_drop_bad_card_urls, extra)
        if dropped:
            logger.info(f"[tools] 音乐卡片的 {dropped} 地址不过闸，丢掉落回文字")
        sender = getattr(channel, "send_card", None)
        ok = None
        if sender is not None and extra:
            try:
                ok = bool(await sender(target, title, singer, **extra))
            except TypeError:
                ok = None          # 这条通道的 send_card 不认扩展参数 → 走通用入口
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[tools] 发音乐卡片失败（{str(exc)[:80]}），改走通用降级")
                ok = None
        if ok is None:
            ok = await channel.send_media("card", target, title, singer)
    if not ok:
        return {"ok": False, "error": "send_failed", "kind": kind}
    return {"ok": True, "action": f"send_{kind}", "target": target[:200]}


async def _act_send_to(params, ctx) -> dict:
    """G2：主动发到指定目标（别的群 / 私聊某人）。

    安全边界（很重要）：只允许发到**白名单**里的目标 —— 白名单来自机器人侧配置
    `aichat_manager.json` 的 `allowed_send_targets`（形如 `group:123456` / `user:abc`）。
    没配白名单就是"一个都不许发"，避免插件拿它当群发器。
    """
    channel = getattr(ctx, "channel", None)
    sender = getattr(channel, "send_to_target", None) if channel is not None else None
    if sender is None:
        return {"ok": False, "error": "unsupported_by_channel"}
    target = str(params.get("target") or "").strip()
    text = str(params.get("text") or "").strip()
    if not target or not text:
        return {"ok": False, "error": "missing_target_or_text"}
    try:
        allowed = _manager().get_send_targets()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "config_unavailable", "detail": str(exc)[:120]}
    if not allowed:
        return {"ok": False, "error": "target_whitelist_empty",
                "hint": "机器人侧 aichat_manager.json 里没配 allowed_send_targets"}
    if target not in allowed:
        return {"ok": False, "error": "target_not_allowed", "target": target[:80],
                "allowed_count": len(allowed)}
    ok = bool(await sender(target, text[:1000]))
    return {"ok": True, "action": "send_to", "target": target[:80]} if ok else \
        {"ok": False, "error": "send_failed", "target": target[:80]}


async def _act_expand_link(params, ctx) -> dict:
    """G3：展开短链（跟随跳转，拿到真实地址；不抓正文）。"""
    url = str(params.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "missing_url"}
    return await _fetch_url(url, want="link")


async def _act_fetch_text(params, ctx) -> dict:
    """G3：抓网页正文（纯文本，够 AI 读；有长度上限）。"""
    url = str(params.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "missing_url"}
    return await _fetch_url(url, want="text",
                            limit=int(params.get("limit") or 20000))


async def _act_download_file(params, ctx) -> dict:
    """G3：下载小文件到机器人数据目录（有大小上限，防止把磁盘写满）。

    2026-10-15 修（安全）：以前这里**完全不校验 URL** ——
      · 不查协议：`file://`、`ftp://` 全部放行（`file:///C:/...` 能把本机文件拷进下载目录）；
      · 不查主机：内网地址、云元数据随便打；
      · `urlopen` 自动跟随跳转且**不复查**跳转目标。
    现在统一走 `web_fetch.open_checked()`（校验协议 + 主机 + 每一跳跳转复查），
    与「读网页」那条路共用同一套内网拦截 —— 内网防线只有一份，才不会各修一半。
    """
    url = str(params.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "missing_url"}
    name = _safe_file_name(str(params.get("name") or "") or url.rsplit("/", 1)[-1])
    limit = min(int(params.get("max_bytes") or _DOWNLOAD_MAX_BYTES), _DOWNLOAD_MAX_BYTES)

    def _download():
        """同步下载（读不到就抛）：丢线程池跑，别卡住事件循环。

        2026-10-15：这里以前直接同步 `with open_checked(...)` —— 一个慢站点能把
        整个机器人卡住最长 20 秒（消息不回、心跳不走）。兄弟函数 `_fetch_url`
        早就用 `run_in_executor` 了，这条漏的补上。
        """
        with web_fetch.open_checked(url, _FETCH_TIMEOUT) as resp:
            size = int(resp.headers.get("Content-Length") or 0)
            if size and size > limit:
                return {"ok": False, "error": "too_large", "bytes": size, "limit": limit}
            blob = resp.read(limit + 1)
            if len(blob) > limit:
                return {"ok": False, "error": "too_large", "bytes": len(blob), "limit": limit}
            return blob

    try:
        blob = await asyncio.to_thread(_download)
    except ValueError as exc:
        # open_checked 主动拒收：bad_url / blocked_host / blocked_redirect
        return {"ok": False, "error": "blocked_url", "detail": str(exc)[:160]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "download_failed", "detail": str(exc)[:160]}
    if isinstance(blob, dict):          # 提前返回的 too_large 结果
        return blob
    try:
        target = _downloads_dir() / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    except OSError as exc:
        return {"ok": False, "error": "write_failed", "detail": str(exc)[:160]}
    return {"ok": True, "action": "download_file", "path": str(target), "bytes": len(blob)}


async def _act_compress_image(params, ctx) -> dict:
    """G3：图片压缩（沙箱没有 Pillow，只能主程序代劳）。

    2026-10-15（安全）：来源也要过 media_guard。以前**不校验**，于是它成了
    "本机文件白名单"的旁路 —— 群友让机器人把 `C:\\Users\\li\\某张图.png` 压一下，
    产物落进 `data/downloads/`（那是白名单目录），再发出去就一路畅通。
    现在只准压"机器人自己拿得到的东西"（下载目录里的、或管理员本人的本机文件）。
    """
    source = str(params.get("path") or params.get("url") or "").strip()
    if not source:
        return {"ok": False, "error": "missing_path"}
    source, why, hint = await asyncio.to_thread(
        media_guard.check_source, source, bool(getattr(ctx, "is_superuser", False)))
    if why:
        return {"ok": False, "error": why, "detail": hint}
    try:
        from ..image_tools import compress_image_file
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "unsupported", "detail": str(exc)[:120]}
    return compress_image_file(
        source,
        max_side=int(params.get("max_side") or 1280),
        quality=int(params.get("quality") or 82),
        out_dir=_downloads_dir())


async def _act_send_buttons(params, ctx) -> dict:
    """发一条带按钮的消息（QQ 官方键盘；发不了就诚实降级成文字列表）。

    `buttons` 形如 [{"id": "sign_in", "label": "签到", "data": "签到"}]；
    用户点了按钮会以 INTERACTION_CREATE 回来，AI 那边收到的就是"用户点了按钮：<data>"。
    """
    channel = getattr(ctx, "channel", None)
    if channel is None:
        return {"ok": False, "error": "channel_unavailable"}
    text = str(params.get("text") or "").strip()
    raw = params.get("buttons") or []
    items = []
    if isinstance(raw, list):
        for i, b in enumerate(raw[:12]):
            if isinstance(b, dict):
                items.append({
                    "id": str(b.get("id") or f"btn{i}")[:60],
                    "label": str(b.get("label") or b.get("text") or f"选项{i + 1}")[:20],
                    "data": str(b.get("data") or b.get("id") or b.get("label") or "")[:200],
                })
            elif isinstance(b, str) and b.strip():
                items.append({"id": f"btn{i}", "label": b.strip()[:20], "data": b.strip()[:200]})
    if not text or not items:
        return {"ok": False, "error": "missing_text_or_buttons"}

    sender = getattr(channel, "send_buttons", None)
    if sender is not None:
        try:
            ok = bool(await sender(text, items, int(params.get("per_row") or 3)))
        except Exception as exc:  # noqa: BLE001
            ok = False
            logger.info(f"[bridge] 发按钮失败，改发文字：{exc}")
        if ok:
            return {"ok": True, "action": "send_buttons", "count": len(items),
                    "clickable": True}
    # 降级：明确告诉 AI 这次是文字版，用户得自己打字回复
    lines = "\n".join(f"{i + 1}. {b['label']}" for i, b in enumerate(items))
    ok = await channel.send_text(f"{text}\n{lines}")
    if ok is False:      # 同 _act_send_message：只有"明确 False"才算失败（None = 已发出）
        return {"ok": False, "error": "send_failed", "detail": "通道返回发送失败"}
    return {"ok": True, "action": "send_buttons", "count": len(items),
            "clickable": False,
            "note": "这个通道不支持按钮，已改成文字选项，用户要打字回答"}


async def _deliver_to_platform(platform: str, target_id: str, text: str) -> dict:
    """兼容旧调用：已经改成统一走 cross_platform 的出口（身份校验也在那边）。"""
    return await _cross()._send(f"{platform}:{target_id}", platform, target_id,
                                "file", None, text=text)


def _cross():
    from . import cross_platform

    return cross_platform


async def _act_send_to_platform(params, ctx) -> dict:
    """把**文本**发到同一个人的另一个平台（身份绑定决定目标）。"""
    platform = str(params.get("platform") or "").strip().lower()
    text = str(params.get("text") or "").strip()
    if not platform or not text:
        return {"ok": False, "error": "missing_platform_or_text"}
    platform = {"官方qq": "qq_official", "微信": "wechat"}.get(platform, platform)
    result = await _cross().deliver_text(_subject_key(ctx), platform, text)
    if result.get("ok") and result.get("queued"):
        result["hint"] = "已安排发送（还没真发出去）；发完系统会告诉对方，别提前打包票"
    if result.get("ok"):
        result["action"] = "send_to_platform"
    return result


_FALLBACK_EXPLAINED: set = set()      # 已经解释过"这条通道收不了媒体"的目标


def _fallback_text(platform: str, kind: str, source: str, label: str) -> tuple:
    """返回 (要发的文本, 是不是首次解释)。首次解释一句，之后只给链接。"""
    key = f"{platform}"
    first = key not in _FALLBACK_EXPLAINED
    _FALLBACK_EXPLAINED.add(key)
    if first:
        return (f"（这段是转发的）：{label}这边收不了「{kind}」，先给你个链接：{source}", True)
    return (f"转发的链接：{source}", False)


async def _act_send_file_to_platform(params, ctx) -> dict:
    """把**任意文件**发到同一个人的另一个平台：图片/语音/视频/普通文件都能。"""
    platform = str(params.get("platform") or "").strip().lower()
    source = str(params.get("file") or params.get("url") or params.get("path") or "").strip()
    kind = str(params.get("kind") or "file").strip().lower()
    name = str(params.get("name") or "").strip()
    if not platform or not source:
        return {"ok": False, "error": "missing_platform_or_file"}
    platform = {"官方qq": "qq_official", "微信": "wechat"}.get(platform, platform)
    # 2026-10-15 安全闸：跨平台转发**同样**是"填什么发什么"—— 这里比发本群更危险，
    # 因为落地的是另一个平台（发出去就收不回来）。挡在取字节之前，连探测都不做。
    source, why, hint = await asyncio.to_thread(
        media_guard.check_source, source, bool(getattr(ctx, "is_superuser", False)))
    if why:
        return {"ok": False, "error": why, "detail": hint}
    # 2026-10-12：**先查这个通道收不收得了这类内容**，别等投递时才失败 ——
    # 否则模型会先跟用户说"发出去了"，过几秒系统才报失败，很难看。
    cross = _cross()
    try:
        if not cross.supports(platform, kind):
            # 2026-10-12：**自动降级** —— 目标通道收不了媒体时，把"说明 + 原图链接"当文本发过去。
            # （老板让把 QQ 的图发微信，微信只能收文字；干巴巴回一句"发不了"没什么用，
            #   给他一段能点的文字至少点得开。）
            label = cross.platform_name(platform)
            bridge, first_time = _fallback_text(platform, kind, source, label)
            res = await cross.deliver_text(_subject_key(ctx), platform, bridge)
            if res.get("ok"):
                res["fallback"] = "text_with_link"
                res["first_time"] = first_time
                res["hint"] = (
                    f"{label}收不了{kind}，已经把链接发过去了。"
                    + ("这是第一次：可以解释一句「微信这边发不了图，先给你链接」，"
                       "以后就别再解释。" if first_time
                       else "以前解释过了：只说「链接发你了、点开能看」，别再解释，"
                            "他要是问再说。"))
            else:
                res["hint"] = f"{label}收不了{kind}，连链接也没发出去。如实说发不了。"
            return res
    except Exception:  # noqa: BLE001 —— 查不了就照老路走
        pass
    result = await cross.deliver_media(_subject_key(ctx), platform, kind, source, name=name,
                                       allow_any_local=bool(getattr(ctx, "is_superuser", False)))
    if result.get("ok"):
        result["action"] = "send_file_to_platform"
    return result


async def _act_my_platforms(params, ctx) -> dict:
    """我现在绑定了哪些平台、各自能收什么（AI 拿它回答"你能发到哪"）。"""
    cross = _cross()
    subject = _subject_key(ctx)
    return {"ok": True, "action": "my_platforms",
            "platforms": cross.list_platforms(subject),
            "summary": cross.describe_capabilities(subject)}


def _subject_key(ctx) -> str:
    """这次对话的主体身份（platform:user_id）—— 跨平台投递的唯一依据。

    绝不从"最近联系人"之类的共享状态里取：那正是把 A 的消息发到 B 的根源。
    """
    extra = getattr(ctx, "extra", None) or {}
    return str(extra.get("user_key")
               or getattr(ctx, "user_key", "")
               or f"{getattr(ctx, 'platform', '')}:{getattr(ctx, 'user_id', '')}")



# ============================================================================
# 本地动作 → 给模型看的工具清单（2026-10-12 老板："要像 codex / DSH 那样会调工具"）
#
# 以前这里只有 `_ACTIONS`（能执行），**没有 schema（模型看不见）** —— 于是 24 个动作里
# 模型只知道运行时那 6 个，记忆/提醒/抓网页/发图这些能力等于不存在。
# 现在按通道权限裁剪后交给模型；跨平台发送仍走运行时的 send_to_my_platform（不在这里重复）。
# ============================================================================
_P = {  # 参数类型简写
    "s": "string",
    "i": "integer",
}


def _tool(name: str, desc: str, params: dict, required: list) -> tuple:
    props = {k: {"type": _P[v[0]], "description": v[1]} for k, v in params.items()}
    return name, {
        "description": desc,
        "parameters": {"type": "object", "properties": props, "required": list(required)},
    }


LOCAL_TOOL_SCHEMAS = dict([
    _tool("memory_write", "把关于这位用户**值得长期记住**的事实记下来（他说的偏好、习惯、重要约定…）。"
                         "只在确实值得记的时候用，别把闲聊都记。",
          {"fact": ("s", "要记住的一句话（第三人称、简短，如\"他不吃香菜\"）")}, ["fact"]),
    _tool("memory_read", "读这位用户的长期记忆（记过的事实、印象、承诺）。回答\"我之前说过啥\"这类问题前先读一下。",
          {"user": ("s", "留空=当前这位；也可以传他的平台 id")}, []),
    _tool("reminder_add", "给自己定一个定时提醒：到点主动去私聊这位用户说这句话。",
          {"minutes": ("i", "多少分钟后提醒（1~43200）"),
           "text": ("s", "到点要说的那句话")}, ["minutes", "text"]),
    _tool("fetch_text", "抓取一个网页的正文（自动去广告/导航），返回纯文本。要读文章、查资料时用。",
          {"url": ("s", "网页地址（http/https）"),
           "limit": ("i", "最多返回多少字，默认 4000")}, ["url"]),
    _tool("expand_link", "把一条链接展开成它的标题+摘要（短链接也能跟到底）。用户发链接问\"这是啥\"时用。",
          {"url": ("s", "要展开的链接")}, ["url"]),
    _tool("http_request", "发一个 HTTP 请求（GET/POST…）拿接口数据。只在确实需要调接口时用。",
          {"method": ("s", "GET / POST / PUT / DELETE"),
           "url": ("s", "接口地址")}, ["method", "url"]),
    _tool("send_image", "在这段对话里发一张图片（本地路径或图片地址）。",
          {"url": ("s", "图片地址"), "path": ("s", "本地图片路径"),
           "caption": ("s", "配一句话，可留空")}, []),
    _tool("send_file", "在这段对话里发一个文件（本地路径或下载地址）。",
          {"url": ("s", "文件地址"), "path": ("s", "本地文件路径"),
           "name": ("s", "显示的文件名，可留空")}, []),
    _tool("send_voice", "在这段对话里发一条语音。",
          {"url": ("s", "语音文件地址"), "path": ("s", "本地语音文件路径")}, []),
    _tool("send_to", "在这段对话里**指定对象**发一句话（群里 @ 某人、或发给某个私聊）。",
          {"target": ("s", "目标：群号 / 用户 id / \"@某人\" 的 qq 号"),
           "text": ("s", "要说的话")}, ["target", "text"]),
    _tool("list_friend_requests", "看现在有哪些人申请加你（还没处理的）。卖家/客服场景常用。",
          {}, []),
    _tool("approve_friend", "通过某个人的好友申请（先 list_friend_requests 拿 user_id）。",
          {"user_id": ("s", "申请人的 user_id（qq 号）"),
           "flag": ("s", "可选：申请单号，list 里没给就用 user_id")}, ["user_id"]),
    _tool("data_read", "读自己存过的一小块数据（键值对）。",
          {"name": ("s", "数据名字")}, ["name"]),
    _tool("data_write", "存一小块数据，下次还能读出来（记流水、计数器这类）。",
          {"name": ("s", "数据名字"), "text": ("s", "要存的内容")}, ["name", "text"]),
    # ---- 以下只有群管理权限的通道（QQ）才看得见 ----
    _tool("recall_message", "撤回一条群消息（需要群管理权限）。",
          {"message_id": ("s", "要撤回的消息 id")}, ["message_id"]),
    _tool("mute_user", "禁言某个群成员一段时间（需要群管理权限）。",
          {"target": ("s", "要禁言的人（qq 号）"),
           "minutes": ("i", "禁言分钟数，0=解除")}, ["target", "minutes"]),
    _tool("mute_all", "开启/关闭全员禁言（需要群管理权限）。",
          {"minutes": ("i", "禁言分钟数，0=解除全员禁言")}, ["minutes"]),
])

# 每个动作需要的权限（与下面 _ACTIONS_PERM 同一个口径，只列这里暴露的那些）
LOCAL_TOOL_PERM = {
    "memory_write": "memory", "memory_read": "memory",
    "reminder_add": "timer",
    "fetch_text": "network", "expand_link": "network", "http_request": "network",
    "send_image": "media", "send_file": "media", "send_voice": "media", "send_to": "send_message",
    "data_read": "", "data_write": "",
    "list_friend_requests": "", "approve_friend": "send_message",
    "recall_message": "group_admin", "mute_user": "group_admin", "mute_all": "group_admin",
}


def local_tool_schemas(msg=None) -> list:
    """插件自己的动作 → OpenAI 工具格式（按**通道权限**裁剪）。

    msg 为空时按"最全集合"给（旧调用方兼容），但绝不会给出比通道能力更多的工具。
    """
    if msg is None:
        perms = {"send_message", "memory", "network", "timer", "media", "group_admin"}
    else:
        try:
            perms = permissions_for(msg)
        except Exception:  # noqa: BLE001 —— 判定失败就给最小集合，绝不放大权限
            perms = {"send_message", "memory", "timer"}
    out = []
    for name, spec in LOCAL_TOOL_SCHEMAS.items():
        need = LOCAL_TOOL_PERM.get(name, "")
        if need and need not in perms:
            continue
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": spec["description"],
                "parameters": spec["parameters"],
            },
        })
    return out



async def _act_list_friend_requests(params, ctx) -> dict:
    """看有哪些待处理的好友申请（只读）。"""
    from . import friend_requests

    return {"ok": True, "pending": friend_requests.pending_list()}


async def _act_approve_friend(params, ctx) -> dict:
    """通过一个好友申请。"""
    from . import friend_requests

    # 2026-10-12：**必须挑支持这个接口的那条连接**。
    # 以前直接用 nonebot.get_bot() —— 微信（iLink）也连着时可能返回它，
    # 于是报「iLink Bot 暂不支持 API: set_friend_add_request」。
    candidates = []
    ch = getattr(ctx, "channel", None)
    own = getattr(ctx, "bot", None) or getattr(ch, "bot", None)
    if own is not None:
        candidates.append(own)
    try:
        from nonebot import get_bots

        candidates.extend(list(get_bots().values()))
    except Exception:  # noqa: BLE001
        try:
            from nonebot import get_bot

            candidates.append(get_bot())
        except Exception:  # noqa: BLE001
            pass
    bot = None
    try:
        from nonebot.adapters.onebot.v11 import Bot as _OneBotV11Bot
    except Exception:  # noqa: BLE001
        _OneBotV11Bot = None  # type: ignore

    def _is_onebot(b) -> bool:
        # 2026-10-12 踩坑：不能只判断"有没有这个方法" —— iLink（微信）适配器对**任何** API 名
        # 都返回一个可调用桩，于是会被误选，最后报「iLink Bot 暂不支持 API」。
        # 必须按**适配器类型**认人。
        if _OneBotV11Bot is not None and isinstance(b, _OneBotV11Bot):
            return True
        return "onebot.v11" in (type(b).__module__ or "").lower()

    # 优先挑"本次消息那条连接"（self_id 对上）—— 多协议端并存时别把动作发错线
    want = str((getattr(ctx, "extra", None) or {}).get("self_id") or "")
    onebots = [c for c in candidates if _is_onebot(c)]
    bot = next((c for c in onebots if want and str(getattr(c, "self_id", "")) == want), None)
    if bot is None:
        bot = onebots[0] if onebots else None
    if bot is None:
        return {"ok": False, "error": "no_bot",
                "hint": "现在没有支持「通过好友申请」的连接（只有第三方 OneBot/QQ 支持）"}
    return await friend_requests.approve(
        bot,
        user_id=str(params.get("user_id") or ""),
        flag=str(params.get("flag") or ""),
    )


_ACTIONS = {
    "send_message": _act_send_message,
    "recall_message": _act_recall_message,
    "mute_user": lambda params, ctx: _act_mute(params, ctx, False),
    "mute_all": lambda params, ctx: _act_mute(params, ctx, True),
    "http_request": _act_http_request,
    "data_write": _act_data_write,
    "data_read": _act_data_read,
    "reminder_add": _act_reminder_add,
    "memory_write": _act_memory_write,
    "memory_read": _act_memory_read,
    # G1 媒体动作
    "send_image": lambda params, ctx: _act_send_media(params, ctx, "image"),
    "send_file": lambda params, ctx: _act_send_media(params, ctx, "file"),
    "send_voice": lambda params, ctx: _act_send_media(params, ctx, "voice"),
    "send_music_card": lambda params, ctx: _act_send_media(params, ctx, "card"),
    # 发到"同一个人的另一个平台"（身份绑定关系；见 identity_bind / cross_platform）
    "send_to_platform": _act_send_to_platform,
    # 跨平台发文件（图片/语音/视频/普通文件，全类型）
    "send_file_to_platform": _act_send_file_to_platform,
    # 我现在绑了哪些平台、各自能收什么（只读）
    "my_platforms": _act_my_platforms,
    "list_friend_requests": _act_list_friend_requests,
    "approve_friend": _act_approve_friend,
    # G2 主动发到指定目标（带白名单）
    "send_to": _act_send_to,
    # 带按钮的消息（QQ 官方键盘；其它通道自动降级成文字选项）
    "send_buttons": _act_send_buttons,
    # G3 重活动作
    "expand_link": _act_expand_link,
    "fetch_text": _act_fetch_text,
    "download_file": _act_download_file,
    "compress_image": _act_compress_image,
}

# 动作需要的权限（None = 无门槛）；沙箱侧再查一遍
ACTION_PERMISSIONS = {
    "send_message": "send_message",
    "recall_message": "group_admin",
    "mute_user": "group_admin",
    "mute_all": "group_admin",
    "http_request": "network",
    "data_write": None,
    "data_read": None,
    "reminder_add": "timer",
    "memory_write": "memory",
    "memory_read": "memory",
    # 媒体：统一要 media 权限（manifest 里声明了才放行）
    "send_image": "media",
    "send_file": "media",
    "send_voice": "media",
    "send_music_card": "media",
    # 发带按钮的消息：文本+键盘算富媒体
    "send_buttons": "media",
    # 主动发到别处：既要 send_message，又要目标在机器人侧白名单里（动作内部再查一遍）
    "send_to": "send_message",
    # 发到"同一个人的另一个平台"：目标由身份绑定决定，不需要白名单
    "send_to_platform": "send_message",
    "send_file_to_platform": "send_message",
    "my_platforms": "",   # 只读查询，谁都能用
    "list_friend_requests": "",          # 只读
    "approve_friend": "send_message", # 通过好友申请
    # 重活动作：要联网权限
    "expand_link": "network",
    "fetch_text": "network",
    "download_file": "network",
    "compress_image": None,      # 本地图片压缩，不联网
}


async def do_action(action: str, params: dict, ctx) -> dict:
    """执行一个工具/沙箱插件请求的动作（主程序侧，全部要过校验）。"""
    handler = _ACTIONS.get(action)
    if handler is None:
        return {"ok": False, "error": "unknown_action", "action": str(action)}
    needed = ACTION_PERMISSIONS.get(action)
    if needed and not ctx.can(needed):
        return {"ok": False, "error": "permission_denied",
                "permission": needed, "action": str(action)}
    try:
        return await handler(dict(params or {}), ctx)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "action_error",
                "detail": str(exc)[:200], "action": str(action)}


async def execute_tool_with_actions(runtime, name: str, args: dict, ctx) -> str:
    """统一执行入口：沙箱插件走子进程，普通能力包的动作排队后由主程序执行。

    普通工具（官方能力包）是同步函数，没法在函数里 await 发送动作，
    所以先排队、工具返回后由这里真正执行，再把真实回执合并进结果 ——
    "工具说成功、动作其实失败"会被如实写回去。
    """
    from qbotmanager.core.agent.tool import merge_effect_report

    spec = runtime.registry.get(name)
    ctx.effect_results.clear()
    queued = []
    if spec is not None and getattr(spec, "sandbox", False):
        ctx.extra["pack_id"] = spec.pack_id
        # 2026-10-16（对抗性复核 platform/D）：子进程里的 ctx 权限已经收窄到
        # 「manifest ∪ 工具声明 ∩ 通道」，但**动作层**（do_action）以前仍拿通道
        # 整体权限判 —— 声明只写 memory 的包照样能 ctx.send("mute_user") 禁言。
        # 这里给动作执行套一层"声明 ∩ 通道"的壳，让两条路的口径一致。
        _meta = runtime.registry.pack_meta(spec.pack_id) or {}
        _allowed = set(_meta.get("permissions") or []) | set(
            getattr(spec, "permissions", None) or [])

        class _NarrowedCtx:
            """只把 can() 收窄成声明书里那几项，其余属性原样透传给真 ctx。"""

            def __init__(self, inner):
                self._inner = inner

            def can(self, perm):
                return str(perm) in _allowed and bool(self._inner.can(perm))

            def __getattr__(self, item):
                return getattr(self._inner, item)

        async def host(action, params, tool_ctx):
            # 沙箱里是同步一问一答，这里顺手记一份回执：
            # 插件如果"动作失败了还说成功"，结果一样会被改回真相。
            res = await do_action(action, params, _NarrowedCtx(tool_ctx))
            tool_ctx.effect_results.append(res)
            return res

        result = await runtime.execute_async(name, args, ctx, host=host)
        return merge_effect_report(result, ctx.effect_results)
    ctx.bind_send(lambda action, prm: (
        queued.append((str(action), dict(prm or {}))),
        {"ok": True, "queued": True, "action": str(action)})[1])
    if spec is not None and spec.pack_id:
        # 每个插件的数据各自一个目录，插件之间互相看不到对方的数据
        ctx.extra["pack_id"] = spec.pack_id
    try:
        result = runtime.execute(name, args, ctx)
    finally:
        ctx.bind_send(None)
    if not queued:
        return result
    ctx.effect_results.clear()
    for action, params in queued:
        ctx.effect_results.append(await do_action(action, params, ctx))
    return merge_effect_report(result, ctx.effect_results)


class BridgeStore:
    """给工具用的 store：记忆 + 提醒都落到真实文件。"""

    def __init__(self, ctx):
        self._ctx = ctx

    def add_reminder(self, minutes, text, target=""):
        return add_reminder(minutes, text, target, self._ctx)

    def remember(self, user, fact):
        res = memory_write(fact, self._ctx)
        if not res.get("ok"):
            raise RuntimeError(str(res.get("error") or "memory_failed"))
        return True

    def fetch_memory(self, user=""):
        res = memory_read(user, self._ctx)
        if not res.get("ok"):
            raise RuntimeError(str(res.get("error") or "memory_failed"))
        return res.get("memory")


def build_tool_context(msg, channel, memory_enabled: bool = True) -> ToolContext:
    """按当前消息/通道建工具上下文（旧调用方不传 platform 时不过滤）。"""
    perms = permissions_for(msg)
    if not memory_enabled:
        perms.discard("memory")
    scene = str(getattr(msg, "scene", "") or "")
    room_id = str(getattr(msg, "room_id", "") or "")
    ctx = ToolContext(
        permissions=perms,
        platform=str(getattr(msg, "platform", "") or ""),
        scene=scene,
        user_id=str(getattr(msg, "user_id", "") or ""),
        group_id=room_id if scene == "group" else "",
        nickname=str(getattr(msg, "nickname", "") or ""),
        is_superuser=bool(getattr(msg, "is_superuser", False)),
        extra={"room_id": room_id,
               # 跨平台发送认人用：platform:user_id（和身份绑定同一套 key）
               "user_key": f"{str(getattr(msg, 'platform', '') or '')}:"
                           f"{str(getattr(msg, 'user_id', '') or '')}",
               # 本次消息来自哪个协议端（2026-10-12：多连接并存时按它挑对的连接发动作）
               "self_id": str(getattr(getattr(msg, "raw", None), "self_id", "") or ""),
               # 喊话人在本群的角色（member/admin/owner）。官方通道由适配器从事件
               # author.member_role 带上来；OneBot 侧没有这个字段（要去查接口）→ 空串。
               # 禁言那道"谁喊都动手"的安全闸两种通道都用它判定。
               "member_role": str((getattr(msg, "extra", None) or {}).get("member_role") or ""),
               "at_others": bool((getattr(msg, "extra", None) or {}).get("at_others"))},
        channel=channel,
    )
    ctx.store = BridgeStore(ctx)
    ensure_reminder_loop()
    return ctx
