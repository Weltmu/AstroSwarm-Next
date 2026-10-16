# -*- coding: utf-8 -*-
"""聊天里"自学"：AI 发现自己不会某件事时，当场给自己写个插件并装上。

老板 2026-10-03 的原话："每加入一个新功能都需要我更新程序…能不能把插件工坊弄一个
在聊天里内置的能力？AI 遇到自己没有的功能时，就自己 coding 一个插件。"

边界（想清楚再写）：
- **能用现有动作拼出来的需求**（查天气/记账/抓网页/发图/提醒…）→ 这里真能自己学会：
  走的就是「插件工坊」那条流水线（方案 → 生成 → 静态检查 → 沙箱试跑 → 安装），
  只是不再需要人点按钮。
- **需要主程序新增动作的需求**（跨平台发消息、撤回、禁言…）→ 这里**学不会**，
  必须由人给主程序加动作；这种情况要如实告诉用户"我做不到，缺什么"。
  判断方法：把主程序当前的动作清单一起喂给生成器（工坊本来就这么做），
  生成器会自己说"做不到"，我们把它的原话转给用户。

安全：只有**机器人主人/管理员**能触发（不然群里谁都能烧你的 token）；
生成过程是异步的（要几分钟），先回一句"我去学一下"，装好了再回话。
"""
from __future__ import annotations

import asyncio
import time

from nonebot import logger

# 待办队列：工具（同步）里只入队，真正干活在聊天回合结束后由 main 循环跑
_pending: list = []
_running = False
MAX_NEED = 400


def enabled() -> bool:
    """开关：默认开（在客户端「AI 大脑」里可以关掉）。"""
    try:
        from .manager import chat_manager

        return bool(chat_manager.is_self_forge_enabled())
    except Exception:  # noqa: BLE001
        return True


def can_trigger(is_superuser: bool) -> bool:
    """只有主人/管理员能触发（防群里有人拿它烧 token）。"""
    return bool(is_superuser)


def enqueue(need: str, ctx, sender: str = "", scene: str = "") -> dict:
    """把"学一个新能力"排进队列（同步、立刻返回，别卡住回话）。"""
    text = " ".join(str(need or "").split())[:MAX_NEED]
    if not text:
        return {"ok": False, "error": "empty_need"}
    if not enabled():
        return {"ok": False, "error": "self_forge_disabled",
                "hint": "主人把「AI 自己在聊天里学新能力」关掉了"}
    if len(_pending) >= 3:
        return {"ok": False, "error": "busy", "hint": "上一条还在学，稍等"}
    _pending.append({"need": text, "ctx": ctx, "sender": sender, "scene": scene,
                     "at": time.time()})
    return {"ok": True, "queued": True, "need": text,
            "note": "已经排上队：我先去写，装好了会主动回你一句（几分钟）"}


async def run_pending(send=None) -> int:
    """把队列里的任务跑完（在聊天回合结束后调用，别阻塞当前回复）。

    send(text) 由调用方给：往**触发这次学习的那个会话**回话。
    返回处理条数。
    """
    global _running
    if _running or not _pending:
        return 0
    _running = True
    done = 0
    try:
        while _pending:
            job = _pending.pop(0)
            ctx = job.get("ctx")
            need = job["need"]
            reply = send
            if reply is None and ctx is not None:
                async def reply(text, _ctx=ctx):        # noqa: ANN001
                    # 2026-10-12：ToolContext 没有 send_text（只有 channel），
                    # 以前这里直接炸 AttributeError，回执发不出还带崩整个投递循环。
                    # 现在按"有什么用什么"来，实在没有就安静跳过回执，绝不崩。
                    ch = getattr(_ctx, "channel", None)
                    if ch is not None and hasattr(ch, "send_text"):
                        await ch.send_text(text)
                        return
                    sender = getattr(_ctx, "send_text", None)
                    if callable(sender):
                        await sender(text)
            if reply is None:
                continue
            try:
                # 2026-10-13（老板：别让用户花完 token 才后悔）：先报工程量 + 预估消耗
                est_line = ""
                try:
                    from qbotmanager.core.workshop import estimator as _wse

                    _est = _wse.estimate(_settings(), need)
                    est_line = "\n（" + _wse.format_estimate(_est, short=True) + "）"
                except Exception:  # noqa: BLE001 —— 估算失败照常干活
                    est_line = ""
                await reply("这个我原本不会，我去给自己写个插件试试，几分钟后告诉你结果。"
                            + est_line)
                result = await _learn(need)
            except Exception as e:  # noqa: BLE001 —— 学不会也不能把聊天搞崩
                logger.warning(f"[forge] 自学失败：{e}")
                result = {"ok": False, "why": f"生成过程出错：{str(e)[:120]}"}
            done += 1
            if result.get("ok"):
                _tip = ""
                try:
                    from qbotmanager.core.workshop import estimator as _wse2

                    _line = _wse2.usage_line(result.get("usage") or {},
                                             _wse2.estimate(_settings(), need))
                    if _line:
                        _tip = "\n（" + _line + "）"
                except Exception:  # noqa: BLE001
                    _tip = ""
                await reply(
                    f"学会了：新插件「{result.get('name') or result.get('id')}」已经装上，"
                    f"现在可以再来一次刚才那件事。"
                    + (f"\n（说明：{result['summary']}）" if result.get("summary") else "")
                    + _tip)
            else:
                await reply(
                    "这个我做不到，原因：" + str(result.get("why") or "生成没通过检查")
                    + "\n（如果是「跨平台发消息 / 撤回 / 禁言」这类，得让主人给我加对应能力，"
                      "光写插件变不出来。）")
    finally:
        _running = False
    return done


async def _learn(need: str) -> dict:
    """真干活：调用工坊流水线生成 + 装上（丢线程，别卡事件循环）。"""
    def _work() -> dict:
        from qbotmanager.core import workshop

        class _S:      # 工坊只用到 root / bot_dir 之类，给个最小的设置对象
            pass

        settings = _settings()
        built = workshop.build(settings, need)
        if not built or not built.get("ok", True):
            return {"ok": False, "why": (built or {}).get("error") or "方案没通过"}
        plan = built.get("plan") or {}
        result = workshop.install(settings, built)
        return {
            "ok": True,
            "id": plan.get("id") or result.get("id") or "",
            "name": plan.get("name") or result.get("name") or "",
            "summary": str(plan.get("summary") or plan.get("desc") or "")[:120],
            "files": sorted((built.get("files") or {}).keys())[:8],
            "usage": built.get("usage") or {},
        }

    return await asyncio.to_thread(_work)


def _settings():
    """拿到机器人侧的 Settings（工坊要 root/bot_dir/plugin 目录）。"""
    from qbotmanager.core.settings import Settings

    # 机器人进程的 cwd 就是 bot 目录，它的上一级就是安装根
    from pathlib import Path

    root = Path.cwd().parent
    return Settings(root)


def status() -> dict:
    return {"pending": len(_pending), "running": _running, "enabled": enabled()}
