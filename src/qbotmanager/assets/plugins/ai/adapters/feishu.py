# -*- coding: utf-8 -*-
"""飞书通道适配器（nonebot-adapter-feishu 事件 → 平台中立模型）。

对应路线图 G4：一次只做一个通道，附开发者后台配置指引。
凭证与指引在客户端「飞书」页；适配器不在安装包里，装好后重启生效。

设计取舍：
- 发送文本 / @ 人 / 图片（image_key）都能用；发本地图片要先上传拿 image_key，
  上传失败就**如实返回 False**（不假装成功）。
- 飞书的事件里用户 id 有 open_id / union_id / user_id 三种，这里统一用 open_id
  （与桌面端设置里让用户填的 `user:openid` 白名单写法一致）。
"""
from nonebot import logger

from ..group_policy import describe_segments, is_at_me, mentions_other
from ..image2txt import recognize_image
from ..manager import chat_manager
from ..platform import (
    ChannelContext,
    ChannelMessage,
    PLATFORM_FEISHU,
    SCENE_GROUP,
    SCENE_PRIVATE,
)
from ..send2root import send_error_to_super_users


def _event_text(event) -> str:
    """事件里的纯文本（飞书的正文在 message.content 的 JSON 里）。"""
    try:
        return str(event.get_message()).strip()
    except Exception:  # noqa: BLE001
        pass
    try:
        import json

        content = getattr(getattr(event, "event", None), "message", None)
        raw = getattr(content, "content", "") or ""
        data = json.loads(raw or "{}")
        return str(data.get("text") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _bot_ids_of(bot) -> list:
    """机器人自己的 id 候选（@ 判定要靠它）。

    2026-10-15 修：以前只读 `bot.self_info.id` —— 而 nonebot-adapter-feishu 的
    `BotInfo` 里**没有 `id`**，机器人自己的 open_id 在 `open_id` 字段上。
    结果 `bot_id` 恒为空串 → 群里 @ 机器人既不算"@ 我"、又因为
    `mentions_other` 把它当成"@ 别人" → 机器人**根本不接话**。
    这里把各版本可能放的字段、以及 `bot.self_id` 全收进来，取到一个就够。
    """
    out = []
    for holder in (getattr(bot, "self_info", None), getattr(bot, "bot_info", None)):
        for attr in ("open_id", "id", "user_id", "app_id", "union_id"):
            value = str(getattr(holder, attr, "") or "").strip()
            if value and value not in out:
                out.append(value)
    self_id = str(getattr(bot, "self_id", "") or "").strip()
    if self_id and self_id not in out:
        out.append(self_id)
    return out


def _chat_info(event) -> tuple:
    """飞书事件 → `(chat_id, 是不是群聊)`。

    ⚠ 飞书事件上**没有 `group_id` 属性** —— 以前 `getattr(event, "group_id", "")`
    恒为空串，于是**群里每一条消息都被当成私聊**：机器人不看 @ 门禁、对每条群消息
    都回，而且按发言成员各拆一份上下文（"@ 别人也抢话"的历史事故形态）。
    群/私聊的真正来源是 `event.event.message.chat_id` + `chat_type`：
      · 群会话 id 以 `oc_` 开头，私聊（p2p）用的是用户 open_id（`ou_`）。
    """
    inner = getattr(getattr(event, "event", None), "message", None)
    if inner is None:
        inner = getattr(event, "message", None)
    chat_id = ""
    for holder in (inner, event):
        for attr in ("chat_id", "chatId"):
            value = getattr(holder, attr, None)
            if value:
                chat_id = str(value)
                break
        if chat_id:
            break
    if not chat_id:
        # 桩事件/老版本可能直接给 group_id（单测里就是这么造的）
        chat_id = str(getattr(event, "group_id", "") or "")
    chat_type = ""
    for holder in (inner, event):
        value = getattr(holder, "chat_type", None) or getattr(holder, "chatType", None)
        if value:
            chat_type = str(value).lower()
            break
    if chat_type:
        return chat_id, chat_type == "group"
    if chat_id.startswith("oc_"):
        return chat_id, True
    return chat_id, bool(getattr(event, "group_id", ""))


async def build_channel_from_feishu(chat_handler, bot, event):
    """飞书事件 → ChannelMessage + ChannelContext。"""
    # 2026-10-15 修：群/私聊判定改看 chat_id（飞书事件上根本没有 group_id 属性，
    # 以前恒判成私聊 → 群里每条消息都回、上下文按人拆散）。详见 _chat_info。
    group_id, is_group_chat = _chat_info(event)
    scene = SCENE_GROUP if is_group_chat else SCENE_PRIVATE
    sender = getattr(event, "event", None)
    sender = getattr(sender, "sender", None)
    sender_id = getattr(sender, "sender_id", None)
    user_id = str(
        getattr(sender_id, "open_id", "")
        or getattr(event, "get_user_id", lambda: "")()
        or ""
    )
    room_id = group_id or user_id
    text = _event_text(event)

    # 图片识别：和 QQ/微信一致（开关在「AI 大脑」里）
    if chat_manager.is_image_recognition_enabled():
        try:
            message = event.get_message()
            keys = [seg.data.get("image_key") for seg in message
                    if getattr(seg, "type", "") == "image"]
            for i, key in enumerate([k for k in keys if k]):
                result = await recognize_image(key)
                text += f"\n[图片{i + 1}的识别结果]{result}"
        except Exception as e:  # noqa: BLE001
            logger.info(f"[feishu] 图片识别失败: {e}")

    # @ 判定（2026-10-11 晚，H-3）：**复用 group_policy**，别再内联 `any(type == "at")`。
    # 改前 `is_at = any(seg.type == "at" …)` = **任意 @ 都算 @ 我** →
    # 飞书群里 @ 别人也必被抢话（违反 I2）。
    # 现在：@ 我 → is_at_me；@ 别人（且未 @ 我）→ at_others → brain 直接不接话。
    bot_ids = _bot_ids_of(bot)
    is_at = is_at_me(event, bot_id=bot_ids)
    at_others = mentions_other(event, bot_id=bot_ids) if scene == SCENE_GROUP else False

    msg = ChannelMessage(
        platform=PLATFORM_FEISHU,
        scene=scene,
        user_id=user_id,
        room_id=room_id,
        text=text,
        is_at_me=is_at,
        nickname="",
        plain_text=text,
        raw=event,
        # 判定函数要读的信号：@ 别人（不抢话）。以前 extra={} ——
        # brain 那边 `extra.get("at_others")` 恒为假，等于没判定。
        # `quoted` 老实留 None：本适配器**没有**把引用段解析出来，绝不编一个假的
        # （引用语义见规则表 7/8/9 行，属下一轮）。
        extra={"at_others": at_others,
               "quoted": None,
               "segs": describe_segments(event)},
    )

    async def send_text(t: str) -> None:
        await bot.send(event, t)

    async def send_text_at(t: str) -> None:
        from nonebot.adapters.feishu import Message, MessageSegment

        await bot.send(event, Message([MessageSegment.at(user_id), MessageSegment.text(" " + t)]))

    async def notify_error(err: str) -> None:
        try:
            await send_error_to_super_users(err, event)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[feishu] 上报错误失败: {e}")

    async def send_image(target: str, caption: str = "") -> bool:
        """发图片：先上传拿 image_key 再发；传不动就如实 False。

        2026-10-15 修：上限以前是"读一次 8MB 就完事" —— 响应体大于 8MB 时
        只拿到前 8MB，**静默**上传一张被截断的坏图；本地文件则完全不限大小
        （read_bytes 整读）。现在两边都先判上限，超了就说超了（不假装发了）。
        """
        try:
            from pathlib import Path

            from nonebot.adapters.feishu import Message, MessageSegment

            limit = 8 * 1024 * 1024
            data = None
            if str(target).lower().startswith(("http://", "https://")):
                import urllib.request

                with urllib.request.urlopen(target, timeout=20) as resp:  # noqa: S310
                    data = resp.read(limit + 1)
                if len(data) > limit:
                    logger.warning("[feishu] 图片超过 8MB 上限，已跳过（没有截断上传）")
                    return False
            else:
                path = Path(str(target))
                if not path.is_file():
                    return False
                try:
                    if path.stat().st_size > limit:
                        logger.warning("[feishu] 本地图片超过 8MB 上限，已跳过")
                        return False
                except OSError:
                    return False
                data = path.read_bytes()
            upload = getattr(bot, "upload_image", None)
            if upload is None:
                logger.warning("[feishu] 这个版本的适配器没有 upload_image，发图片跳过")
                return False
            image_key = await upload(image_type="message", image=data)
            await bot.send(event, Message([MessageSegment.image(image_key)]))
            if caption:
                await bot.send(event, caption)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[feishu] 发图片失败：{e}")
            return False

    ctx = ChannelContext(
        platform=PLATFORM_FEISHU,
        scene=scene,
        send_text=send_text,
        send_text_at=send_text_at,
        notify_error=notify_error,
        send_image=send_image,
        # 飞书要 file_key / 语音要先上传，暂不提供；prompt 里会告诉 AI"这个通道发不了"
        send_file=None,
        send_voice=None,
        send_card=None,
        # 分条：飞书本身能一条条发，但老板口径是"受限通道默认合并、不确定就别分"，
        # 这条通道还没在真机上验过连发节奏 → 保守合并（要开就是配置里切「总是」）。
        multi_message=False,
        multi_message_note="飞书：未在真机验过连发 → 保守合并成一条",
    )
    return msg, ctx
