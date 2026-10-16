# -*- coding: utf-8 -*-
"""好友申请：自动通过 + 给模型用的工具（2026-10-12，卖家场景）。

- 收到 `request.friend`（OneBot V11）→ 记一份待处理清单；
- `auto_approve_friend` 开着 → 直接通过；
- 关着 → 只记录，等模型/人工处理（`approve_friend` 工具可指定用户通过）。

2026-10-12 补：清单**同时落一份盘**（`<机器人目录>/data/friend_requests.json`），
好让控制台（另一个进程）也能"看得见好友申请"。为什么不走内存：
控制台是 uvicorn 进程、插件在 nonebot 进程里，两个进程不共享内存，
只能靠文件。落盘是**只写不读**的单向桥：机器人写、控制台读，
控制台绝不会替机器人做动作（见 astroswarm_linux/console_ext.friend_requests_state）。
"""
import json
import os
import time
from pathlib import Path

from nonebot import logger

try:  # 只有 OneBot V11 通道才有好友申请
    from nonebot.adapters.onebot.v11 import Bot as OneBotV11Bot
except Exception:  # noqa: BLE001
    OneBotV11Bot = None  # type: ignore

# 待处理：user_id -> {flag, comment, at}
PENDING: dict = {}
PENDING_TTL = 3600          # 一小时前的申请不再展示


def pending_file() -> Path:
    """落盘位置：与机器人自己那份 aichat_manager.json 同一个根。

    优先环境变量 `ASTROSWARM_BOT_DIR`（部署端注入）；没有就按
    "插件配置文件的同级往上找 bot 根"退一步 —— 拿不到也不能抛，落盘只是附加能力。
    """
    env = str(os.environ.get("ASTROSWARM_BOT_DIR") or "").strip()
    if env:
        return Path(env) / "data" / "friend_requests.json"
    try:
        from .manager import MANAGER_FILE   # 例：<bot>/config/ai/aichat_manager.json

        p = Path(MANAGER_FILE)
        for parent in p.parents:
            if parent.name == "bot":
                return parent / "data" / "friend_requests.json"
        return p.parent.parent / "data" / "friend_requests.json"
    except Exception:  # noqa: BLE001
        return Path.cwd() / "data" / "friend_requests.json"


def _write_pending_file() -> None:
    """把当前待处理清单写一份 JSON（原子写；失败只记 debug，绝不影响主流程）。"""
    try:
        rows = pending_list()
        path = pending_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        payload = json.dumps({"updated_at": time.time(), "pending": rows},
                             ensure_ascii=False, indent=2)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[friend] 待处理清单落盘失败（不影响处理）：{e}")


def remember(user_id: str, flag: str, comment: str = "") -> None:
    PENDING[str(user_id)] = {"flag": str(flag), "comment": str(comment or ""),
                             "at": time.time()}
    _write_pending_file()



def pending_list(max_age: float = PENDING_TTL) -> list:
    now = time.time()
    out = []
    for uid, info in list(PENDING.items()):
        if now - float(info.get("at") or 0) > max_age:
            PENDING.pop(uid, None)
            continue
        out.append({"user_id": uid, "comment": info.get("comment", "")})
    return out


async def approve(bot, user_id: str = "", flag: str = "") -> dict:
    """通过一个好友申请。给 user_id 或 flag 都行（flag 优先）。"""
    info = PENDING.get(str(user_id)) or {}
    use_flag = str(flag or info.get("flag") or "")
    if not use_flag:
        return {"ok": False, "error": "no_pending_request",
                "hint": "手头没有这个人的待处理申请（可能已经处理过或超时了）"}
    # 2026-10-12 踩坑：别在**导入期**判 isinstance —— 插件加载时适配器可能还没注册，
    # OneBotV11Bot 会是 None，于是永远返回通道不支持。直接调、按错误兜底才对。
    setter = getattr(bot, "set_friend_add_request", None)
    if setter is None:
        return {"ok": False, "error": "channel_unsupported",
                "hint": "当前通道没有「通过好友申请」这个接口（只有第三方 OneBot/QQ 有）"}
    try:
        await setter(flag=use_flag, approve=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "call_failed", "detail": str(e)[:160]}
    PENDING.pop(str(user_id), None)
    _write_pending_file()          # 处理完立刻同步给控制台那份只读清单
    logger.info(f"[friend] 已通过好友申请 user={user_id}")
    return {"ok": True, "user_id": str(user_id), "approved": True}


async def handle_friend_request(bot, event) -> None:
    """OneBot V11 的 request.friend 事件入口。

    2026-10-15（审计 X2）：入口先过共享开关 —— 客户端把 AI 总开关/QQ 平台开关
    关掉之后，以前这里**照样自动通过好友申请**（"关了还在动"最典型的一种）。
    申请本身仍然记下来（只读清单，管理员能在控制台看到并手动处理）。
    """
    user_id = str(getattr(event, "user_id", "") or "")
    flag = str(getattr(event, "flag", "") or "")
    comment = str(getattr(event, "comment", "") or "")
    if not user_id or not flag:
        return
    remember(user_id, flag, comment)
    logger.info(f"[friend] 收到好友申请 user={user_id} 验证消息={comment[:60]!r}")
    try:
        from . import gates

        blocked = gates.blocked_reason("qq")
    except Exception:  # noqa: BLE001
        blocked = ""
    if blocked:
        logger.info(f"[friend] {blocked} → 不自动通过（申请已记下，可在客户端手动处理）")
        return
    try:
        from .manager import chat_manager

        auto = chat_manager.is_auto_approve_friend_enabled()
    except Exception:  # noqa: BLE001 —— 读不到开关就按"不自动"处理，绝不擅自通过
        auto = False
    if auto:
        res = await approve(bot, user_id=user_id, flag=flag)
        logger.info(f"[friend] 自动通过结果: {res}")


# ---------------------------------------------------------------- 新朋友见面问候
_GREETED: dict = {}          # user_id -> 时间戳（同一人 60 秒内只问候一次）
GREET_COOLDOWN = 60.0
ADD_NOTICE_TEXTS = ("请求添加你为好友", "请求加你为好友", "请求添加好友")


def should_greet(user_id: str) -> bool:
    now = time.time()
    last = float(_GREETED.get(str(user_id)) or 0)
    if now - last < GREET_COOLDOWN:
        return False
    _GREETED[str(user_id)] = now
    return True


def is_add_notice(text: str) -> bool:
    """QQ 那个"请求添加你为好友"的系统通知，本质是加好友事件，不是用户在提问。"""
    t = str(text or "").strip()
    return any(k == t or k in t for k in ADD_NOTICE_TEXTS) and len(t) <= 24


async def greet_new_friend(bot, user_id: str, nickname: str = "") -> dict:
    """用**当前人设**生成一句见面语发给新朋友（AI 生成；失败就退回一句很短的兜底）。"""
    # 2026-10-15（审计 X2）：AI 关着的时候别打招呼 —— 以前这条旁路不过任何开关，
    # 关掉 AI 之后新好友还会收到"在的，你好呀"这种固定句。
    try:
        from . import gates

        blocked = gates.blocked_reason("qq")
    except Exception:  # noqa: BLE001
        blocked = ""
    if blocked:
        logger.info(f"[friend] {blocked} → 不打招呼")
        return {"ok": True, "skipped": "ai_disabled"}
    if not should_greet(user_id):
        return {"ok": True, "skipped": "already_greeted"}
    prompt = (f"（系统提示：{nickname or ('QQ ' + str(user_id))} 刚刚加你为好友，"
              f"这是你们第一次说话。）用你自己的人设，主动说一句见面语，一句话就够，"
              f"别念系统提示、别说「我看到了通知」这类话。")
    text = ""
    try:
        from .chat import get_chat_reply

        text = (await get_chat_reply([{"role": "user", "content": prompt}], is_group=False) or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[friend] 生成见面语失败: {e}")
    # 模型返回的可能是 {"reply": [...]} 这种壳 —— 必须先过回复格式化器，
    # 否则发出去的是原始 JSON（2026-10-12 模拟验收就是发了一串 JSON）。
    try:
        from . import reply_format

        segs = reply_format.reply_segments(text)
        if segs:
            text = str(segs[0]).strip()
    except Exception:  # noqa: BLE001
        pass
    if not text:
        text = "在的，你好呀。"
    # 发送方式二选一：bot（有 send_private_msg，notice 那条路径）或 ctx（当前通道，
    # 消息那条路径 —— handle_message 里根本没有 bot 对象）。
    try:
        sender = getattr(bot, "send_private_msg", None)
        if sender is not None:
            await sender(user_id=int(user_id), message=text)
        else:
            send_text = getattr(bot, "send_text", None)
            if send_text is None:
                return {"ok": False, "error": "no_sender",
                        "detail": "既不是 bot 也不是通道上下文，发不出去"}
            await send_text(text)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "send_failed", "detail": str(e)[:160]}
    logger.info(f"[friend] 已给新朋友打招呼 user={user_id}")
    return {"ok": True, "greeted": True, "text": text[:60]}
