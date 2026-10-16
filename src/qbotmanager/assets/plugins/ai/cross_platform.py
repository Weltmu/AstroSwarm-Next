# -*- coding: utf-8 -*-
"""跨平台互通：唯一的出口（所有跨平台发送都必须走这里）。

老板 2026-10-04 的要求：
1. 基础互通：发消息、发文件（图片/语音/视频/普通文件，所有类型）；
2. 机器人能**跨平台主动聊天**；
3. **身份必须分清**："不要把发给 QQ 上其他人的消息也通过微信发给用户"。

所以这个模块只有一个原则，代码里也用唯一出口来保证：

    **任何一次跨平台投递，目标都必须是"这次对话的主体"（subject_key）
      自己绑定的身份；查不到就拒绝，绝不猜、绝不退回"上次聊过的人"。**

subject_key 一定是形如 `platform:user_id` 的原始身份（微信 ilink id / QQ openid /
第三方 QQ 号），由调用方从**当前这条消息**或**任务本身记录的目标**里取，不许从
"全局最近联系人"之类的共享状态里取 —— 那正是串台的根源。

投递能力：
- 文本：QQ 官方 `send_to_c2c` / 第三方 OneBot `send_private_msg` / 微信 iLink 主动消息；
- 图片、语音、视频、文件：QQ 官方走 media 上传（`file_image` 等），第三方走
  `upload_private_file` / `send_private_msg` 的媒体段，微信侧能发什么就发什么，
  发不了**如实报错**（错误码告诉调用方，转成人话给用户）。
"""
from __future__ import annotations

import asyncio
import base64
import mimetypes
import time
from pathlib import Path

from nonebot import logger

from . import media_guard

PLATFORM_NAMES = {
    "qq": "QQ（第三方协议端）",
    "qq_official": "QQ 官方机器人",
    "wechat": "微信",
    "feishu": "飞书",
    "telegram": "纸飞机",
}

KIND_IMAGE = "image"
KIND_VOICE = "voice"
KIND_VIDEO = "video"
KIND_FILE = "file"
KINDS = (KIND_IMAGE, KIND_VOICE, KIND_VIDEO, KIND_FILE)

# 各平台能收什么（做不到的如实拒绝，别假装成功）
# 微信 iLink 适配器目前只有 send_text（要 context_token，且只在对方 30 分钟窗口内），
# 所以微信侧**只能发文本**；QQ 官方与第三方 QQ 全类型都能发。
CAPS = {
    "qq_official": {KIND_IMAGE, KIND_VOICE, KIND_VIDEO, KIND_FILE},
    "qq": {KIND_IMAGE, KIND_VOICE, KIND_VIDEO, KIND_FILE},
    "wechat": set(),
}


def platform_name(platform: str) -> str:
    return PLATFORM_NAMES.get(str(platform or ""), str(platform or "未知平台"))


def supports(platform: str, kind: str) -> bool:
    return str(kind or "").lower() in CAPS.get(str(platform or ""), set())


def resolve_target(subject_key: str, platform: str) -> dict:
    """主体在目标平台上的身份 → {ok, target?, error?}。

    只认"绑定关系"：没绑定就返回 not_bound，让 AI 去教用户发「绑定」。
    """
    subject = str(subject_key or "").strip()
    want = str(platform or "").strip().lower()
    if not subject or ":" not in subject:
        return {"ok": False, "error": "bad_subject"}
    if not want:
        return {"ok": False, "error": "bad_platform"}
    try:
        from .identity import resolve as identity_resolve
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "identity_unavailable", "detail": str(exc)[:120]}
    for key in identity_resolve(subject):
        head, _, tail = str(key).partition(":")
        if head == want and tail:
            return {"ok": True, "target": tail, "target_key": key}
    return {"ok": False, "error": "not_bound", "platform": want,
            "hint": "他还没绑定这个平台：让他先发「绑定」拿码，再去那个平台发"
                    "「绑定 六位数字」。"}


def list_platforms(subject_key: str) -> list:
    """主体绑定了哪些平台（给 AI / 用户看）。"""
    try:
        from .identity import resolve as identity_resolve

        keys = identity_resolve(str(subject_key or ""))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for key in keys:
        head = str(key).split(":", 1)[0]
        if head not in out:
            out.append(head)
    return out


def _read_local(path_or_url: str) -> bytes | None:
    """读本地文件。

    **调用方必须先过 `media_guard.check_source`**（`_send` 里那道闸）——
    这里的路径已经被限定在机器人自己的产出目录里，本机其它文件到不了这一步。
    """
    p = Path(str(path_or_url or ""))
    if p.is_file():
        try:
            return p.read_bytes()
        except OSError:
            return None
    return None


def _guess_name(path_or_url: str, kind: str) -> str:
    name = Path(str(path_or_url or "").split("?")[0]).name
    if not name:
        name = {"image": "image.png", "voice": "voice.mp3",
                "video": "video.mp4", "file": "file.bin"}.get(kind, "file.bin")
    return name[:80]


async def _fetch_remote(url: str, limit: int = 20 * 1024 * 1024) -> bytes | None:
    """把 http(s) 上的文件取回来（跨平台转发要用真实字节）。

    2026-10-15 修（安全）：以前这里是裸 `urlopen` —— 不查内网、**不复查跳转**，
    等于给"把内网服务当文件发到另一个平台"开了条直路。现在统一走
    `web_fetch.open_checked`（逐跳复查）。唯一放开的是 `media_guard` 认过出处的
    地址：NapCat / iLink 给自己的图片地址常常就长在本机端口上，不放开就没法
    "把 QQ 上的图转到微信"。
    """
    def _do() -> bytes | None:
        from . import web_fetch

        try:
            with web_fetch.open_checked(
                    url, 20, allow_private=media_guard.is_trusted_origin(url)) as resp:
                return resp.read(limit)
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[cross] 取远程文件失败 {url}: {exc}")
            return None

    return await asyncio.to_thread(_do)


async def deliver_text(subject_key: str, platform: str, text: str) -> dict:
    """跨平台发一条文本（唯一出口：目标必须来自 subject 自己的绑定）。"""
    target = resolve_target(subject_key, platform)
    if not target.get("ok"):
        return target
    body = str(text or "").strip()
    if not body:
        return {"ok": False, "error": "empty_text"}
    return await _send(target["target_key"], platform, target["target"], KIND_FILE,
                       None, text=body)


async def deliver_media(subject_key: str, platform: str, kind: str, source: str,
                        name: str = "", allow_any_local: bool = False) -> dict:
    """跨平台发一个文件（图片/语音/视频/普通文件都走这里）。"""
    kind = str(kind or KIND_FILE).lower()
    if kind not in KINDS:
        return {"ok": False, "error": "bad_kind", "kinds": list(KINDS)}
    target = resolve_target(subject_key, platform)
    if not target.get("ok"):
        return target
    if not supports(platform, kind):
        return {"ok": False, "error": "platform_cannot",
                "hint": f"{platform_name(platform)} 收不了这类内容"}
    return await _send(f"{platform}:{target['target']}", platform, target["target"],
                       kind, source, name=name, allow_any_local=allow_any_local)


async def _send(target_key: str, platform: str, target_id: str, kind: str,
                source: str | None, text: str = "", name: str = "",
                allow_any_local: bool = False) -> dict:
    """真投递。`target_key` 只用于日志，方便排查串台。

    这里也是**出站媒体的最后一道闸**（队列重放、兼容入口都从这条路出去）：
    本地路径只有机器人自己的产出目录放行，网址只认公网或协议端给过的地址。
    """
    try:
        from nonebot import get_bots
    except Exception as exc:  # noqa: BLE001 —— 测试环境 / 没有 nonebot 运行时时如实报错
        return {"ok": False, "error": "runtime_unavailable", "detail": str(exc)[:120]}

    bots = []
    for bot in (get_bots() or {}).values():
        module = type(bot).__module__ or ""
        if platform == "qq_official" and "adapters.qq" in module:
            bots.append(bot)
        elif platform == "qq" and "onebot" in module:
            bots.append(bot)
        elif platform == "wechat" and "ilink" in module.lower():
            bots.append(bot)
    if not bots:
        return {"ok": False, "error": "platform_offline",
                "hint": f"{platform_name(platform)} 现在没在跑"}
    bot = bots[0]

    try:
        if not source:                                  # 纯文本
            if platform == "qq_official":
                await bot.send_to_c2c(openid=target_id, message=text)
            elif platform == "qq":
                await bot.call_api("send_private_msg", user_id=int(target_id), message=text)
            else:
                ok = await _send_wechat(bot, target_id, text, None)
                if not ok:
                    return {"ok": False, "error": "wechat_unavailable",
                            "hint": "微信这边只能被动回话（对方 30 分钟内说过话）"}
            return {"ok": True, "delivered": target_key, "kind": "text"}

        # 媒体：先过安全闸，再拿真实字节（本地路径或 http(s) 链接都行）
        checked, why, hint = await asyncio.to_thread(
            media_guard.check_source, source, allow_any_local)
        if why:
            return {"ok": False, "error": why, "detail": hint, "delivered": None}
        source = checked
        data = _read_local(source)
        if data is None and str(source).startswith(("http://", "https://")):
            data = await _fetch_remote(str(source))
        if data is None:
            return {"ok": False, "error": "source_unreadable", "source": str(source)[:120]}
        if len(data) > 20 * 1024 * 1024:
            return {"ok": False, "error": "too_large", "bytes": len(data)}

        file_name = name or _guess_name(source, kind)
        if platform == "qq_official":
            segment = _qq_official_segment(kind, data, file_name)
            await bot.send_to_c2c(openid=target_id, message=segment)
        elif platform == "qq":
            b64 = "base64://" + base64.b64encode(data).decode()
            if kind == KIND_FILE:
                await bot.call_api("upload_private_file", user_id=int(target_id),
                                   file=b64, name=file_name)
            else:
                seg = {"image": "image", "voice": "record", "video": "video"}[kind]
                await bot.call_api("send_private_msg", user_id=int(target_id),
                                   message=[{"type": seg, "data": {"file": b64}}])
        else:
            ok = await _send_wechat(bot, target_id, "", data)
            if not ok:
                return {"ok": False, "error": "wechat_unavailable",
                        "hint": "微信这边发媒体有限制，暂时发不出去"}
        return {"ok": True, "delivered": target_key, "kind": kind,
                "bytes": len(data), "name": file_name}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[cross] 投递失败 {target_key} {kind}: {exc}")
        return {"ok": False, "error": "send_failed", "detail": str(exc)[:160],
                "delivered": None}


def _qq_official_segment(kind: str, data: bytes, name: str):
    """QQ 官方媒体的消息段（图片走 file_image，语音 file_audio，视频 file_video）。"""
    from nonebot.adapters.qq import MessageSegment

    b64 = "base64://" + base64.b64encode(data).decode()
    if kind == KIND_IMAGE:
        return MessageSegment.image(b64)
    if kind == KIND_VOICE:
        return MessageSegment.audio(b64)
    if kind == KIND_VIDEO:
        return MessageSegment.video(b64)
    return MessageSegment.file(b64)


async def _send_wechat(bot, target_id: str, text: str, data: bytes | None) -> bool:
    """微信 iLink 侧发送：走适配器自带的 client.send_text（要 context_token）。

    复用主动聊天那套（proactive）：token 缓存 + 30 分钟窗口判断都在那边。
    媒体发不了（适配器没有接口）—— 调用方在 supports() 那一步就会拦掉。
    """
    if data is not None:
        return False
    try:
        from . import proactive

        info = proactive._wx_tokens().get(target_id) or {}          # noqa: SLF001
        token = str(info.get("context_token") or "")
        if not token:
            logger.info(f"[cross] 微信发送跳过：没有 {target_id} 的会话 token")
            return False
        client = proactive._wx_client()                             # noqa: SLF001
        if client is None:
            return False
        return bool(await client.send_text(target_id, token, text))
    except Exception as exc:  # noqa: BLE001
        logger.info(f"[cross] 微信发送异常: {exc}")
        return False


def own_platform(subject_key: str) -> str:
    return str(subject_key or "").split(":", 1)[0]


def other_platforms(subject_key: str) -> list:
    """他**另外**绑了哪些平台（不含他当前说话的这个）。

    为什么要排除自己：用户问"你能发到哪"，答"可以发到微信"没意义（他现在就在微信），
    而且没绑定的人也会被答成"可以发到微信"，听着像已经能跨平台了。
    """
    mine = own_platform(subject_key)
    return [p for p in list_platforms(subject_key) if p and p != mine]


def describe_capabilities(subject_key: str) -> str:
    """给 AI 的一句话能力说明（知道现在能替这个人做什么）。"""
    platforms = other_platforms(subject_key)
    if not platforms:
        return "他还没绑定其它平台（让他发「绑定」拿码，再去那个平台发「绑定 六位数字」）。"
    bits = []
    for platform in platforms:
        kinds = CAPS.get(platform, set())
        label = platform_name(platform)
        bits.append(f"{label}：可发文本" + ("、图片、语音、视频、文件"
                                        if len(kinds) >= 4 else
                                        "、" + "、".join(sorted(kinds)) if kinds else "（暂不支持媒体）"))
    return "可以替他发到：" + "；".join(bits) + "。"


# ---------------------------------------------------------------- 内置工具用的队列
# 聊天工具是同步的、发送是异步的：和"自学"（skill_forge）同一套路 ——
# 工具里只入队并立刻回一句，真正的投递在回合结束后跑，结果再回话给用户。
_pending: list = []
_running = False


_RECENT_SENDS: dict = {}          # (platform, 指纹) -> 时间戳
_DEDUPE_WINDOW = 120.0            # 两分钟内发过一模一样的东西 → 不再重发


def _fingerprint(kind: str, source: str, text: str) -> str:
    """投递内容的指纹（图片/文件用地址，文本用正文）。"""
    if kind == "text":
        return f"text:{(text or '').strip()[:200]}"
    return f"{kind}:{(source or '').strip()[:400]}"


def _is_recent_duplicate(platform: str, kind: str, source: str, text: str) -> bool:
    key = (str(platform or ""), _fingerprint(kind, source, text))
    now = time.time()
    last = float(_RECENT_SENDS.get(key) or 0)
    if now - last < _DEDUPE_WINDOW:
        return True
    _RECENT_SENDS[key] = now
    # 顺手清掉过期项，别让表长胖
    for k in [k for k, ts in _RECENT_SENDS.items() if now - float(ts) > _DEDUPE_WINDOW * 5]:
        _RECENT_SENDS.pop(k, None)
    return False


def enqueue(ctx, subject_key: str, platform: str, kind: str = "text",
            source: str = "", text: str = "", name: str = "",
            allow_any_local: bool = False) -> dict:
    """排一次跨平台投递（同步、立刻返回）。

    带 source（要转发的文件）时先做一道**不查 DNS 的**来源预筛：本地路径只有
    机器人自己的产出目录放行，字面量的内网/本机地址直接拒。权威检查在投递前
    （`_send` 里，异步线程里跑，会解析域名），这里只是为了立刻给模型一句实话。
    """
    global _running
    if len(_pending) >= 5:
        return {"ok": False, "error": "busy", "hint": "上一条还在发，稍等"}
    if source:
        _target, why, hint = media_guard.precheck_source(
            source, allow_any_local=allow_any_local)
        if why:
            return {"ok": False, "error": why, "detail": hint}
    # 2026-10-12：同一份东西短时间内别发两遍（老板实测：一张图被转了两次）
    if _is_recent_duplicate(platform, kind, source, text):
        logger.info(f"[cross] 两分钟内发过同样的内容 → 跳过重复投递"
                    f"（{platform} {_fingerprint(kind, source, text)[:60]}）")
        return {"ok": True, "skipped": "duplicate",
                "hint": "刚才已经发过一模一样的东西了，这次跳过；跟他说一句「这张刚发过」就够了"}
    if not subject_key or ":" not in subject_key:
        return {"ok": False, "error": "bad_subject"}
    try:
        from .manager import chat_manager

        if not chat_manager.is_cross_platform_enabled():
            return {"ok": False, "error": "cross_disabled",
                    "hint": "跨平台互通被关掉了（客户端 AI 大脑 → 跨平台互通）"}
    except Exception:  # noqa: BLE001 —— 读不到开关就当开着，别把功能卡死
        pass
    _pending.append({"ctx": ctx, "subject": subject_key, "platform": platform,
                     "kind": kind, "source": source, "text": text, "name": name,
                     "allow_any_local": bool(allow_any_local)})
    return {"ok": True, "queued": True, "note": "已经安排发送，发完我会告诉对方"}


async def run_pending(send=None) -> int:
    """把排队的投递跑完（回合结束后调用），并把结果回话给发起人。"""
    global _running
    if _running or not _pending:
        return 0
    _running = True
    done = 0
    try:
        while _pending:
            job = _pending.pop(0)
            ctx = job.get("ctx")
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
            if job["kind"] == "text":
                result = await deliver_text(job["subject"], job["platform"], job["text"])
            else:
                result = await deliver_media(job["subject"], job["platform"],
                                             job["kind"], job["source"], name=job["name"],
                                             allow_any_local=bool(job.get("allow_any_local")))
            done += 1
            label = platform_name(job["platform"])
            # 2026-10-12：这条路以前不打日志，发没发出去只能靠"用户有没有收到"猜。
            try:
                from nonebot import logger as _lg

                if result.get("ok"):
                    _lg.info(f"[cross] 已投递到 {label}（{job['kind']}）"
                             f"｜subject={job['subject']}｜内容={str(job.get('text') or job.get('source'))[:60]}")
                else:
                    _lg.warning(f"[cross] 投递失败 → {label}｜error={result.get('error')}"
                                f"｜detail={str(result.get('detail') or result.get('hint') or '')[:120]}")
            except Exception:  # noqa: BLE001
                pass
            if result.get("ok"):
                await reply(f"已经发到你的{label}了。")
            else:
                reason = {
                    "not_bound": f"你还没绑定{label}：先发「绑定」拿码，"
                                 f"再去{label}上给我发「绑定 六位数字」。",
                    "platform_offline": f"{label}这个通道现在没在跑，先把它接上。",
                    "platform_cannot": f"{label}收不了这类内容（比如它只能发文本）。",
                    "source_unreadable": "这个文件我这边读不到（路径不对或下载不下来）。",
                    "too_large": "这个文件超过 20MB，发不了。",
                }.get(str(result.get("error")), f"没发出去（{result.get('error')}）")
                await reply("没发成功：" + reason)
    finally:
        _running = False
    return done


def status() -> dict:
    return {"pending": len(_pending), "running": _running}
