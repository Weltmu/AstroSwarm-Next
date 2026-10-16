# -*- coding: utf-8 -*-
"""微信 ClawBot 通道适配器（nonebot-adapter-ilink 事件 → 平台中立模型）。

2026-10-12：**图片和引用要从原始 payload 里自己捞**（老板实测：微信发图让它转 QQ，
它只会回"我没看到图"）。iLink 适配器只认文字（type 1）和语音转文字（type 3），
图片（type 2）一律丢 ✗ —— 但它的 `raw=data` 把**整包原始数据**留着，
所以这里自己读 `item_list` 把图捞出来（和 QQ 侧一样：给模型识别结果 + 原图地址）。
"""
from nonebot import logger

from .. import media_guard
from ..platform import (
    ChannelContext,
    ChannelMessage,
    PLATFORM_WECHAT,
    SCENE_GROUP,
    SCENE_PRIVATE,
)

_IMG_KEYS = ("image_url", "url", "file_url", "cdn_url")          # 优先原图
_THUMB_KEYS = ("thumb_url", "thumb", "preview_url")              # 实在没有才用缩略图
_QUOTE_KEYS = ("quote", "quoted", "refer", "refer_msg", "reply", "quote_msg",
               "refer_text", "quote_text")


def _find_url(obj, keys=_IMG_KEYS, depth: int = 0) -> str:
    """在一个 item 里尽力找图片地址（不同版本的字段名不一样）。"""
    if depth > 4 or not isinstance(obj, dict):
        return ""
    for k in keys:
        v = obj.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    for v in obj.values():
        if isinstance(v, dict):
            got = _find_url(v, keys, depth + 1)
            if got:
                return got
    return ""


def extract_media_and_quote(event) -> tuple:
    """从 iLink 原始 payload 里捞 (图片地址列表, 引用正文)。拿不到就返回空。"""
    data = getattr(event, "raw", None)
    if not isinstance(data, dict):
        return [], ""
    urls, quote = [], ""
    items = data.get("item_list") or []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") == 2:                     # 2 = 图片
                u = _find_url(item) or _find_url(item, _THUMB_KEYS)
                if u and u not in urls:                   # 同一张别重复记（原图+缩略图）
                    urls.append(u)
            for k in _QUOTE_KEYS:
                v = item.get(k) or data.get(k)
                if isinstance(v, str) and v.strip() and not quote:
                    quote = v.strip()[:300]
                elif isinstance(v, dict) and not quote:
                    for kk in ("text", "content", "title", "desc"):
                        if isinstance(v.get(kk), str) and v.get(kk).strip():
                            quote = v[kk].strip()[:300]
                            break
    return urls, quote


async def build_channel_from_wechat(chat_handler, bot, event):
    """iLink 事件 → ChannelMessage + ChannelContext（私聊个人助手，天然 to_me）。"""
    user_id = event.get_user_id()
    raw = getattr(event, "raw_message", "") or event.get_plaintext()

    # ---- 图片：识别 + 给地址（要转发的就是它）----
    try:
        urls, quote = extract_media_and_quote(event)
    except Exception as e:  # noqa: BLE001 —— 捞不到就照旧走纯文本，别影响回话
        logger.info(f"[wechat] 原始消息解析失败（按纯文本处理）：{e}")
        urls, quote = [], ""
    if quote:
        raw = f"[他引用的内容]{quote}\n{raw}"
        logger.info(f"[wechat] 引用内容：{quote[:60]}")
    if urls:
        try:
            from ..image2txt import recognize_image
            from ..manager import chat_manager as _cm

            seen = bool(_cm.is_image_recognition_enabled())
        except Exception:  # noqa: BLE001
            recognize_image, seen = None, False
        for i, u in enumerate(urls, 1):
            # 登记出处（见 media_guard）：iLink 给的图片地址往往是内网/本机口，
            # 不登记的话"微信图片转 QQ"会被内网拦截误伤。
            media_guard.trust_origin(u)
            desc = ""
            if seen and recognize_image is not None:
                try:
                    desc = await recognize_image(u)
                except Exception as e:  # noqa: BLE001
                    desc = f"（识别失败：{str(e)[:60]}）"
            raw += (f"\n[图片{i}的识别结果]{desc}"
                    f"\n[图片{i}·转发用] 原图地址：{u}"
                    f"\n（他要你把它发到别的平台时，直接调 "
                    f"send_file_to_my_platform(platform=目标, url=这个地址, kind=image)）")
        logger.info(f"[wechat] 收到 {len(urls)} 张图片，已带上地址")

    # 2026-10-15 修（两个真问题）：
    # ① iLink 事件**自带 group_id / message_type**（群消息是可能出现形态），以前无论
    #    如何都按私聊处理 → 群里每条消息都回（不看 @ 门禁）、还按发言成员各拆一份上下文；
    # ② `is_superuser` 恒 True → **群里任何人都被当成管理员**：
    #    media_guard 的"管理员可以发本机任意文件"、skill_forge 的触发门槛全被绕过。
    #    私聊（p2p）里对面就是扫码绑定的那个人，保持 True；群里一律不是管理员。
    group_id = str(getattr(event, "group_id", "") or "")
    is_group = bool(group_id) or str(getattr(event, "message_type", "") or "") == "group"
    scene = SCENE_GROUP if is_group else SCENE_PRIVATE

    msg = ChannelMessage(
        platform=PLATFORM_WECHAT,
        scene=scene,
        user_id=user_id,
        room_id=group_id or user_id,
        text=raw,
        is_at_me=not is_group,          # 群里要有 @ 才算（群消息不做无脑接话）
        nickname="微信用户",
        plain_text=raw,
        raw=event,
        extra={"context_token": getattr(event, "context_token", "")},
        is_superuser=not is_group,      # 见上：只有私聊那条通道是"扫码者=管理员"
    )

    async def send_text(t: str) -> bool:
        ok = await bot.send(event, t)
        logger.info(f"[wechat] 发送回复 {'成功' if ok else '失败'}: {t[:80]}")
        return bool(ok)

    async def send_text_at(t: str) -> bool:
        ok = await bot.send(event, t)
        logger.info(f"[wechat] 发送回复 {'成功' if ok else '失败'}: {t[:80]}")
        return bool(ok)

    async def notify_error(err: str) -> None:
        logger.error(f"[wechat] {err}")

    ctx = ChannelContext(
        platform=PLATFORM_WECHAT,
        scene=scene,
        send_text=send_text,
        send_text_at=send_text_at,
        notify_error=notify_error,
        # 微信 iLink 通道**没有查证过能不能连发** → 按老板口径"不确定就默认合并"，
        # 保守处理；哪天真测出能连发，改这一个字段即可（判定逻辑不用动）。
        multi_message=False,
        multi_message_note="微信 iLink：没查证过能否连发 → 保守合并成一条",
    )
    return msg, ctx
