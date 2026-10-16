# -*- coding: utf-8 -*-
"""QQ 官方通道一键体检：把"机器人不说话"的几种原因逐条查出来，用大白话给结论。

用户遇到的现象都一样（群里 @ 没反应 / 私聊不回），但原因可能有七八种：
没填凭证、凭证填错、进程没起、平台没连上、只收 @ 消息、AI 总开关关了、
群聊回话开关关了、模型没配、沙箱里没有测试成员…… 让用户挨个猜是不现实的。

这里只做"读"：读设置、读进程状态、读插件写的状态文件，不改任何东西。
**不打印任何密钥**，只说"填了没填"。
"""
from __future__ import annotations

from . import ai_config

OK = "ok"
BAD = "bad"
INFO = "info"


def _add(rows, name, level, text, hint=""):
    rows.append({"name": name, "level": level, "text": text, "hint": hint})


def run_checks(settings, manager) -> list[dict]:
    """跑一遍体检，返回 [{name, level, text, hint}]（level: ok/bad/info）。"""
    rows: list[dict] = []
    channel = str(getattr(settings, "qq_channel", "") or "official").lower()

    if channel != "official":
        _add(rows, "通道", INFO, "当前用的是第三方 OneBot 协议端，不是官方通道",
             "这份体检只针对「QQ 官方机器人」；要体检官方通道，"
             "先在「接入方式」里切到官方并保存。")
        return rows

    # 1) 凭证填了没（绝不回显内容）
    appid = str(getattr(settings, "qq_official_appid", "") or "").strip()
    token = str(getattr(settings, "qq_official_token", "") or "").strip()
    secret = str(getattr(settings, "qq_official_secret", "") or "").strip()
    missing = [n for n, v in (("AppID", appid), ("Token", token), ("AppSecret", secret)) if not v]
    if missing:
        _add(rows, "凭证", BAD, "还缺：" + "、".join(missing),
             "去 q.qq.com → 你的机器人 → 开发设置里把这三样抄过来，"
             "填到下面「QQ 通道配置」里点保存，再重启 NoneBot。")
    else:
        _add(rows, "凭证", OK, "AppID / Token / AppSecret 都填好了")

    # 2) 环境（沙箱只有测试成员算数，最容易白试半天）
    if bool(getattr(settings, "qq_official_sandbox", False)):
        _add(rows, "环境", INFO, "沙箱环境（只有你加进测试成员的号才生效）",
             "沙箱里别的成员 @ 它是没反应的；要对外用就得走正式环境+平台审核。")
    else:
        _add(rows, "环境", OK, "正式环境")

    # 3) 机器人进程
    try:
        bot_on = bool(manager.bot_running())
    except Exception:  # noqa: BLE001
        bot_on = False
    if bot_on:
        _add(rows, "机器人进程", OK, "NoneBot 在跑")
    else:
        _add(rows, "机器人进程", BAD, "NoneBot 没在跑",
             "点页面上方「启动全部」，等 10~20 秒再看连接状态。")

    # 4) 平台连接
    try:
        state = manager.qq_login_state() or {}
        connected = bool(state.get("connected"))
        reason = str(state.get("reason") or "").strip()
    except Exception:  # noqa: BLE001
        connected, reason = False, ""
    if connected:
        _add(rows, "连接", OK, "已经连上 QQ 开放平台" + (f"（{reason}）" if reason else ""))
    else:
        _add(rows, "连接", BAD, "没连上" + (f"：{reason}" if reason else ""),
             "凭证填错、Token 过期、或者机器人没启动都会这样；先确认凭证，再重启一次 NoneBot。")

    # 5) 收消息模式（全量 / 仅 @ / 还没收到过）
    group_state = ai_config.read_qq_group_state(settings)
    mode = str(group_state.get("mode") or "none")
    if mode == "full":
        _add(rows, "群消息", OK, ai_config.qq_group_state_text(group_state))
    elif mode == "at_only":
        _add(rows, "群消息", INFO, ai_config.qq_group_state_text(group_state),
             ai_config.qq_group_state_hint(group_state))
    else:
        _add(rows, "群消息", INFO, ai_config.qq_group_state_text(group_state),
             "把机器人拉进一个群，在群里 @ 它一次；收到后这里会显示真实的收消息模式。")

    # 6) AI 总开关 + 群聊回话开关
    if not bool(getattr(settings, "ai_enabled", True)):
        _add(rows, "AI 总开关", BAD, "「AI 大脑」总开关是关的 —— 机器人不会回任何话",
             "在本页上面的「AI 大脑」里打开。")
    else:
        _add(rows, "AI 总开关", OK, "AI 大脑已启用")
    if not ai_config.read_group_chat_enabled(settings):
        _add(rows, "群聊回话", BAD, "「允许在群里回话」是关的 —— 群里一律不吭声",
             "在「AI 大脑 → 人设与主动聊天」里打开「允许在群里回话」。")
    else:
        groups = ai_config.read_manager(settings).get("enabled_groups") or []
        _add(rows, "群聊回话", OK,
             "已允许（不限群）" if not groups else f"已允许（只限 {len(groups)} 个群）")

    # 7) 模型配置（没配模型 = 收得到消息也回不出来）
    cfg = ai_config.current_config(settings)
    if not str(cfg.get("api_key") or "").strip() or not str(cfg.get("model") or "").strip():
        _add(rows, "AI 模型", BAD, "还没配好 AI 接口（缺 API Key 或模型名）",
             "在「AI 大脑」里填接口地址 / Key / 模型，保存后重启机器人。")
    else:
        _add(rows, "AI 模型", OK, "已配置：" + str(cfg.get("model") or ""))

    return rows


def summary(rows: list[dict]) -> str:
    """一句话总结，放在体检窗口顶部（结论在前）。"""
    bad = sum(1 for r in rows if r["level"] == BAD)
    if bad == 0:
        return "没发现问题：该填的都填了、机器人也在跑。群里 @ 它试试，看状态卡那一行显示什么模式。"
    return f"查到 {bad} 处会导致「不说话」的问题，按下面带 ✗ 的条目一条条处理就行。"
